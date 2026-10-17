import json
import os
from pathlib import Path
import sys
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
