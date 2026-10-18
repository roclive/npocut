"""Restrained, source-only finished-video template presets."""
from __future__ import annotations

import copy

from kb_source import _plain, build_script


TEMPLATES = {
    'knowledge-brief': {
        'label': '知识简报', 'max_duration': 90, 'shot_seconds': [5, 12],
        'max_points': 4, 'summary_parts': 2, 'hook': 'title',
        'font_scale': 1.0,
        'colors': {'background': '#101e31', 'veil': '#081321', 'accent': '#53d7bd',
                   'heading': '#ffffff', 'body': '#d4e2ef', 'footer': '#8fa7bf'},
    },
    'concept-explainer': {
        'label': '概念讲解', 'max_duration': 150, 'shot_seconds': [8, 18],
        'max_points': 5, 'summary_parts': 2, 'hook': 'title',
        'font_scale': .94,
        'colors': {'background': '#172019', 'veil': '#0c1510', 'accent': '#d2a85d',
                   'heading': '#fffaf0', 'body': '#e1ddcf', 'footer': '#a8aa9e'},
    },
    'vertical-short': {
        'label': '竖版短视频', 'max_duration': 45, 'shot_seconds': [3, 8],
        'max_points': 2, 'summary_parts': 0, 'hook': 'first-key-point',
        'font_scale': 1.14,
        'colors': {'background': '#211720', 'veil': '#160d14', 'accent': '#ff745c',
                   'heading': '#fff8f4', 'body': '#f0dcd5', 'footer': '#bfa29d'},
    },
}


def template_config(name: str) -> dict:
    if name not in TEMPLATES:
        raise ValueError(f'Unknown template: {name}')
    return copy.deepcopy(dict(id=name, **TEMPLATES[name]))


def build_template_script(item: dict, name: str, lang: str = 'zh') -> dict:
    """Select source-backed material without padding or repeated narration."""
    config = template_config(name)
    base = build_script(item, max_points=config['max_points'], lang=lang)
    title, summaries, points, outro = [], [], [], []
    for segment in base['segments']:
        {'title': title, 'summary': summaries, 'point': points,
         'outro': outro}[segment['kind']].append(segment)
    if config['hook'] == 'first-key-point' and points:
        hook = dict(points.pop(0), kind='hook', heading={'zh': '关键数据', 'ja': '要点', 'en': 'Key fact'}[lang])
        candidates = [hook, *points, *outro]
    else:
        candidates = [*title, *summaries[:config['summary_parts']], *points, *outro]

    # Keep each fact once. Sparse sources produce a shorter video; no filler is made.
    segments, seen, seconds = [], set(), 0.0
    outro_segment = candidates[-1] if candidates and candidates[-1].get('kind') == 'outro' else None
    reserve = _estimated_seconds(outro_segment['narration']) if outro_segment else 0
    for segment in candidates:
        key = _plain(segment.get('narration'))
        estimate = _estimated_seconds(key)
        is_outro = segment.get('kind') == 'outro'
        if (key and key not in seen and
                (is_outro or seconds + estimate + reserve <= config['max_duration'])):
            seen.add(key)
            segments.append(segment)
            seconds += estimate
    # A long hook is still preferable to fabricated filler; always retain it and the source page.
    if not segments and candidates:
        segments.append(candidates[0])
    if outro_segment and outro_segment not in segments:
        segments.append(outro_segment)
    result = dict(base, segments=segments, template=config)
    return result


def _estimated_seconds(text: str) -> float:
    return max(2.0, len(_plain(text)) / 5 + .35)


def template_names() -> tuple[str, ...]:
    return tuple(TEMPLATES)
