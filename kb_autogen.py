#!/usr/bin/env python3
"""Generate narrated videos for new or changed OntoKB items."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import html
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import URLError
from urllib.parse import quote, urlsplit

import kb_images
import kb_scenes
import kb_source
import kb_video


STATE_VERSION = 2
MAX_ATTEMPTS = 3
RETRY_DELAYS = (0.1, 0.2)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def run_lock(out: Path):
    """Hold an exclusive lock for the process lifetime of an autogen run."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / '.autogen.lock'
    fd = None
    with (out / '.autogen.guard').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                fd = None
                raise RuntimeError('autogen locked, skipped')
            os.ftruncate(fd, 0)
            os.write(fd, f'{os.getpid()}\n'.encode())
            os.fsync(fd)
        finally:
            fcntl.flock(guard, fcntl.LOCK_UN)
    assert fd is not None
    identity = os.fstat(fd)
    try:
        yield
    finally:
        with (out / '.autogen.guard').open('a') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                if path.stat().st_ino == identity.st_ino:
                    path.unlink()
            except FileNotFoundError:
                pass
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                fcntl.flock(guard, fcntl.LOCK_UN)


def content_hash(item: dict, options: dict) -> str:
    content = {key: item.get(key) for key in ('title', 'summary', 'key_points')}
    content['render_options'] = options
    # Categories appear on title slides, so they also invalidate rendered content.
    content['categories'] = item.get('categories', [])
    content['url'] = item.get('url', '')
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def valid_output(out: Path, relative: str) -> bool:
    """Return true only for a completed video bundle, not a partial mp4."""
    try:
        video = out / relative
        data = json.loads((video.parent / 'manifest.json').read_text(encoding='utf-8'))
        declared = data.get('files', {}).get('final')
        return (video.is_file() and video.stat().st_size > 0
                and (declared is None or declared == video.name))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def retry(operation):
    """Retry transient local/network failures with a small bounded backoff."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return operation()
        except (OSError, URLError, TimeoutError, RuntimeError):
            if attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])


def scene_manifest_ready(path: Path) -> bool:
    """Only reuse a scene manifest when every requested image completed."""
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        scenes = manifest['scenes']
        if not scenes:
            return False
        for scene in scenes:
            image = scene.get('image')
            if (not isinstance(image, dict) or image.get('blocked_reason')
                    or not image.get('path') or not image.get('sha256')):
                return False
            data = Path(image['path']).read_bytes()
            if (not data.startswith(b'\x89PNG\r\n\x1a\n')
                    or hashlib.sha256(data).hexdigest() != image['sha256']):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def render_scenes(plan: dict, scene_path: Path, out: Path, image_config) -> dict:
    """Render scenes, converting backend fallback results into retryable errors."""
    manifest = kb_scenes.render_scene_plan(
        plan, scene_path.parent, cache_dir=out / '.image_cache', config=image_config)
    blocked = [scene.get('image', {}).get('blocked_reason')
               for scene in manifest.get('scenes', [])
               if scene.get('image', {}).get('blocked_reason')]
    if blocked:
        raise RuntimeError('image generation blocked: ' + '; '.join(map(str, blocked)))
    if not scene_manifest_ready(scene_path):
        raise RuntimeError('image generation produced an incomplete scene manifest')
    return manifest


def save_state(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')


def write_gallery(out: Path, state: dict) -> None:
    rows = []
    esc = lambda value: html.escape(str(value), quote=True)
    for record in state.values():
        formats = []
        preview = ''
        manifest = None
        for output in record.get('outputs', []):
            video = out / output
            if not video.is_file():
                continue
            current = json.loads((video.parent / 'manifest.json').read_text(encoding='utf-8'))
            manifest = manifest or current
            href = esc(quote(output))
            formats.append(f'<a href="{href}">MP4 ({esc(video.parent.name)})</a>')
            # Standard landscape names and custom WxH dimensions both work.
            name = video.parent.name
            landscape = name == 'landscape'
            if 'x' in name:
                dimensions = name.split('x')
                landscape = len(dimensions) == 2 and all(d.isdigit() for d in dimensions) and int(dimensions[0]) >= int(dimensions[1])
            if landscape and not preview:
                preview = f'<video controls preload="metadata" width="640" style="max-width:100%" src="{href}"></video>'
        if manifest:
            script = manifest['script']
            url = str(script.get('url', ''))
            source = (f'<a href="{esc(url)}">Source</a>'
                      if urlsplit(url).scheme in ('http', 'https') else esc(url))
            rows.append(f'<li><h2>{esc(script.get("title", "Untitled"))}</h2>'
                        f'<p>{float(manifest["total_duration"]):.1f} s · '
                        + ' · '.join(formats) + f' · {source}</p>{preview}</li>')
    atomic_write(out / 'index.html', '<!doctype html><html lang="en"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width"><title>OntoKB videos</title>'
                 '<body><h1>OntoKB videos</h1><ul>' + ''.join(rows) + '</ul></body></html>\n')


def csv_values(value: str, allowed=None) -> list[str]:
    values = sorted(set(part.strip() for part in value.split(',') if part.strip()))
    if not values or (allowed and not set(values) <= set(allowed)):
        raise argparse.ArgumentTypeError('Invalid comma-separated values')
    return values


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-json', type=Path)
    parser.add_argument('--base-url', default=kb_source.DEFAULT_BASE_URL)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--out', type=Path, default=Path('kb_out'))
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--kinds', type=csv_values)
    parser.add_argument('--formats', type=lambda s: csv_values(s, ('landscape', 'vertical')),
                        default=['landscape', 'vertical'])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--strict', action='store_true')
    parser.add_argument('--force', metavar='ID')
    parser.add_argument('--size', default='1920x1080', help='Landscape WxH; vertical swaps dimensions')
    parser.add_argument('--tts', choices=['auto', 'edge', 'say'], default='auto')
    parser.add_argument('--voice')
    parser.add_argument('--lang', choices=['zh', 'ja', 'en'], default='zh')
    parser.add_argument('--max-points', type=int, default=6)
    parser.add_argument('--no-burn', action='store_true')
    args = parser.parse_args(argv)
    counts = dict(generated=0, skipped=0, failed=0)
    code = 0
    try:
        size = tuple(map(int, args.size.lower().split('x')))
        if len(size) != 2 or any(n < 32 or n % 2 for n in size) or args.limit < 0 or args.max_points < 0:
            raise ValueError('Use even --size WxH >=32 and nonnegative limits')
        image_config = kb_images.Config.from_env()
        options = dict(state_version=STATE_VERSION, layout_version=3, scene_schema=1,
                       image=dict(provider=image_config.provider, model=image_config.model,
                                  style=kb_scenes.STYLE, size=[512, 512]),
                       tts=args.tts, formats=sorted(args.formats), size=size, voice=args.voice, lang=args.lang,
                       max_points=args.max_points, burn=not args.no_burn,
                       font=kb_video.font_path(), fake_tts=os.environ.get('NPOCUT_FAKE_TTS') == '1')
        try:
            library = (kb_source.load_library(args.from_json) if args.from_json else
                       kb_source.fetch_library(args.base_url, args.timeout))
        except (OSError, URLError, TimeoutError) as exc:
            if args.from_json:
                raise
            print(f'ontokb offline, skipped: {exc}')
            return 1 if args.strict else 0
        with run_lock(args.out):
            state_path = args.out / 'autogen_state.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            # Reserve slugs for all known IDs, including records absent from this snapshot.
            slugs = {}
            used = set()
            ids = list(state) + [str(item.get('id', '')) for item in library]
            for identity in ids:
                if identity in slugs:
                    continue
                base = kb_source.slugify(identity)
                candidate = base
                if candidate in used:
                    candidate = base + '-' + hashlib.sha256(identity.encode()).hexdigest()[:8]
                while candidate in used:
                    candidate += '-x'
                slugs[identity] = candidate
                used.add(candidate)
            selected = 0
            seen = set()
            for item in library:
                item_id = str(item.get('id', ''))
                if not item_id or item_id in seen:
                    counts['skipped'] += 1
                    continue
                seen.add(item_id)
                digest = content_hash(item, options)
                old = state.get(item_id, {})
                changed = old.get('hash') != digest
                forced = args.force == item_id
                missing = old.get('status') == 'done' and (not old.get('outputs') or any(
                    not valid_output(args.out, output) for output in old.get('outputs', [])))
                eligible = (changed or missing or (old.get('status') in ('failed', 'running')
                                                    and old.get('attempts', 0) < MAX_ATTEMPTS)
                            or forced)
                if (not eligible or selected >= args.limit or
                        (args.kinds and item.get('kind') not in args.kinds)):
                    counts['skipped'] += 1
                    continue
                selected += 1
                print(f'{"plan" if args.dry_run else "generate"}: {item_id} {item.get("title", "")} [{",".join(args.formats)}]')
                if args.dry_run:
                    counts['skipped'] += 1
                    continue
                # --force preserves its original CLI meaning: rerender the
                # selected item even when its content and configuration match.
                reusable = old.get('hash') == digest and not forced
                record = dict(hash=digest, config=options, status='running',
                              outputs=list(old.get('outputs', [])) if reusable else [], error=None,
                              completed_formats=dict(old.get('completed_formats', {})) if reusable else {},
                              updated_at=datetime.now(timezone.utc).isoformat(),
                              attempts=1 if changed else old.get('attempts', 0) + 1)
                state[item_id] = record
                save_state(state_path, state)
                try:
                    script = kb_source.build_script(item, args.max_points, args.lang)
                    script['categories'] = item.get('categories', [])
                    item_out = args.out
                    scene_relative = f'{slugs[item_id]}/scenes/scene-manifest.json'
                    scene_path = args.out / scene_relative
                    scene_ok = (reusable and old.get('scene_manifest') == scene_relative
                                and scene_manifest_ready(scene_path))
                    if not scene_ok:
                        try:
                            plan = kb_scenes.build_scene_plan(item, script)
                        except ValueError:
                            # Sparse legacy OntoKB records can still produce the
                            # original text-only video even when no valid visual
                            # storyboard can be derived from their source fields.
                            scene_path = None
                        else:
                            retry(lambda: render_scenes(
                                plan, scene_path, args.out, image_config))
                    record['scene_manifest'] = scene_relative if scene_path else None
                    record['phase'] = 'images_done' if scene_path else 'images_skipped'
                    record['updated_at'] = datetime.now(timezone.utc).isoformat()
                    save_state(state_path, state)
                    outputs = [output for output in record['outputs'] if valid_output(args.out, output)]
                    completed = {fmt: output for fmt, output in record['completed_formats'].items()
                                 if output in outputs}
                    for fmt in args.formats:
                        dimensions = ((max(size), min(size)) if fmt == 'landscape' else
                                      (min(size), max(size)))
                        if fmt in completed:
                            continue
                        final = retry(lambda: kb_video.render_script(
                            script, out=item_out, size=dimensions,
                            voice=args.voice, lang=args.lang, tts=args.tts, burn=not args.no_burn,
                            output_slug=slugs[item_id], scene_manifest=scene_path))
                        outputs.append(str(final.resolve().relative_to(args.out.resolve())))
                        completed[fmt] = outputs[-1]
                        record.update(outputs=outputs, completed_formats=completed,
                                      phase=f'video_{fmt}_done',
                                      updated_at=datetime.now(timezone.utc).isoformat())
                        save_state(state_path, state)
                    record.update(status='done', outputs=outputs, phase='done', error=None,
                                  updated_at=datetime.now(timezone.utc).isoformat())
                    counts['generated'] += 1
                except Exception as exc:
                    record['status'] = 'failed'
                    record['error'] = str(exc)
                    counts['failed'] += 1
                    print(f'failed: {item_id}: {exc}')
                state[item_id] = record
                save_state(state_path, state)
            if not args.dry_run:
                write_gallery(args.out, state)
    except RuntimeError as exc:
        print(str(exc))
    except (OSError, ValueError) as exc:
        print(f'kb_autogen: {exc}')
        code = 1
    finally:
        print(json.dumps(counts))
    return code or (1 if counts['failed'] else 0)


if __name__ == '__main__':
    raise SystemExit(main())
