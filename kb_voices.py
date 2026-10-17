"""Backend-specific voice catalog; usable without network or macOS."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import subprocess
import time

from kb_video import EDGE_VOICES, SAY_DEFAULT_VOICE

CACHE = Path('kb_out/.tts_cache/edge_voices.json')
LOCALES = {'zh': 'zh-CN', 'ja': 'ja-JP', 'en': 'en-US'}
ZH_LABELS = {'Yunyang': '云扬 · 新闻男声', 'Xiaoxiao': '晓晓 · 女声',
             'Yunxi': '云希 · 男声', 'Xiaoyi': '晓伊 · 女声', 'Yunjian': '云健 · 男声'}
FALLBACK = [
    {'ShortName': f'{locale}-{name}Neural', 'Locale': locale, 'Gender': gender}
    for locale, names in [('zh-CN', [('Yunyang', 'Male'), ('Xiaoxiao', 'Female'), ('Yunxi', 'Male'), ('Xiaoyi', 'Female'), ('Yunjian', 'Male')]),
                          ('ja-JP', [('Keita', 'Male'), ('Nanami', 'Female')]),
                          ('en-US', [('Guy', 'Male'), ('Jenny', 'Female')])]
    for name, gender in names
]


async def fetch_edge():
    import edge_tts
    return await asyncio.wait_for(edge_tts.list_voices(), timeout=8)


def edge_catalog(cache=None):
    cache = Path(cache) if cache is not None else CACHE
    try:
        if time.time() - cache.stat().st_mtime < 7 * 86400:
            voices = json.loads(cache.read_text(encoding='utf-8'))
            if valid_catalog(voices):
                return voices
    except (OSError, ValueError):
        pass
    try:
        voices = asyncio.run(fetch_edge())
        if not valid_catalog(voices):
            raise ValueError('Invalid voice catalog')
    except Exception:
        return FALLBACK
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(voices, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass
    return voices


def valid_catalog(voices):
    return isinstance(voices, list) and bool(voices) and all(
        isinstance(v, dict) and all(isinstance(v.get(k), str) for k in ('ShortName', 'Locale', 'Gender')) for v in voices)


def parse_say(text, lang):
    locales = {'zh': {'zh_CN', 'zh_TW', 'zh_HK'}, 'ja': {'ja_JP'}, 'en': {'en_US', 'en_GB'}}[lang]
    result = []
    for line in text.splitlines():
        match = re.match(r'(.+?)\s+([a-z]{2}_[A-Z]{2})\s', line)
        if match and match[2] in locales:
            name = match[1].strip()
            result.append({'name': name, 'label': name})
    return result


def list_voices(lang, cache=None):
    edge = []
    for voice in edge_catalog(cache):
        if voice['Locale'] != LOCALES[lang]:
            continue
        name = voice['ShortName']
        short = name.removeprefix(LOCALES[lang] + '-').removesuffix('Neural')
        edge.append({'name': name, 'label': ZH_LABELS.get(short, short), 'gender': voice['Gender']})
    try:
        listing = subprocess.run(['say', '-v', '?'], capture_output=True, text=True, check=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ''
    say = parse_say(listing, lang)
    default_say = SAY_DEFAULT_VOICE if lang == 'zh' and any(v['name'] == SAY_DEFAULT_VOICE for v in say) else (say[0]['name'] if say else SAY_DEFAULT_VOICE if lang == 'zh' else '')
    return {'edge': edge, 'say': say, 'defaults': {'edge': EDGE_VOICES[lang], 'say': default_say}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lang', choices=LOCALES, default='zh')
    print(json.dumps(list_voices(parser.parse_args().lang), ensure_ascii=False))
