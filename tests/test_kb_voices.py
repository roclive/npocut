import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import kb_voices as voices


def test_edge_listing_and_cache(tmp_path, monkeypatch):
    listing = AsyncMock(return_value=voices.FALLBACK)
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(list_voices=listing))
    monkeypatch.setattr(voices.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='Tingting zh_CN # hello\n'))
    cache = tmp_path / 'voices.json'
    result = voices.list_voices('zh', cache)
    assert result['defaults'] == {'edge': 'zh-CN-YunyangNeural', 'say': 'Tingting'}
    assert len(result['edge']) == 5
    assert result['edge'][0]['label'] == '云扬 · 新闻男声'
    assert voices.list_voices('ja', cache)['edge'][0]['name'] == 'ja-JP-KeitaNeural'
    assert listing.await_count == 1
    assert json.loads(cache.read_text()) == voices.FALLBACK
    os.utime(cache, (0, 0))
    voices.edge_catalog(cache)
    assert listing.await_count == 2


def test_offline_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, 'fetch_edge', AsyncMock(side_effect=OSError('offline')))
    assert voices.edge_catalog(tmp_path / 'missing') == voices.FALLBACK


def test_say_parsing():
    sample = '''Tingting            zh_CN # 你好
Sin-ji              zh_HK # hello
Mei-Jia             zh_TW # hello
Kyoko               ja_JP # hello
Samantha            en_US # hello
Daniel (Enhanced)   en_GB # hello
Thomas              fr_FR # hello
'''
    assert [v['name'] for v in voices.parse_say(sample, 'zh')] == ['Tingting', 'Sin-ji', 'Mei-Jia']
    assert voices.parse_say(sample, 'ja') == [{'name': 'Kyoko', 'label': 'Kyoko'}]
    assert len(voices.parse_say(sample, 'en')) == 2
