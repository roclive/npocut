import json
from pathlib import Path
import shutil
import sys
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import kb_video as video
from common import parse_srt, probe_json
from kb_source import load_library

FIXTURE = ROOT / 'tests/fixtures/ontokb_reading.json'


@pytest.mark.skipif(not shutil.which('say'), reason='macOS say is unavailable')
def test_real_say_smoke(monkeypatch, tmp_path):
    monkeypatch.delenv('NPOCUT_FAKE_TTS', raising=False)
    target = tmp_path / 'speech.wav'
    duration = video.synthesize_segment('你好，世界。', video.choose_voice('zh'), target)
    with wave.open(str(target)) as audio:
        assert audio.getframerate() == 24000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() / audio.getframerate() > .3
    assert duration > .3


@pytest.mark.parametrize('vertical', [False, True])
def test_render_fixture(monkeypatch, tmp_path, vertical):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    item = load_library(FIXTURE)[0]
    args = [str(item['id']), '--from-json', str(FIXTURE), '--size', '320x180',
            '--max-points', '0', '--out', str(tmp_path)]
    if vertical:
        args += ['--vertical', '--no-burn']
    assert video.main(args) == 0
    directory = next(tmp_path.iterdir())
    manifest = json.loads((directory / 'manifest.json').read_text())
    final = directory / manifest['files']['final']
    data = probe_json(final)
    stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
    assert (stream['width'], stream['height']) == ((180, 320) if vertical else (320, 180))
    assert stream['codec_name'] == 'h264' and stream['pix_fmt'] == 'yuv420p'
    assert any(s['codec_name'] == 'aac' for s in data['streams'])
    duration = float(data['format']['duration'])
    assert abs(duration - sum(manifest['durations'])) < .5
    assert manifest['total_duration'] == duration
    cues = parse_srt(directory / 'subs.srt')
    assert cues
    previous = 0
    for cue in cues:
        assert previous <= cue.start < cue.end <= duration
        previous = cue.end
    # Decode the full result, including its audio, to check playability.
    video.ffmpeg('-i', final, '-f', 'null', '-')
    assert sorted(p.name for p in directory.iterdir()) == sorted(set(manifest['files'].values()) | {'manifest.json'})


def test_wrapping_and_long_slide(tmp_path):
    from PIL import ImageFont
    font = ImageFont.truetype(video.font_path(), 24)
    for text in ['中文段落。日本語。' * 20, 'An unusuallylongunbrokentoken' * 15, 'First\n第二行']:
        lines = video.wrap_text(text, font, 140)
        assert all(font.getlength(line) <= 140 for line in lines)
    script = {'title': '标题' * 40, 'segments': [{}]}
    video.draw_slide(script, dict(heading='长标题' * 30, text='内容' * 200), 0,
                     (320, 180), tmp_path / 'slide.png')


def test_sentence_timings_and_padding(tmp_path):
    script = {'segments': [{'narration': '第一句。第二句！Third sentence?'}]}
    target = tmp_path / 'subs.srt'
    video.make_subtitles(script, [5.4], target)
    cues = parse_srt(target)
    assert len(cues) == 3
    assert cues[0].start == 0 and cues[-1].end == 5
    assert all(a.end == b.start for a, b in zip(cues, cues[1:]))


def test_voice_selection(monkeypatch):
    monkeypatch.delenv('NPOCUT_FAKE_TTS', raising=False)
    class Result:
        stdout = 'Meijia zh_TW # text\nLili zh_CN # text\nTingting zh_CN # text\nKyoko ja_JP # text'
    monkeypatch.setattr(video.subprocess, 'run', lambda *a, **kw: Result())
    assert video.choose_voice('zh') == 'Tingting'
    assert video.choose_voice('ja') == 'Kyoko'
    assert video.choose_voice('en', 'Custom') == 'Custom'
    with pytest.raises(ValueError, match='en_US'):
        video.choose_voice('en')


def test_script_cli_offline(monkeypatch, tmp_path):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    path = tmp_path / 'script.json'
    path.write_text(json.dumps({'id': 'custom', 'title': 'Example', 'segments': [
        {'kind': 'title', 'heading': 'Example', 'narration': 'Hello.'}]}))
    monkeypatch.setattr(video, 'fetch_library', lambda *a: pytest.fail('must stay offline'))
    assert video.main(['custom', '--script', str(path), '--size', '64x36',
                       '--no-burn', '--out', str(tmp_path / 'out')]) == 0


@pytest.mark.parametrize('size', [(321, 180), (0, 0)])
def test_invalid_size(size):
    with pytest.raises(ValueError, match='Size'):
        video.render_script({'segments': [{'narration': 'Hello'}]}, size=size)
