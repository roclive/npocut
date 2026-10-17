#!/usr/bin/env python3
"""Render an OntoKB VideoScript as narrated slides with burned subtitles."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

from common import FFMPEG, Cue, concat_file_line, probe_duration, write_srt
from kb_source import DEFAULT_BASE_URL, build_script, fetch_library, load_library, slugify

PAD = 0.4


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
    return 'Tingting' if lang == 'zh' and 'Tingting' in voices else voices[0]


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


def draw_slide(script: dict, segment: dict, index: int, size: tuple[int, int], target: Path) -> None:
    width, height = size
    image = Image.new('RGB', size, '#101e31')
    draw = ImageDraw.Draw(image)
    margin = max(8, int(width * .075))
    usable = width - margin * 2
    short = min(size)
    draw.rounded_rectangle((margin, int(height * .09), width-margin, int(height * .105)),
                           radius=2, fill='#53d7bd')
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
                                max(1, int(short * (.095 if title else .075))))
    body_height = available - len(headings) * hs - gap
    bf, bodies, bs = fit_text(body, usable, max(1, body_height), max(1, int(short * .055)))
    block_height = len(headings) * hs + gap + len(bodies) * bs
    y = top + (available - block_height) / 2
    for lines, font, spacing, color in [(headings, hf, hs, '#ffffff'),
                                        (bodies, bf, bs, '#d4e2ef')]:
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
        draw.text((margin, y), line, font=font, fill='#8fa7bf')
        y += spacing
    image.save(target)


def assemble(slides: list[Path], audio: list[Path], durations: list[float], tmp: Path) -> Path:
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
        ffmpeg('-loop', 1, '-framerate', 30, '-i', slide, '-t', frames/30,
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


def make_subtitles(script: dict, durations: list[float], target: Path) -> None:
    cues = []
    offset = 0.0
    for segment, duration in zip(script['segments'], durations):
        sentences = [s.strip() for s in re.findall(r'[^。！？.!?\n]+[。！？.!?]*|[。！？.!?]+',
                                                   segment['narration']) if s.strip()]
        weights = [max(.01, speech_weight(s)) for s in sentences]
        total = sum(weights)
        cursor = offset
        for sentence, weight in zip(sentences, weights):
            end = cursor + (duration-PAD) * weight / total
            cues.append(Cue(cursor, end, (sentence,)))
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
    font_size = max(1, round(288 * min(size) / size[1] * .033))
    style = (f'FontName={family},FontSize={font_size},Alignment=2,'
             'MarginV=9,MarginL=18,MarginR=18,Outline=1,Shadow=0')
    style = style.replace(',', r'\,')
    ffmpeg('-i', video, '-vf', f'subtitles=subs.srt:fontsdir=fonts:force_style={style}',
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'copy', '-movflags', '+faststart', target, cwd=tmp)



def write_manifest(script: dict, durations: list[float], voice: str,
                   files: dict, final: Path, target: Path) -> None:
    manifest = dict(script=script, durations=durations, voice=voice, files=files,
                    total_duration=probe_duration(final),
                    created_at=datetime.now(timezone.utc).isoformat())
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def render_script(script: dict, *, out: Path = Path('kb_out'), size=(1920, 1080),
                  voice: str | None = None, lang: str | None = None, burn: bool = True,
                  custom_size: bool = False) -> Path:
    if not isinstance(script, dict) or not isinstance(script.get('segments'), list) or not script['segments']:
        raise ValueError('VideoScript must contain a nonempty segments list')
    if any(not isinstance(s, dict) or not isinstance(s.get('narration'), str) for s in script['segments']):
        raise ValueError('Each segment must contain a narration string')
    if len(size) != 2 or any(n < 32 or n % 2 for n in size):
        raise ValueError('Size must contain even dimensions of at least 32 pixels')
    voice = choose_voice(lang or script.get('lang', 'zh'), voice)
    slug = slugify(script.get('id') or script.get('title', 'video'))
    format_name = {(1920, 1080): 'landscape', (1080, 1920): 'vertical'}.get(
        tuple(size), f'{size[0]}x{size[1]}')
    if custom_size:
        format_name = f'{size[0]}x{size[1]}'
    destination = out.resolve() / slug / format_name
    with tempfile.TemporaryDirectory(prefix='npocut-kb-') as directory:
        tmp = Path(directory)
        audio, slides, durations = [], [], []
        for i, segment in enumerate(script['segments']):
            wav, slide = tmp / f'audio-{i}.wav', tmp / f'slide-{i}.png'
            durations.append(synthesize_segment(segment['narration'], voice, wav))
            draw_slide(script, segment, i, size, slide)
            audio.append(wav)
            slides.append(slide)
        video = assemble(slides, audio, durations, tmp)
        make_subtitles(script, durations, tmp / 'subs.srt')
        final = tmp / f'{slug}.subbed.mp4' if burn else video
        if burn:
            burn_subtitles(video, final, size, tmp)
        files = {'video': 'video.mp4', 'subtitles': 'subs.srt', 'final': final.name}
        write_manifest(script, durations, voice, files, final, tmp / 'manifest.json')
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
    parser.add_argument('--voice')
    parser.add_argument('--lang', choices=['zh', 'ja', 'en'])
    parser.add_argument('--max-points', type=int, default=6)
    parser.add_argument('--out', type=Path, default=Path('kb_out'))
    parser.add_argument('--no-burn', action='store_true')
    args = parser.parse_args(argv)
    try:
        size = tuple(map(int, args.size.lower().split('x'))) if args.size else (1920, 1080)
        if len(size) != 2:
            raise ValueError('Use --size WxH')
        if args.vertical:
            size = (min(size), max(size))
        if args.script:
            script = json.loads(args.script.read_text(encoding='utf-8-sig'))
        else:
            library = load_library(args.from_json) if args.from_json else fetch_library(args.base_url)
            item = next((i for i in library if str(i.get('id')) == args.id), None)
            if item is None:
                raise ValueError(f'Item not found: {args.id}')
            script = build_script(item, args.max_points, args.lang or 'zh')
            script['categories'] = item.get('categories', [])
        print(render_script(script, out=args.out, size=size, voice=args.voice,
                            lang=args.lang, burn=not args.no_burn, custom_size=bool(args.size)))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'kb_video: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
