import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_autogen as autogen


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')
    source = tmp_path / 'reading.json'
    source.write_text(json.dumps({'library': [dict(id='a', title='Hi', summary='',
                                                  key_points=[], kind='article',
                                                  url='https://example.com/?a=1&b=2')]}))
    out = tmp_path / 'out'
    args = ['--from-json', str(source), '--out', str(out), '--size', '64x36',
            '--formats', 'landscape', '--no-burn']
    return source, out, args


def state(out):
    return json.loads((out / 'autogen_state.json').read_text())


def test_first_skip_changed(setup, monkeypatch):
    source, out, args = setup
    assert autogen.main(args) == 0
    first = state(out)['a']
    assert first['status'] == 'done' and first['attempts'] == 1
    assert (out / first['outputs'][0]).is_file()
    gallery = (out / 'index.html').read_text()
    assert 'Hi' in gallery and 'MP4' in gallery and 's ·' in gallery
    assert 'a=1&amp;b=2' in gallery
    real = autogen.kb_video.render_script
    monkeypatch.setattr(autogen.kb_video, 'render_script', lambda *a, **kw: pytest.fail('must skip'))
    assert autogen.main(args) == 0
    assert state(out)['a'] == first
    payload = json.loads(source.read_text())
    payload['library'][0]['summary'] = 'New.'
    source.write_text(json.dumps(payload))
    monkeypatch.setattr(autogen.kb_video, 'render_script', real)
    assert autogen.main(args) == 0
    assert state(out)['a']['hash'] != first['hash']
    assert state(out)['a']['attempts'] == 1


def test_failure_retry_cap_and_force(setup, monkeypatch):
    _, out, args = setup
    def fail(*a, **kw):
        raise ValueError('render failed')
    monkeypatch.setattr(autogen.kb_video, 'render_script', fail)
    for attempts in range(1, 4):
        assert autogen.main(args) == 1
        record = state(out)['a']
        assert record['attempts'] == attempts
        assert record['status'] == 'failed' and record['error'] == 'render failed'
    assert autogen.main(args) == 0
    assert state(out)['a']['attempts'] == 3
    assert autogen.main(args + ['--force', 'a']) == 1
    assert state(out)['a']['attempts'] == 4


def test_force_rerenders_completed_item(setup, stub_render, monkeypatch):
    _, out, args = setup
    real_scenes = autogen.kb_scenes.render_scene_plan
    scene_calls = 0

    def count_scenes(*call_args, **kwargs):
        nonlocal scene_calls
        scene_calls += 1
        return real_scenes(*call_args, **kwargs)

    monkeypatch.setattr(autogen.kb_scenes, 'render_scene_plan', count_scenes)
    assert autogen.main(args) == 0
    first = state(out)['a']
    assert len(stub_render) == 1
    assert scene_calls == 1

    assert autogen.main(args + ['--force', 'a']) == 0
    forced = state(out)['a']
    assert len(stub_render) == 2
    assert scene_calls == 2
    assert forced['hash'] == first['hash']
    assert forced['attempts'] == first['attempts'] + 1
    assert forced['status'] == 'done'


def test_retry_recovers(setup, monkeypatch):
    _, out, args = setup
    real = autogen.kb_video.render_script
    def fail(*a, **kw):
        raise OSError('temporary')
    monkeypatch.setattr(autogen.kb_video, 'render_script', fail)
    assert autogen.main(args) == 1
    monkeypatch.setattr(autogen.kb_video, 'render_script', real)
    assert autogen.main(args) == 0
    assert state(out)['a']['status'] == 'done'
    assert state(out)['a']['attempts'] == 2
    assert state(out)['a']['error'] is None


def test_lock_and_stale(setup, monkeypatch, capsys):
    _, out, args = setup
    monkeypatch.setattr(autogen.kb_video, 'render_script', lambda *a, **kw: pytest.fail('locked'))
    with autogen.run_lock(out):
        assert autogen.main(args) == 0
        assert not (out / 'autogen_state.json').exists()
    assert 'locked, skipped' in capsys.readouterr().out
    lock = out / '.autogen.lock'
    lock.write_text('abandoned')
    os.utime(lock, (time.time() - 7201,) * 2)
    with autogen.run_lock(out):
        assert lock.exists()
    assert not lock.exists()


def test_old_live_lock_is_not_reclaimed(setup, monkeypatch, capsys):
    _, out, args = setup
    monkeypatch.setattr(autogen.kb_video, 'render_script', lambda *a, **kw: pytest.fail('locked'))
    with autogen.run_lock(out):
        lock = out / '.autogen.lock'
        os.utime(lock, (time.time() - 7201,) * 2)
        assert autogen.main(args) == 0
        assert lock.exists()
        assert not (out / 'autogen_state.json').exists()
    assert 'locked, skipped' in capsys.readouterr().out


def test_blocked_images_fail_then_retry_instead_of_becoming_done(setup, stub_render, monkeypatch):
    _, out, args = setup
    calls = 0

    def blocked(plan, output, **kwargs):
        nonlocal calls
        calls += 1
        output.mkdir(parents=True, exist_ok=True)
        manifest = dict(plan, scenes=[dict(
            plan['scenes'][0], image=dict(path=None, blocked_reason='network offline'))])
        (output / 'scene-manifest.json').write_text(json.dumps(manifest))
        return manifest

    monkeypatch.setattr(autogen.kb_scenes, 'render_scene_plan', blocked)
    assert autogen.main(args) == 1
    failed = state(out)['a']
    assert failed['status'] == 'failed'
    assert failed['attempts'] == 1
    assert failed['error'] == 'image generation blocked: network offline'
    assert failed.get('phase') is None
    assert calls == 3
    assert stub_render == []

    def succeeding(plan, output, **kwargs):
        output.mkdir(parents=True, exist_ok=True)
        image = output / 'recovered.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        import hashlib
        manifest = dict(plan, scenes=[dict(plan['scenes'][0], image=dict(
            path=str(image), sha256=hashlib.sha256(image.read_bytes()).hexdigest()))])
        (output / 'scene-manifest.json').write_text(json.dumps(manifest))
        return manifest

    # The blocked manifest left by the failed run must not be reusable.
    monkeypatch.setattr(autogen.kb_scenes, 'render_scene_plan', succeeding)
    assert autogen.main(args) == 0
    recovered = state(out)['a']
    assert recovered['status'] == 'done'
    assert recovered['attempts'] == 2
    assert autogen.scene_manifest_ready(out / recovered['scene_manifest'])


def test_corrupt_scene_image_is_rebuilt_before_video_resume(setup, stub_render, monkeypatch):
    _, out, args = setup
    real_video = autogen.kb_video.render_script
    real_scenes = autogen.kb_scenes.render_scene_plan
    video_calls = 0

    def interrupt_second(*call_args, **kwargs):
        nonlocal video_calls
        video_calls += 1
        if video_calls == 2:
            raise KeyboardInterrupt
        return real_video(*call_args, **kwargs)

    monkeypatch.setattr(autogen.kb_video, 'render_script', interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        autogen.main(args + ['--formats', 'landscape,vertical'])

    interrupted = state(out)['a']
    manifest_path = out / interrupted['scene_manifest']
    manifest = json.loads(manifest_path.read_text())
    image_path = Path(manifest['scenes'][0]['image']['path'])
    image_path.write_bytes(b'not-a-png')
    assert not autogen.scene_manifest_ready(manifest_path)

    scene_calls = 0

    def count_scenes(*call_args, **kwargs):
        nonlocal scene_calls
        scene_calls += 1
        return real_scenes(*call_args, **kwargs)

    monkeypatch.setattr(autogen.kb_scenes, 'render_scene_plan', count_scenes)
    monkeypatch.setattr(autogen.kb_video, 'render_script', real_video)
    assert autogen.main(args + ['--formats', 'landscape,vertical']) == 0
    assert scene_calls == 1
    assert autogen.scene_manifest_ready(manifest_path)
    repaired = json.loads(manifest_path.read_text())
    repaired_image = Path(repaired['scenes'][0]['image']['path']).read_bytes()
    assert repaired_image.startswith(b'\x89PNG\r\n\x1a\n')
    assert len(stub_render) == 2


def test_competing_processes_generate_once_with_intact_state(setup, tmp_path):
    source, out, args = setup
    counter = tmp_path / 'render-calls.txt'
    runner = textwrap.dedent('''
        import json
        from pathlib import Path
        import sys
        import time
        import kb_autogen

        counter = Path(sys.argv[1])

        def render_scenes(plan, output, **kwargs):
            output.mkdir(parents=True, exist_ok=True)
            image = output / 'fake.png'
            image.write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')
            import hashlib
            asset = {'path': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
            manifest = dict(plan, scenes=[dict(scene, image=asset) for scene in plan['scenes']])
            (output / 'scene-manifest.json').write_text(json.dumps(manifest))
            return manifest

        def render_video(script, *, out, size, output_slug, **kwargs):
            with counter.open('a') as stream:
                stream.write('render\\n')
                stream.flush()
            time.sleep(0.5)
            folder = out / output_slug / 'landscape'
            folder.mkdir(parents=True, exist_ok=True)
            final = folder / 'video.mp4'
            final.write_bytes(b'fixture')
            (folder / 'manifest.json').write_text(json.dumps({
                'script': script, 'total_duration': 1.0, 'files': {'final': final.name}}))
            return final

        kb_autogen.kb_scenes.render_scene_plan = render_scenes
        kb_autogen.kb_video.render_script = render_video
        raise SystemExit(kb_autogen.main(sys.argv[2:]))
    ''')
    command = [sys.executable, '-c', runner, str(counter), *args]
    env = dict(os.environ, NPOCUT_FAKE_TTS='1')
    first = subprocess.Popen(command, cwd=Path(autogen.__file__).parent, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command, cwd=Path(autogen.__file__).parent, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)
    assert (first.returncode, second.returncode) == (0, 0), (
        first_stdout, first_stderr, second_stdout, second_stderr)
    assert counter.read_text().splitlines() == ['render']
    record = state(out)['a']
    assert record['status'] == 'done'
    assert record['attempts'] == 1
    assert len(record['outputs']) == 1
    assert autogen.valid_output(out, record['outputs'][0])
    assert not (out / '.autogen.lock').exists()


def test_offline(setup, monkeypatch, capsys):
    _, out, _ = setup
    def offline(*a):
        raise URLError('unavailable')
    monkeypatch.setattr(autogen.kb_source, 'fetch_library', offline)
    assert autogen.main(['--out', str(out)]) == 0
    text = capsys.readouterr().out
    assert 'ontokb offline, skipped' in text
    assert json.loads(text.splitlines()[-1]) == dict(generated=0, skipped=0, failed=0)
    assert autogen.main(['--out', str(out), '--strict']) == 1


def test_dry_run_filters_limits(setup, monkeypatch, capsys):
    source, out, args = setup
    payload = json.loads(source.read_text())
    payload['library'] += [dict(id='b', title='B', kind='youtube')]
    source.write_text(json.dumps(payload))
    monkeypatch.setattr(autogen.kb_video, 'render_script', lambda *a, **kw: pytest.fail('dry run'))
    assert autogen.main(args + ['--dry-run', '--kinds', 'youtube', '--limit', '1']) == 0
    assert 'plan: b' in capsys.readouterr().out
    assert not list(out.rglob('*.mp4'))
    assert not (out / 'autogen_state.json').exists()
    assert not (out / 'index.html').exists()


def test_hash_options():
    item = dict(title='T', summary='S', key_points=['P'])
    digest = autogen.content_hash(item, {'voice': 'one'})
    assert digest != autogen.content_hash(item, {'voice': 'two'})
    assert digest != autogen.content_hash(dict(item, key_points=['new']), {'voice': 'one'})


def test_image_config_change_invalidates(setup, stub_render, monkeypatch):
    _, out, args = setup
    monkeypatch.setenv('KB_IMAGE_MODEL', 'model-one')
    assert autogen.main(args) == 0
    first = state(out)['a']['hash']
    assert autogen.main(args) == 0
    assert len(stub_render) == 1
    monkeypatch.setenv('KB_IMAGE_MODEL', 'model-two')
    assert autogen.main(args) == 0
    assert state(out)['a']['hash'] != first
    assert len(stub_render) == 2


def test_negative_prompt_change_invalidates(setup, stub_render, monkeypatch):
    _, out, args = setup
    assert autogen.main(args) == 0
    first = state(out)['a']
    assert first['config']['image']['negative_prompt'] == autogen.kb_scenes.NEGATIVE
    assert autogen.main(args) == 0
    assert len(stub_render) == 1

    monkeypatch.setattr(autogen.kb_scenes, 'NEGATIVE', 'changed negative prompt')
    assert autogen.main(args) == 0
    changed = state(out)['a']
    assert changed['hash'] != first['hash']
    assert changed['config']['image']['negative_prompt'] == 'changed negative prompt'
    assert len(stub_render) == 2


def test_lock_documentation_matches_process_lifetime_flock():
    root = Path(autogen.__file__).parent
    english = (root / 'README.md').read_text(encoding='utf-8')
    chinese = (root / 'README_zh.md').read_text(encoding='utf-8')
    assert 'held for the process lifetime using `flock`' in english
    assert 'expires after two hours' not in english
    assert '由 `flock` 在进程生命周期内持有' in chinese
    assert '两小时后过期' not in chinese


@pytest.fixture
def stub_render(monkeypatch):
    calls = []
    def render(script, *, out, size, output_slug, **kwargs):
        calls.append((script['id'], output_slug))
        folder = out / output_slug / ('landscape' if size[0] >= size[1] else 'vertical')
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / 'video.mp4'
        final.write_bytes(b'fixture')
        (folder / 'manifest.json').write_text(json.dumps(dict(script=script, total_duration=3.8)))
        return final
    monkeypatch.setattr(autogen.kb_video, 'render_script', render)
    return calls


def test_flat_collision_paths_and_grouped_gallery(setup, stub_render):
    source, out, args = setup
    source.write_text(json.dumps({'library': [
        dict(id='a/b', title='<Shared title>', url='https://example.test/?a=1&b=2'),
        dict(id='a b', title='Second')]}))
    assert autogen.main(args + ['--formats', 'landscape,vertical']) == 0
    records = state(out)
    first = records['a/b']['outputs']
    second = records['a b']['outputs']
    assert first == ['a-b/landscape/video.mp4', 'a-b/vertical/video.mp4']
    import hashlib
    suffix = hashlib.sha256(b'a b').hexdigest()[:8]
    assert all(path.startswith(f'a-b-{suffix}/') for path in second)
    gallery = (out / 'index.html').read_text()
    assert gallery.count('<li>') == 2
    assert gallery.count('&lt;Shared title&gt;') == 1
    assert gallery.count('<video ') == 2
    assert gallery.count('MP4 (') == 4
    assert 'a=1&amp;b=2' in gallery
    assert autogen.main(args + ['--formats', 'landscape,vertical']) == 0
    assert len(stub_render) == 4


def test_legacy_state_regenerates_and_missing_output_recovers(setup, stub_render):
    _, out, args = setup
    out.mkdir()
    autogen.atomic_write(out / 'autogen_state.json', json.dumps({'a': dict(
        hash='legacy-render-options', status='done', attempts=1,
        outputs=['item-legacy/a/landscape/video.mp4'])}))
    assert autogen.main(args) == 0
    assert state(out)['a']['outputs'] == ['a/landscape/video.mp4']
    (out / state(out)['a']['outputs'][0]).unlink()
    assert autogen.main(args) == 0
    assert len(stub_render) == 2


def test_atomic_write_and_csv(tmp_out):
    target = tmp_out / 'nested/state.json'
    autogen.atomic_write(target, 'one')
    autogen.atomic_write(target, 'two')
    assert target.read_text() == 'two'
    assert list(target.parent.iterdir()) == [target]
    assert autogen.csv_values('vertical, landscape,vertical') == ['landscape', 'vertical']
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        autogen.csv_values('bogus', ['landscape'])


def test_backend_and_voice_passthrough(setup, monkeypatch):
    _, out, args = setup
    captured = []
    def render(*a, **kwargs):
        captured.append((kwargs['tts'], kwargs['voice']))
        raise ValueError('stub')
    monkeypatch.setattr(autogen.kb_video, 'render_script', render)
    assert autogen.main(args + ['--tts', 'say', '--voice', 'Tingting']) == 1
    digest = state(out)['a']['hash']
    assert autogen.main(args + ['--tts', 'edge', '--voice', 'zh-CN-YunyangNeural']) == 1
    assert state(out)['a']['hash'] != digest
    assert captured == [('say', 'Tingting'), ('edge', 'zh-CN-YunyangNeural')]


def test_interrupted_format_resumes_completed_output(setup, stub_render, monkeypatch):
    _, out, args = setup
    real = autogen.kb_video.render_script
    calls = 0

    def interrupt_second(*call_args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real(*call_args, **kwargs)

    monkeypatch.setattr(autogen.kb_video, 'render_script', interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        autogen.main(args + ['--formats', 'landscape,vertical'])
    interrupted = state(out)['a']
    assert interrupted['status'] == 'running'
    assert interrupted['completed_formats'].keys() == {'landscape'}

    monkeypatch.setattr(autogen.kb_video, 'render_script', real)
    assert autogen.main(args + ['--formats', 'landscape,vertical']) == 0
    assert state(out)['a']['status'] == 'done'
    assert len(stub_render) == 2
