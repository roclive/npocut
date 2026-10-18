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
from kb_templates import build_template_script

FIXTURE = ROOT / 'tests/fixtures/ontokb_reading.json'


def assert_chapter_progress_visible(path, size, tmp_path):
    """Check the fixed teal progress bar in a decoded frame, not its source PNG."""
    from PIL import Image
    frame = tmp_path / f'progress-{len(list(tmp_path.glob("progress-*.png")))}.png'
    video.ffmpeg('-ss', .1, '-i', path, '-frames:v', 1, frame)
    with Image.open(frame) as image:
        width, height = size
        pixels = image.convert('RGB').crop((0, int(height * .78), width, int(height * .84)))
        assert any(g > r * 1.45 and g > b * 1.05 and g > 90
                   for r, g, b in pixels.getdata())


@pytest.mark.parametrize('vertical', [False, True])
def test_render_fixture(monkeypatch, tmp_path, vertical):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    item = load_library(FIXTURE)[0]
    args = [str(item['id']), '--from-json', str(FIXTURE), '--size', '320x180',
            '--max-points', '0', '--out', str(tmp_path)]
    if vertical:
        args += ['--vertical', '--no-burn']
    assert video.main(args) == 0
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


def test_script_and_template_are_rejected(tmp_path, capsys):
    path = tmp_path / 'script.json'
    path.write_text(json.dumps({'segments': [{'narration': 'source fact'}]}))
    with pytest.raises(SystemExit) as raised:
        video.main(['custom', '--script', str(path), '--template', 'vertical-short'])
    assert raised.value.code == 1
    assert 'cannot be combined' in capsys.readouterr().err


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


@pytest.mark.parametrize('motion,expected', [(True, 'ken-burns'), (False, 'static')])
def test_scene_images_motion_and_low_performance(tmp_path, motion, expected):
    from PIL import Image
    script = {'id': 'visual', 'title': 'Visual story', 'segments': [
        {'kind': 'title', 'heading': 'Opening', 'narration': 'Hello.'},
        {'kind': 'outro', 'heading': 'Source', 'narration': 'Bye.'},
    ]}
    images = []
    for index, color in enumerate(('#b34c35', '#286f82')):
        path = tmp_path / f'art-{index}.png'
        Image.new('RGB', (96, 96), color).save(path)
        images.append(path)
    scene_manifest = tmp_path / 'scene-manifest.json'
    scene_manifest.write_text(json.dumps({'source_id': 'visual', 'scenes': [
        {'narration': {'segment_index': i, 'text': segment['narration']},
         'image': {'path': str(images[i])}}
        for i, segment in enumerate(script['segments'])]}), encoding='utf-8')
    final = video.render_script(script, out=tmp_path / 'out', size=(64, 36), burn=False,
                                scene_manifest=scene_manifest, motion=motion)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert manifest['visuals']['mode'] == expected
    assert manifest['visuals']['scene_count'] == 2
    assert manifest['visuals']['subtitle_safe_area']['bottom_fraction'] == .18
    data = probe_json(final)
    assert any(s['codec_type'] == 'audio' for s in data['streams'])
    video.ffmpeg('-i', final, '-f', 'null', '-')
    if not motion:
        # Low-performance encoding must composite the fixed overlay instead of
        # falling back to the old precomposed slide (which has no progress bar).
        assert_chapter_progress_visible(final, (64, 36), tmp_path)


def test_visual_layer_uses_template_veil_color(tmp_path):
    from PIL import Image
    size = (64, 36)
    artwork = tmp_path / 'artwork.png'
    background = tmp_path / 'background.png'
    overlay = tmp_path / 'overlay.png'
    Image.new('RGB', size, '#ff0000').save(artwork)
    script = {
        'title': 'Veil',
        'template': {'colors': {'veil': '#0000ff'}},
        'segments': [{'kind': 'title', 'heading': 'Veil', 'narration': 'Veil'}],
    }

    video.draw_visual_layers(script, script['segments'][0], 0, size, artwork,
                             background, overlay)

    with Image.open(background) as rendered:
        assert rendered.convert('RGB').getpixel((0, 0)) == (173, 0, 81)


def test_scene_manifest_validation_and_missing_image_fallback(tmp_path, tiny_size):
    script = {'id': 'visual', 'segments': [{'narration': 'Hello.'}]}
    manifest = tmp_path / 'scene-manifest.json'
    manifest.write_text(json.dumps({'source_id': 'visual', 'scenes': [
        {'narration': {'segment_index': 0, 'text': 'Hello.'},
         'image': {'path': 'missing.png'}}]}), encoding='utf-8')
    final = video.render_script(script, out=tmp_path / 'out', size=tiny_size, burn=False,
                                scene_manifest=manifest)
    data = json.loads((final.parent / 'manifest.json').read_text())
    assert data['visuals']['mode'] == 'static'
    assert data['visuals']['missing_scene_count'] == 1
    # A missing scene image still uses the fixed scene overlay over a dark
    # background, including chapter progress.
    assert_chapter_progress_visible(final, tiny_size, tmp_path)
    broken = json.loads(manifest.read_text())
    broken['scenes'][0]['narration']['text'] = 'Different.'
    manifest.write_text(json.dumps(broken), encoding='utf-8')
    with pytest.raises(ValueError, match='order'):
        video.render_script(script, out=tmp_path / 'bad', size=tiny_size,
                            scene_manifest=manifest)


def test_legacy_mode_does_not_report_missing_scenes(monkeypatch, tmp_path, tiny_size):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    script = {'id': 'legacy', 'segments': [
        {'narration': 'First.'},
        {'narration': 'Second.'},
    ]}

    final = video.render_script(script, out=tmp_path / 'out', size=tiny_size, burn=False)

    visuals = json.loads((final.parent / 'manifest.json').read_text())['visuals']
    assert visuals['mode'] == 'legacy'
    assert visuals['scene_count'] == 0
    assert visuals['missing_scene_count'] == 0


def test_scene_manifest_accepts_legacy_cwd_relative_image_path(monkeypatch, tmp_path):
    """Consume paths emitted when kb_scenes receives its default relative --out."""
    from PIL import Image

    monkeypatch.chdir(tmp_path)
    output = Path('kb_out/scenes')
    image = output / 'image-cache' / 'scene.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (16, 16), '#286f82').save(image)
    manifest = output / 'scene-manifest.json'
    manifest.write_text(json.dumps({'source_id': 'visual', 'scenes': [
        {'narration': {'segment_index': 0, 'text': 'Hello.'},
         'image': {'path': str(image)}}
    ]}), encoding='utf-8')

    loaded = video.load_scene_images(manifest, {
        'id': 'visual', 'segments': [{'narration': 'Hello.'}]
    })

    assert loaded == [image.resolve()]


@pytest.mark.parametrize('template,vertical', [('knowledge-brief', False),
                                                ('vertical-short', True)])
def test_finished_template_real_clip_records_config(monkeypatch, tmp_path, template, vertical):
    """Use macOS speech and burned subtitles for two production-like visual proofs."""
    monkeypatch.delenv('NPOCUT_FAKE_TTS', raising=False)
    if not shutil.which('say'):
        pytest.skip('macOS say is required for the real-TTS acceptance clip')
    item = {'id': 'real-proof', 'title': 'AI 培训计划', 'url': 'https://example.test/source',
            'summary': '该计划提供四天培训，并安排后续实践。',
            'key_points': ['项目计划在二零二七年底前培养一万人。'], 'categories': ['AI']}
    fixture = tmp_path / 'real.json'
    fixture.write_text(json.dumps([item], ensure_ascii=False))
    out = tmp_path / template
    args = [item['id'], '--from-json', str(fixture), '--template', template,
            '--size', '640x360', '--tts', 'say', '--out', str(out)]
    if vertical:
        args.append('--vertical')
    assert video.main(args) == 0
    manifest_path = next(out.glob('*/*/manifest.json'))
    manifest = json.loads(manifest_path.read_text())
    final = manifest_path.parent / manifest['files']['final']
    assert manifest['template']['id'] == template
    assert manifest['aspect_ratio'] == ('9:16' if vertical else '16:9')
    assert manifest['tts_backend'] == 'say' and manifest['voice'] != 'fake'
    assert final.name.endswith('.subbed.mp4')
    media_duration = float(probe_json(final)['format']['duration'])
    assert abs(media_duration - sum(manifest['durations'])) < .5
    assert media_duration <= manifest['template']['max_duration'] + .05
    assert max(manifest['durations']) <= manifest['template']['shot_seconds'][1] + .05
    cues = parse_srt(manifest_path.parent / 'subs.srt')
    assert cues[-1].end <= media_duration
    # Decode a cue midpoint from the burned deliverable. Bright subtitle glyphs
    # must be visible inside the reserved bottom area at a readable font size.
    from PIL import Image
    frame = tmp_path / f'{template}.png'
    midpoint = (cues[0].start + cues[0].end) / 2
    video.ffmpeg('-ss', midpoint, '-i', final, '-frames:v', 1, frame)
    with Image.open(frame) as image:
        lower = image.convert('RGB').crop((0, int(image.height * .80), image.width, image.height))
        assert video.subtitle_font_size(image.size) >= 13
        assert sum(max(pixel) > 210 for pixel in lower.getdata()) > 5


def test_measured_slow_tts_is_recut_to_template_limits(monkeypatch, tmp_path):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    original = video.speech_weight
    monkeypatch.setattr(video, 'speech_weight', lambda text: original(text) * 3)
    item = load_library(FIXTURE)[0]
    script = build_template_script(item, 'vertical-short')
    final = video.render_script(script, out=tmp_path, size=(64, 36), burn=False)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert sum(manifest['durations']) <= 45.001
    assert max(manifest['durations']) <= 8.02
    assert manifest['script']['segments'][0]['kind'] == 'hook'
    assert manifest['script']['segments'][-1]['kind'] == 'outro'


def test_template_scene_manifest_survives_measured_tts_recut(monkeypatch, tmp_path):
    """Authored scene artwork follows shots split after speech is measured."""
    from PIL import Image
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    original = video.speech_weight
    monkeypatch.setattr(video, 'speech_weight', lambda text: original(text) * 3)
    item = load_library(FIXTURE)[0]
    script = build_template_script(item, 'vertical-short')
    scenes = []
    for index, segment in enumerate(script['segments']):
        image = tmp_path / f'scene-{index}.png'
        Image.new('RGB', (16, 16), (index * 20, 30, 40)).save(image)
        scenes.append({'narration': {'segment_index': index, 'text': segment['narration']},
                       'image': {'path': str(image)}})
    scene_manifest = tmp_path / 'scene-manifest.json'
    scene_manifest.write_text(json.dumps({'source_id': script['id'], 'scenes': scenes}),
                              encoding='utf-8')

    final = video.render_script(script, out=tmp_path / 'out', size=(64, 36), burn=False,
                                scene_manifest=scene_manifest)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    assert len(manifest['script']['segments']) > len(scenes)
    assert manifest['visuals']['scene_count'] == len(manifest['script']['segments'])


@pytest.mark.parametrize('template', ['concept-explainer', 'vertical-short'])
def test_measured_template_shots_respect_minimum(monkeypatch, tmp_path, template):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    item = load_library(FIXTURE)[0]
    script = build_template_script(item, template)
    final = video.render_script(script, out=tmp_path / template, size=(64, 36), burn=False)
    manifest = json.loads((final.parent / 'manifest.json').read_text())
    minimum, maximum = manifest['template']['shot_seconds']
    assert all(minimum - .01 <= duration <= maximum + .02
               for duration in manifest['durations'])
