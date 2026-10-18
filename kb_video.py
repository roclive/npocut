#!/usr/bin/env python3
"""Render an OntoKB VideoScript as narrated slides with burned subtitles."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import logging
import time
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from common import FFMPEG, Cue, concat_file_line, probe_duration, write_srt
from kb_source import DEFAULT_BASE_URL, build_script, fetch_library, load_library, slugify
from kb_templates import build_template_script, split_segment, template_names

PAD = 0.4
SAY_DEFAULT_VOICE = 'Tingting'
PHASE_PREFIX = 'NPOCUT_PHASE:'
VISUAL_PALETTES = {
    'editorial': ('#101e31', '#53d7bd', '#ffffff', '#d4e2ef', '#8fa7bf'),
    'paper': ('#eee9df', '#b85c38', '#28231f', '#504941', '#746b62'),
    'contrast': ('#090909', '#f4d35e', '#ffffff', '#e6e6e6', '#a8a8a8'),
}


def report_phase(phase: str) -> None:
    """Emit a machine-readable progress event for web and CLI consumers."""
    print(f'{PHASE_PREFIX}{phase}', file=sys.stderr, flush=True)


def ffmpeg(*args: object, cwd: Path | None = None) -> None:
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
                    *map(str, args)], cwd=cwd, check=True)


def choose_voice(lang: str, override: str | None = None) -> str:
    if override:
        return override
    if os.environ.get('NPOCUT_FAKE_TTS') == '1':
        return 'fake'
    listing = subprocess.run(['say', '-v', '?'], capture_output=True, text=True, check=True).stdout
    locale = {'zh': 'zh_CN', 'ja': 'ja_JP', 'en': 'en_US'}[lang]
    voices = []
    for line in listing.splitlines():
        match = re.match(r'(.+?)\s+([a-z]{2}_[A-Z]{2})\s', line)
        if match and match[2] == locale:
            voices.append(match[1].strip())
    if not voices:
        raise ValueError(f'No installed {locale} voice; install one or pass --voice')
    return SAY_DEFAULT_VOICE if lang == 'zh' and SAY_DEFAULT_VOICE in voices else voices[0]


def speech_weight(text: str) -> float:
    return sum(0.18 if ord(c) >= 0x2E80 else 0.06 for c in text if not c.isspace())


def synthesize_segment(text: str, voice: str, target: Path) -> float:
    """Synthesize PCM audio and append silence; return measured padded duration."""
    if os.environ.get('NPOCUT_FAKE_TTS') == '1' or not text.strip():
        ffmpeg('-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono',
               '-t', max(1.5, speech_weight(text)), target.with_suffix('.raw.wav'))
        source = target.with_suffix('.raw.wav')
    else:
        text_path = target.with_suffix('.txt')
        text_path.write_text(text, encoding='utf-8')
        source = target.with_suffix('.raw.wav')
        subprocess.run(['say', '-v', voice, '--file-format=WAVE', '-o', str(source),
                        '--data-format=LEI16@24000', '-f', str(text_path)], check=True)
    ffmpeg('-i', source, '-af', f'apad=pad_dur={PAD}', '-ar', 24000,
           '-ac', 1, '-c:a', 'pcm_s16le', target)
    return probe_duration(target)


EDGE_VOICES = {'zh': 'zh-CN-YunyangNeural', 'ja': 'ja-JP-KeitaNeural',
               'en': 'en-US-GuyNeural'}


async def edge_stream(text: str, voice: str, target: Path) -> list[dict]:
    import edge_tts
    boundaries = []
    with target.open('wb') as audio:
        async for event in edge_tts.Communicate(
                text, voice, boundary="SentenceBoundary").stream():
            if event['type'] == 'audio':
                audio.write(event['data'])
            elif event['type'] == 'SentenceBoundary':
                boundaries.append(dict(text=event['text'], start=event['offset'] / 1e7,
                                       duration=event['duration'] / 1e7))
    return boundaries


def cached_speech(text: str, voice: str, backend: str, target: Path,
                  cache: Path) -> tuple[float, list[dict]]:
    if backend == 'fake':
        return synthesize_segment(text, voice, target), []
    key = hashlib.sha256(json.dumps([backend, voice, text], ensure_ascii=False).encode()).hexdigest()
    wav, metadata = cache / (key + '.wav'), cache / (key + '.json')
    if wav.is_file() and metadata.is_file():
        boundaries = json.loads(metadata.read_text(encoding='utf-8'))
        shutil.copyfile(wav, target)
        return probe_duration(target), boundaries
    boundaries = []
    if backend == 'edge' and text.strip():
        mp3 = target.with_suffix('.mp3')
        for attempt in range(3):
            try:
                boundaries = asyncio.run(asyncio.wait_for(edge_stream(text, voice, mp3), 60))
                break
            except ImportError:
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        ffmpeg('-i', mp3, '-af', f'apad=pad_dur={PAD}', '-ar', 24000,
               '-ac', 1, '-c:a', 'pcm_s16le', target)
        duration = probe_duration(target)
    else:
        duration = synthesize_segment(text, voice, target)
    cache.mkdir(parents=True, exist_ok=True)
    # Publish complete files atomically, including during simultaneous renders.
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        staged = Path(directory)
        shutil.copyfile(target, staged / 'audio.wav')
        (staged / 'boundary.json').write_text(json.dumps(boundaries, ensure_ascii=False), encoding='utf-8')
        os.replace(staged / 'audio.wav', wav)
        os.replace(staged / 'boundary.json', metadata)
    return duration, boundaries


def subtitle_chunks(sentence: str, size: tuple[int, int]) -> list[str]:
    font = ImageFont.truetype(font_path(), subtitle_font_size(size))
    width = size[0] * .84
    def fits(text):
        if font.getlength(text) > width * 2:
            return False
        return len(wrap_text(text, font, width)) <= 2
    def split(text):
        if fits(text):
            return [text]
        candidates = [i for i in range(6, len(text)-5)
                      if text[i-1] in '，、；：,;:']
        if not candidates:
            candidates = list(range(6, len(text)-5))
        if not candidates:
            return [text]
        middle = min(candidates, key=lambda i: abs(speech_weight(text[:i]) - speech_weight(text[i:])))
        return split(text[:middle]) + split(text[middle:])
    pieces = split(sentence)
    return ['\n'.join(wrap_text(piece, font, width)) for piece in pieces]


def font_path() -> str:
    candidates = [os.environ.get('NPOCUT_FONT'),
                  '/System/Library/Fonts/Hiragino Sans GB.ttc',
                  '/System/Library/Fonts/STHeiti Medium.ttc',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError('No suitable font found; set NPOCUT_FONT')


def wrap_text(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Wrap by measured glyph width, including CJK and unbroken URLs."""
    if width <= 0:
        raise ValueError('Text width must be positive')
    closing = set('，。、；：！？）」』】》…,.!?;:)]}')
    opening = set('（「『【《([{')
    lines = []
    for paragraph in text.split('\n'):
        remaining = paragraph.strip()
        while remaining:
            end = 0
            while end < len(remaining) and font.getlength(remaining[:end+1]) <= width:
                end += 1
            if end == 0:
                raise ValueError('A glyph exceeds the available text width')
            if end < len(remaining):
                # Backtrack the boundary so punctuation stays with its neighbour,
                # without allowing hanging punctuation to exceed the pixel limit.
                while end and (remaining[end] in closing or remaining[end-1] in opening):
                    end -= 1
                if not end:
                    raise ValueError('Punctuation group exceeds the available text width')
            lines.append(remaining[:end].rstrip())
            remaining = remaining[end:].lstrip()
        if not paragraph.strip():
            lines.append('')
    return lines


def fit_text(text: str, width: int, height: int, initial: int):
    for size in range(max(1, initial), 0, -1):
        font = ImageFont.truetype(font_path(), size)
        try:
            lines = wrap_text(text, font, width)
        except ValueError:
            continue
        spacing = math.ceil(size * 1.45)
        if len(lines) * spacing <= height:
            return font, lines, spacing
    raise ValueError('Slide text cannot fit; shorten the script or increase --size')


def draw_slide(script: dict, segment: dict, index: int, size: tuple[int, int], target: Path,
               artwork: Path | None = None, style: str = 'editorial') -> None:
    width, height = size
    if style not in VISUAL_PALETTES:
        raise ValueError('Unknown visual style')
    background, accent, heading_color, body_color, footer_color = VISUAL_PALETTES[style]
    template = script.get('template') or {}
    colors = template.get('colors') or {}
    background_color = colors.get('background', background)
    veil_color = colors.get('veil', '#081321')
    font_scale = float(template.get('font_scale', 1))
    if artwork is None:
        image = Image.new('RGB', size, background_color)
    else:
        with Image.open(artwork) as source:
            image = ImageOps.fit(source.convert('RGB'), size, method=Image.Resampling.LANCZOS)
        # A restrained veil keeps headings readable without hiding the illustration.
        image = Image.blend(image, Image.new('RGB', size, veil_color), .48)
    draw = ImageDraw.Draw(image)
    margin = max(8, int(width * .075))
    usable = width - margin * 2
    short = min(size)
    draw.rounded_rectangle((margin, int(height * .09), width-margin, int(height * .105)),
                           radius=2, fill=colors.get('accent', accent))
    title = segment.get('kind') == 'title'
    heading = str(segment.get('heading', ''))
    body = str(segment.get('text', ''))
    if title:
        categories = script.get('categories', script.get('category', ''))
        body = ' / '.join(map(str, categories)) if isinstance(categories, list) else str(categories)
    if segment.get('kind') == 'outro':
        body = str(script.get('url', body))
    # The bottom 18% belongs exclusively to subtitles. The footer sits above it.
    top, bottom = int(height * .15), int(height * .73)
    gap = max(2, int(short * .035)) if body else 0
    available = bottom - top
    heading_height = int(available * (.65 if title else .42))
    hf, headings, hs = fit_text(heading, usable, heading_height,
                                max(1, int(short * (.095 if title else .075) * font_scale)))
    body_height = available - len(headings) * hs - gap
    bf, bodies, bs = fit_text(body, usable, max(1, body_height), max(1, int(short * .055 * font_scale)))
    block_height = len(headings) * hs + gap + len(bodies) * bs
    y = top + (available - block_height) / 2
    for lines, font, spacing, color in [(headings, hf, hs, colors.get('heading', heading_color)),
                                        (bodies, bf, bs, colors.get('body', body_color))]:
        for line in lines:
            draw.text((margin, y), line, font=font, fill=color)
            y += spacing
        if lines is headings:
            y += gap
    footer = f"{script.get('title', '')}  ·  {index+1}/{len(script['segments'])}"
    font, lines, spacing = fit_text(footer, usable, max(2, int(height*.05)),
                                    max(1, int(short*.020)))
    y = height * .76
    for line in lines:
        draw.text((margin, y), line, font=font, fill=colors.get('footer', footer_color))
        y += spacing
    image.save(target)


def load_scene_images(scene_manifest: str | Path, script: dict) -> list[Path | None]:
    """Validate a kb_scenes manifest and resolve usable images.

    A missing/blocked image degrades independently to the normal background. Relative
    paths are resolved beside the manifest so bundles remain movable.
    """
    path = Path(scene_manifest).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    scenes = data.get('scenes')
    if not isinstance(scenes, list) or len(scenes) != len(script['segments']):
        raise ValueError('Scene manifest must contain one scene per script segment')
    if data.get('source_id') and str(data['source_id']) != str(script.get('id', '')):
        raise ValueError('Scene manifest source_id does not match VideoScript')
    resolved = []
    for index, scene in enumerate(scenes):
        narration = scene.get('narration', {}) if isinstance(scene, dict) else {}
        if (not isinstance(scene, dict) or narration.get('segment_index', index) != index
                or narration.get('text', script['segments'][index]['narration'])
                != script['segments'][index]['narration']):
            raise ValueError('Scene manifest segment order does not match VideoScript')
        value = scene.get('image', {}).get('path')
        image = Path(value).expanduser() if value else None
        candidates = [image] if image is not None else []
        if image is not None and not image.is_absolute():
            # New/movable manifests store paths relative to the manifest. Older
            # kb_scenes manifests produced from a relative --out value contain a
            # cwd-relative path which already includes that output directory.
            candidates = [path.parent / image, image]
        usable = next((candidate for candidate in candidates if candidate.is_file()), None)
        resolved.append(usable.resolve() if usable is not None else None)
    return resolved


def draw_visual_layers(script: dict, segment: dict, index: int, size: tuple[int, int],
                       artwork: Path | None, background: Path, overlay: Path,
                       style: str = 'editorial') -> None:
    """Make a movable image layer and a fixed text/progress layer."""
    if style not in VISUAL_PALETTES:
        raise ValueError('Unknown visual style')
    palette_background, palette_accent, *_ = VISUAL_PALETTES[style]
    colors = (script.get('template') or {}).get('colors', {})
    canvas_color = colors.get('background', palette_background)
    if artwork is None:
        visual = Image.new('RGB', size, canvas_color)
    else:
        with Image.open(artwork) as source:
            visual = ImageOps.fit(source.convert('RGB'), size, method=Image.Resampling.LANCZOS)
        veil = (script.get('template') or {}).get('colors', {}).get('veil', '#081321')
        visual = Image.blend(visual, Image.new('RGB', size, veil), .32)
    visual.save(background)
    # Reuse the proven typography, then make its plain background transparent.
    flat = overlay.with_name(overlay.stem + '-flat.png')
    draw_slide(script, segment, index, size, flat, style=style)
    with Image.open(flat) as source:
        rgba = source.convert('RGBA')
    # Remove the exact flat canvas rather than relying on luminance: the paper
    # palette is deliberately bright and must not obscure scene artwork.
    canvas = Image.new('RGB', size, canvas_color)
    difference = ImageOps.grayscale(ImageChops.difference(rgba.convert('RGB'), canvas))
    rgba.putalpha(difference.point(lambda value: 0 if value == 0 else 255))
    draw = ImageDraw.Draw(rgba)
    width, height = size
    margin = max(8, int(width * .075))
    progress = (index + 1) / len(script['segments'])
    # Keep the lower 18% clear for libass subtitles.
    y = int(height * .79)
    draw.rounded_rectangle((margin, y, width-margin, y + max(2, int(height*.008))),
                           radius=2, fill=(255, 255, 255, 55))
    accent = colors.get('accent', palette_accent)
    draw.rounded_rectangle((margin, y, margin + int((width-2*margin)*progress),
                            y + max(2, int(height*.008))), radius=2, fill=accent)
    rgba.save(overlay)
    flat.unlink()


def assemble(slides: list[Path], audio: list[Path], durations: list[float], tmp: Path,
             visual_layers: list[tuple[Path, Path] | None] | None = None,
             motion: bool = True) -> Path:
    """Encode frame-aligned stills and concatenate PCM audio before final AAC encoding."""
    video_list = tmp / 'video.txt'
    audio_list = tmp / 'audio.txt'
    clips = []
    # Frame rounding is absorbed into the following slide, avoiding cumulative drift.
    frames_so_far = 0
    elapsed = 0.0
    for i, (slide, duration) in enumerate(zip(slides, durations)):
        elapsed += duration
        frames = round(elapsed * 30) - frames_so_far
        frames_so_far += frames
        clip = tmp / f'clip-{i}.mp4'
        seconds = frames / 30
        layers = visual_layers[i] if visual_layers else None
        if layers and motion:
            background, overlay = layers
            fade = min(.25, seconds / 5)
            direction = (f'on/{max(1, frames-1)}' if i % 2 == 0
                         else f'1-on/{max(1, frames-1)}')
            # Four percent is enough to feel alive while avoiding seasick motion.
            vf = (f"[0:v]zoompan=z='1+0.04*on/{max(1, frames-1)}':"
                  f"x='(iw-iw/zoom)*({direction})':y='(ih-ih/zoom)/2':"
                  f"d={frames}:s={slide_image_size(slide)}:fps=30,"
                  f"fade=t=in:st=0:d={fade},fade=t=out:st={max(0, seconds-fade)}:d={fade}[bg];"
                  "[1:v]format=rgba[fg];[bg][fg]overlay=0:0:shortest=1,format=yuv420p")
            ffmpeg('-loop', 1, '-framerate', 30, '-i', background,
                   '-loop', 1, '-framerate', 30, '-i', overlay, '-t', seconds,
                   '-filter_complex', vf, '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                   '-pix_fmt', 'yuv420p', '-r', 30, clip)
        elif layers:
            background, overlay = layers
            ffmpeg('-loop', 1, '-framerate', 30, '-i', background,
                   '-loop', 1, '-framerate', 30, '-i', overlay, '-t', seconds,
                   '-filter_complex', '[0:v][1:v]overlay=0:0:shortest=1,format=yuv420p',
                   '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'stillimage',
                   '-pix_fmt', 'yuv420p', '-r', 30, clip)
        else:
            ffmpeg('-loop', 1, '-framerate', 30, '-i', slide, '-t', seconds,
                   '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'stillimage',
                   '-pix_fmt', 'yuv420p', '-r', 30, clip)
        clips.append(clip)
    video_list.write_text(''.join(concat_file_line(p) for p in clips))
    audio_list.write_text(''.join(concat_file_line(p) for p in audio))
    output = tmp / 'video.mp4'
    ffmpeg('-f', 'concat', '-safe', 0, '-i', video_list,
           '-f', 'concat', '-safe', 0, '-i', audio_list,
           '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
           '-movflags', '+faststart', output)
    return output


def slide_image_size(path: Path) -> str:
    with Image.open(path) as image:
        return f'{image.width}x{image.height}'


def subtitle_font_size(size: tuple[int, int]) -> int:
    """Subtitle pixel size based on the short side and orientation."""
    return max(1, round(min(size) * (.045 if size[1] > size[0] else .035)))


def make_subtitles(script: dict, durations: list[float], target: Path,
                   size: tuple[int, int] | None = None,
                   boundaries: list[list[dict]] | None = None) -> None:
    cues = []
    offset = 0.0
    for index, (segment, duration) in enumerate(zip(script['segments'], durations)):
        events = boundaries[index] if boundaries else []
        if not events:
            sentences = [s.strip() for s in re.findall(r'[^。！？.!?\n]+[。！？.!?]*|[。！？.!?]+',
                                                       segment['narration']) if s.strip()]
            total = sum(speech_weight(s) for s in sentences) or 1
            cursor = 0.0
            events = []
            for sentence in sentences:
                length = (duration-PAD) * speech_weight(sentence) / total
                events.append(dict(text=sentence, start=cursor, duration=length))
                cursor += length
        for event in events:
            chunks = subtitle_chunks(event['text'], size) if size else [event['text']]
            total = sum(speech_weight(c) for c in chunks) or 1
            cursor = offset + event['start']
            available = min(event['duration'], max(0, duration-PAD-event['start']))
            for chunk in chunks:
                end = cursor + available * speech_weight(chunk) / total
                if end > cursor:
                    cues.append(Cue(cursor, end, tuple(chunk.split('\n'))))
                cursor = end
        offset += duration
    write_srt(cues, target)


def burn_subtitles(video: Path, target: Path, size: tuple[int, int], tmp: Path) -> None:
    # Fixed relative subtitle/font paths avoid libass's path escaping pitfalls.
    fonts = tmp / 'fonts'
    fonts.mkdir()
    font_file = Path(font_path())
    shutil.copyfile(font_file, fonts / font_file.name)
    family = ImageFont.truetype(str(font_file), 20).getname()[0]
    # libass's default PlayResY is 288: express styles in that coordinate space.
    font_size = max(1, round(288 * subtitle_font_size(size) / size[1]))
    style = (f'FontName={family},FontSize={font_size},Alignment=2,'
             'MarginV=12,MarginL=18,MarginR=18,WrapStyle=2,Outline=1,Shadow=0')
    style = style.replace(',', r'\,')
    ffmpeg('-i', video, '-vf', f'subtitles=subs.srt:fontsdir=fonts:force_style={style}',
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'copy', '-movflags', '+faststart', target, cwd=tmp)



def write_manifest(script: dict, durations: list[float], voice: str,
                   files: dict, final: Path, target: Path, tts_backend="say", fallback_reason=None,
                   visuals: dict | None = None) -> None:
    manifest = dict(script=script, durations=durations, voice=voice, files=files,
                    tts_backend=tts_backend, fallback_reason=fallback_reason,
                    visuals=visuals or {'mode': 'legacy', 'scene_count': 0},
                    total_duration=probe_duration(final),
                    created_at=datetime.now(timezone.utc).isoformat())
    manifest['template'] = script.get('template', {'id': 'legacy'})
    manifest['aspect_ratio'] = (visuals or {}).get('aspect_ratio', '16:9')
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def render_script(script: dict, *, out: Path = Path('kb_out'), size=(1920, 1080),
                  voice: str | None = None, lang: str | None = None, burn: bool = True,
                  custom_size: bool = False, output_slug: str | None = None, tts: str = "auto",
                  scene_manifest: str | Path | None = None, motion: bool = True,
                  style: str = 'editorial') -> Path:
    script = copy.deepcopy(script)
    if not isinstance(script, dict) or not isinstance(script.get('segments'), list) or not script['segments']:
        raise ValueError('VideoScript must contain a nonempty segments list')
    if any(not isinstance(s, dict) or not isinstance(s.get('narration'), str) for s in script['segments']):
        raise ValueError('Each segment must contain a narration string')
    if len(size) != 2 or any(n < 32 or n % 2 for n in size):
        raise ValueError('Size must contain even dimensions of at least 32 pixels')
    language = lang or script.get('lang', 'zh')
    if tts not in ('auto', 'edge', 'say'):
        raise ValueError('Unknown TTS backend')
    backend = 'fake' if os.environ.get('NPOCUT_FAKE_TTS') == '1' else ('say' if tts == 'say' else 'edge')
    voice = ('fake' if backend == 'fake' else
             (voice or EDGE_VOICES[language]) if backend == 'edge' else choose_voice(language, voice))
    fallback_reason = None
    if output_slug is not None and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', output_slug):
        raise ValueError('Invalid output slug')
    slug = output_slug or slugify(script.get('id') or script.get('title', 'video'))
    format_name = {(1920, 1080): 'landscape', (1080, 1920): 'vertical'}.get(
        tuple(size), f'{size[0]}x{size[1]}')
    if custom_size:
        format_name = f'{size[0]}x{size[1]}'
    destination = out.resolve() / slug / format_name
    with tempfile.TemporaryDirectory(prefix='npocut-kb-') as directory:
        tmp = Path(directory)
        audio, slides, durations, boundaries, visual_layers = [], [], [], [], []
        # Validate against the authored script. If measured speech later re-cuts
        # a shot, each child inherits its parent's artwork.
        scene_images = load_scene_images(scene_manifest, script) if scene_manifest else None
        def synthesize_all():
            """Synthesize, then enforce template shot and total limits on measured audio."""
            while True:
                durations.clear()
                boundaries.clear()
                for i, segment in enumerate(script['segments']):
                    duration, events = cached_speech(segment['narration'], voice, backend,
                                                     tmp / f'audio-{i}.wav', out.resolve() / '.tts_cache')
                    durations.append(duration)
                    boundaries.append(events)
                template = script.get('template') or {}
                maximum = (template.get('shot_seconds') or [None, None])[1]
                oversized = next((i for i, value in enumerate(durations)
                                  if maximum and value > maximum + .02
                                  and len(script['segments'][i]['narration']) > 1), None)
                if oversized is None:
                    break
                count = max(2, math.ceil(durations[oversized] / maximum))
                replacement = split_segment(script['segments'][oversized], maximum, count)
                if len(replacement) == 1:
                    break
                script['segments'][oversized:oversized + 1] = replacement
                if scene_images is not None:
                    scene_images[oversized:oversized + 1] = [scene_images[oversized]] * len(replacement)

            minimum = (template.get('shot_seconds') or [None, None])[0]
            if minimum:
                for index, duration in enumerate(durations):
                    if duration + .001 >= minimum:
                        continue
                    padded = tmp / f'audio-padded-{index}.wav'
                    ffmpeg('-i', tmp / f'audio-{index}.wav', '-af',
                           f'apad=whole_dur={minimum}', '-t', minimum,
                           '-ar', 24000, '-ac', 1, '-c:a', 'pcm_s16le', padded)
                    padded.replace(tmp / f'audio-{index}.wav')
                    durations[index] = probe_duration(tmp / f'audio-{index}.wav')

            limit = template.get('max_duration')
            if limit and sum(durations) > limit:
                outro = (len(durations) - 1
                         if script['segments'][-1].get('kind') == 'outro' else None)
                reserve = durations[outro] if outro is not None else 0
                keep, elapsed = [], 0.0
                stop = outro if outro is not None else len(durations)
                for index in range(stop):
                    if elapsed + durations[index] + reserve > limit + .001:
                        break
                    keep.append(index)
                    elapsed += durations[index]
                if outro is not None:
                    keep.append(outro)
                script['segments'] = [script['segments'][i] for i in keep]
                if scene_images is not None:
                    scene_images[:] = [scene_images[i] for i in keep]
                durations[:] = [durations[i] for i in keep]
                boundaries[:] = [boundaries[i] for i in keep]
                for new_index, old_index in enumerate(keep):
                    if new_index != old_index:
                        shutil.copyfile(tmp / f'audio-{old_index}.wav',
                                        tmp / f'audio-kept-{new_index}.wav')
                for new_index, old_index in enumerate(keep):
                    if new_index != old_index:
                        shutil.move(tmp / f'audio-kept-{new_index}.wav',
                                    tmp / f'audio-{new_index}.wav')

        report_phase('narration')
        try:
            synthesize_all()
        except Exception as exc:
            if tts != 'auto' or backend != 'edge':
                raise
            fallback_reason = f'{type(exc).__name__}: {exc}'
            logging.warning('Edge TTS failed; using say for the whole video: %s', fallback_reason)
            backend = 'say'
            voice = choose_voice(language)
            synthesize_all()
        report_phase('images')
        for i, segment in enumerate(script['segments']):
            wav, slide = tmp / f'audio-{i}.wav', tmp / f'slide-{i}.png'
            artwork = scene_images[i] if scene_images else None
            draw_slide(script, segment, i, size, slide, artwork, style)
            if scene_images is not None:
                background, overlay = tmp / f'visual-{i}.png', tmp / f'overlay-{i}.png'
                draw_visual_layers(script, segment, i, size, artwork, background, overlay, style)
                visual_layers.append((background, overlay))
            else:
                visual_layers.append(None)
            audio.append(wav)
            slides.append(slide)
        report_phase('combine')
        video = assemble(slides, audio, durations, tmp, visual_layers, motion)
        make_subtitles(script, durations, tmp / 'subs.srt', size, boundaries)
        final = tmp / f'{slug}.subbed.mp4' if burn else video
        if burn:
            burn_subtitles(video, final, size, tmp)
        files = {'video': 'video.mp4', 'subtitles': 'subs.srt', 'final': final.name}
        available = sum(image is not None for image in scene_images) if scene_images is not None else 0
        visuals = {'mode': ('legacy' if scene_images is None else
                            'ken-burns' if motion and available else 'static'),
                   'scene_count': available,
                   'missing_scene_count': (0 if scene_images is None else
                                           len(scene_images) - available),
                   'motion_scale': .04 if motion and available else 0,
                   'fade_seconds_max': .25 if motion and available else 0,
                   'subtitle_safe_area': {'bottom_fraction': .18},
                   'aspect_ratio': '9:16' if size[1] > size[0] else '16:9',
                   'scene_manifest': str(Path(scene_manifest).resolve()) if scene_manifest else None}
        write_manifest(script, durations, voice, files, final, tmp / 'manifest.json', backend,
                       fallback_reason, visuals)
        destination.mkdir(parents=True, exist_ok=True)
        for name in set(files.values()) | {'manifest.json'}:
            shutil.copy2(tmp / name, destination / name)
    return destination / final.name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('id')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--from-json', type=Path)
    source.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--script', type=Path)
    parser.add_argument('--vertical', action='store_true')
    parser.add_argument('--size', help='Even WxH dimensions; vertical swaps landscape dimensions')
    parser.add_argument('--style', choices=['editorial', 'paper', 'contrast'], default='editorial')
    parser.add_argument('--tts', choices=['auto', 'edge', 'say'], default='auto')
    parser.add_argument('--voice')
    parser.add_argument('--lang', choices=['zh', 'ja', 'en'])
    parser.add_argument('--max-points', type=int, default=6)
    parser.add_argument('--template', choices=template_names(),
                        help='finished-video preset; omit to preserve the legacy template')
    parser.add_argument('--out', type=Path, default=Path('kb_out'))
    parser.add_argument('--no-burn', action='store_true')
    parser.add_argument('--scenes', type=Path, help='kb_scenes scene-manifest.json')
    parser.add_argument('--low-performance', action='store_true',
                        help='use scene images without Ken Burns motion or fades')
    args = parser.parse_args(argv)
    try:
        size = tuple(map(int, args.size.lower().split('x'))) if args.size else (1920, 1080)
        if len(size) != 2:
            raise ValueError('Use --size WxH')
        if args.vertical:
            size = (min(size), max(size))
        if args.script:
            if args.template:
                raise ValueError('--script cannot be combined with --template; provide source data so template rules can be applied')
            script = json.loads(args.script.read_text(encoding='utf-8-sig'))
        else:
            library = load_library(args.from_json) if args.from_json else fetch_library(args.base_url)
            item = next((i for i in library if str(i.get('id')) == args.id), None)
            if item is None:
                raise ValueError(f'Item not found: {args.id}')
            script = (build_template_script(item, args.template, args.lang or 'zh')
                      if args.template else build_script(item, args.max_points, args.lang or 'zh'))
            script['categories'] = item.get('categories', [])
        print(render_script(script, out=args.out, size=size, voice=args.voice,
                            lang=args.lang, tts=args.tts, burn=not args.no_burn,
                            custom_size=bool(args.size), scene_manifest=args.scenes,
                            motion=not args.low_performance, style=args.style))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'kb_video: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
