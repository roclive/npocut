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


@pytest.mark.parametrize('vertical', [False, True])
def test_render_fixture(monkeypatch, tmp_path, vertical, capsys):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    item = load_library(FIXTURE)[0]
    args = [str(item['id']), '--from-json', str(FIXTURE), '--size', '320x180',
            '--max-points', '0', '--out', str(tmp_path)]
    if vertical:
        args += ['--vertical', '--no-burn']
    assert video.main(args) == 0
    progress = capsys.readouterr().err
    phases = [f'{video.PHASE_PREFIX}{name}' for name in ('narration', 'images', 'combine')]
    assert all(phase in progress for phase in phases)
    assert [progress.index(phase) for phase in phases] == sorted(progress.index(phase) for phase in phases)
    directory = next(tmp_path.iterdir()) / ('180x320' if vertical else '320x180')
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


def test_formats_coexist(monkeypatch, tmp_path):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    item = load_library(FIXTURE)[0]
    for vertical, dimensions in [(False, (320, 180)), (True, (180, 320))]:
        args = [str(item['id']), '--from-json', str(FIXTURE), '--size', '320x180',
                '--max-points', '0', '--out', str(tmp_path)]
        assert video.main(args + (['--vertical'] if vertical else [])) == 0
    directory = next(tmp_path.iterdir())
    for dimensions in [(320, 180), (180, 320)]:
        folder = directory / f'{dimensions[0]}x{dimensions[1]}'
        manifest = json.loads((folder / 'manifest.json').read_text())
        final = folder / manifest['files']['final']
        assert final.exists()
        stream = next(s for s in probe_json(final)['streams'] if s['codec_type'] == 'video')
        assert (stream['width'], stream['height']) == dimensions


@pytest.mark.parametrize('text', [
    '中文测试，标点。日本語、句読点。' * 4,
    '测试（括号）以及「引用」和『内容』【标题】《书名》' * 3,
    '你好！？结束……下一句；说明：内容。' * 3,
])
def test_kinsoku(text):
    from PIL import ImageFont
    font = ImageFont.truetype(video.font_path(), 24)
    for width in [100, 140, 200]:
        lines = video.wrap_text(text, font, width)
        assert ''.join(lines) == text
        assert all(font.getlength(line) <= width for line in lines)
        assert all(line[0] not in '，。、；：！？）」』】》…' for line in lines)
        assert all(line[-1] not in '（「『【《' for line in lines)


@pytest.mark.parametrize('size,expected', [((1080, 1920), 49), ((1920, 1080), 38),
                                         ((360, 640), 16), ((640, 360), 13)])
def test_subtitle_font_orientation(size, expected):
    assert video.subtitle_font_size(size) == expected


def test_two_line_subtitles(tmp_out):
    text = '混合 CJK and latin 日本語 emoji 🙂 unsupported \U0010ffff ' * 20
    target = tmp_out / 'subs.srt'
    video.make_subtitles({'segments': [{'narration': text}]}, [25.4], target, (360, 640))
    cues = parse_srt(target)
    assert len(cues) > 1
    from PIL import ImageFont
    font = ImageFont.truetype(video.font_path(), video.subtitle_font_size((360, 640)))
    for cue in cues:
        assert len(cue.lines) <= 2
        assert all(font.getlength(line) <= 360 * .84 for line in cue.lines)
    assert cues[-1].end == pytest.approx(25, abs=.002)


def test_font_step_down_and_unsupported_glyphs(tmp_out):
    text = '超长要点 with Latin 日本語 🙂 \U0010ffff ' * 50
    font, lines, spacing = video.fit_text(text, 280, 80, 24)
    assert font.size < 24
    assert len(lines) * spacing <= 80
    assert all(font.getlength(line) <= 280 for line in lines)
    video.draw_slide({'title': '混合🙂', 'segments': [{}]},
                     {'heading': 'ポイント', 'text': text}, 0, (320, 180), tmp_out / 'slide.png')
    assert (tmp_out / 'slide.png').is_file()


def test_fake_tts_weight_and_audio(tmp_out):
    assert video.speech_weight('中A 日b') == pytest.approx(.48)
    duration = video.synthesize_segment('中A', video.choose_voice('ja'), tmp_out / 'audio.wav')
    assert duration == pytest.approx(1.5 + video.PAD, abs=.02)
    with wave.open(str(tmp_out / 'audio.wav')) as audio:
        assert audio.getnchannels() == 1 and audio.getframerate() == 24000


def test_japanese_edge_render(tmp_out, tiny_size):
    from kb_source import build_script
    script = build_script(dict(id='ja', title='日本語🙂', summary='https://example.test',
                               key_points=[]), lang='ja')
    final = video.render_script(script, out=tmp_out, size=tiny_size)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert manifest['script']['lang'] == 'ja'
    assert manifest['voice'] == 'fake'
    assert final.is_file()


def test_long_collision_slug_preserved(tmp_out, tiny_size):
    slug = 'a' * 80 + '-12345678'
    script = {'id': 'a' * 100, 'segments': [{'narration': 'Hi'}]}
    final = video.render_script(script, out=tmp_out, size=tiny_size, burn=False, output_slug=slug)
    assert final.parent.parent.name == slug
    with pytest.raises(ValueError, match='slug'):
        video.render_script(script, size=tiny_size, output_slug='../escape')


def test_voice_locale_fallback(monkeypatch):
    monkeypatch.delenv('NPOCUT_FAKE_TTS', raising=False)
    class Result:
        stdout = 'Lili zh_CN # text\nKyoko ja_JP # text\nOtoya ja_JP # text'
    monkeypatch.setattr(video.subprocess, 'run', lambda *a, **kw: Result())
    assert video.choose_voice('zh') == 'Lili'
    assert video.choose_voice('ja') == 'Kyoko'


def test_orphan_clause(tmp_path):
    text = 'Anthropic 将投入 1 亿美元培养前沿部署工程师，目标是到 2027 年底培养一万人。'
    target = tmp_path / 'subs.srt'
    video.make_subtitles({'segments': [{'narration': text}]}, [12.4], target, (1080, 1920))
    cues = parse_srt(target)
    assert all(len(''.join(c.lines).strip()) >= 6 for c in cues)
    assert ''.join(''.join(c.lines) for c in cues).replace(' ', '') == text.replace(' ', '')
    assert all(len(c.lines) <= 2 for c in cues)


def test_edge_boundaries_cache(monkeypatch, tmp_path):
    import types
    monkeypatch.delenv('NPOCUT_FAKE_TTS')
    mp3 = tmp_path / 'sample.mp3'
    video.ffmpeg('-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', 3, mp3)
    calls = []
    class Communicate:
        def __init__(self, text, voice, **kwargs):
            assert kwargs == {'boundary': 'SentenceBoundary'}
            calls.append((text, voice))
        async def stream(self):
            yield dict(type='audio', data=mp3.read_bytes())
            yield dict(type='SentenceBoundary', text='第一句。', offset=2000000, duration=6000000)
            yield dict(type='SentenceBoundary', text='第二句。', offset=15000000, duration=10000000)
    monkeypatch.setitem(sys.modules, 'edge_tts', types.SimpleNamespace(Communicate=Communicate))
    script = {'id': 'edge', 'segments': [{'narration': '第一句。第二句。'}]}
    for _ in range(2):
        final = video.render_script(script, out=tmp_path / 'out', size=(64, 36), burn=False)
    assert len(calls) == 1
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert manifest['tts_backend'] == 'edge'
    assert manifest['voice'] == 'zh-CN-YunyangNeural'
    cues = parse_srt(final.parent / 'subs.srt')
    assert [(c.start, c.end) for c in cues] == [(.2, .8), (1.5, 2.5)]


def test_auto_whole_video_fallback(monkeypatch, tmp_path, caplog):
    import types
    monkeypatch.delenv('NPOCUT_FAKE_TTS')
    monkeypatch.setattr(video.time, 'sleep', lambda _: None)
    mp3 = tmp_path / 'sample.mp3'
    video.ffmpeg('-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', 1, mp3)
    calls = []
    class Communicate:
        def __init__(self, text, voice, **kwargs):
            calls.append(text)
            self.text = text
        async def stream(self):
            if self.text == '第一句。':
                yield dict(type='audio', data=mp3.read_bytes())
            else:
                raise OSError('offline')
    monkeypatch.setitem(sys.modules, 'edge_tts', types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(video, 'choose_voice', lambda *args: 'Tingting')
    original = video.synthesize_segment
    say_calls = []
    def silent_say(text, voice, target):
        say_calls.append((text, voice))
        with monkeypatch.context() as context:
            context.setenv('NPOCUT_FAKE_TTS', '1')
            return original(text, voice, target)
    monkeypatch.setattr(video, 'synthesize_segment', silent_say)
    script = {'id': 'fallback', 'segments': [{'narration': '第一句。'}, {'narration': '第二句。'}]}
    final = video.render_script(script, out=tmp_path / 'out', size=(64, 36), burn=False)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert manifest['tts_backend'] == 'say' and manifest['voice'] == 'Tingting'
    assert 'offline' in manifest['fallback_reason']
    assert len(calls) == 4 and len(say_calls) == 2
    assert len(caplog.records) == 1
