import copy
import json
from pathlib import Path

import pytest

import kb_images
from kb_source import build_script, load_library
from kb_scenes import build_scene_plan, render_scene_plan, main
from kb_templates import build_template_script, template_names


@pytest.fixture
def item():
    return load_library(Path(__file__).parent / 'fixtures/ontokb_reading.json')[0]


def test_fixture_plan_is_repeatable_and_source_backed(item):
    script = build_script(item)
    plan = build_scene_plan(item, script)
    assert plan == build_scene_plan(item, script)
    assert len(plan['scenes']) == len(script['segments'])
    for scene, segment in zip(plan['scenes'], script['segments']):
        assert scene['narration']['text'] == segment['narration']
        assert scene['source']['url'] == item['url']
        assert scene['source']['original_text']
        assert scene['duration'] > 0
        assert 'No text or Chinese title characters' in scene['prompt']
        assert 'Chinese characters' in scene['negative_prompt']
        assert isinstance(scene['seed'], int)
    durations = [3] * len(script['segments'])
    measured = build_scene_plan(item, script, durations=durations)
    assert measured['total_duration'] == 3 * len(durations)
    assert measured['scenes'][1]['start'] == 3
    bad = copy.deepcopy(script)
    bad['segments'][1]['narration'] = '无来源的断言'
    with pytest.raises(ValueError, match='source text'):
        build_scene_plan(item, bad)


def test_cache_hit_content_invalidation_and_missing_image(item, tmp_path, monkeypatch):
    calls = []
    original = kb_images.generate

    def generate(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(kb_images, 'generate', generate)
    script = build_script(item, max_points=0)
    plan = build_scene_plan(item, script)
    first = render_scene_plan(plan, tmp_path)
    count = len(calls)
    second = render_scene_plan(plan, tmp_path)
    assert len(calls) == count
    assert all(s['image']['cache_hit'] for s in second['scenes'])
    Path(first['scenes'][0]['image']['path']).unlink()
    repaired = render_scene_plan(plan, tmp_path)
    assert len(calls) == count + 1
    assert not repaired['scenes'][0]['image']['cache_hit']
    changed = dict(item, summary=item['summary'] + '新增来源内容。')
    updated = render_scene_plan(build_scene_plan(changed, script), tmp_path)
    assert all(not s['image']['cache_hit'] for s in updated['scenes'])
    assert first['scenes'][0]['image']['cache_key'] != updated['scenes'][0]['image']['cache_key']
    assert json.loads((tmp_path / 'scene-manifest.json').read_text()) == updated


def test_no_credentials_falls_back_per_scene_offline(item, tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('network used'))
    plan = build_scene_plan(item, build_script(item, max_points=0))
    manifest = render_scene_plan(plan, tmp_path, config=kb_images.Config('openai'))
    for scene in manifest['scenes']:
        asset = scene['image']
        assert asset['provider'] == 'gradient'
        assert not asset['ai_generated'] and not asset['seed_applied']
        assert 'missing' in asset['blocked_reason']
        assert Path(asset['path']).is_file()
    again = render_scene_plan(plan, tmp_path, config=kb_images.Config('openai'))
    assert all(not s['image']['cache_hit'] for s in again['scenes'])


def test_failure_isolated_and_cache_parameters(item, tmp_path, monkeypatch):
    plan = build_scene_plan(item, build_script(item, max_points=0))
    original = kb_images.generate
    calls = []

    def fail_one(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('unavailable')
        return original(*args, **kwargs)

    monkeypatch.setattr(kb_images, 'generate', fail_one)
    manifest = render_scene_plan(plan, tmp_path)
    assert manifest['scenes'][0]['image']['path'] is None
    assert all(s['image']['path'] for s in manifest['scenes'][1:])
    for field, value in [('seed', 42), ('prompt', 'different'), ('negative_prompt', 'different')]:
        changed = copy.deepcopy(plan)
        changed['scenes'][1][field] = value
        result = render_scene_plan(changed, tmp_path)
        assert result['scenes'][1]['image']['cache_key'] != manifest['scenes'][1]['image']['cache_key']


def test_plan_cli(item, tmp_path):
    snapshot = Path(__file__).parent / 'fixtures/ontokb_reading.json'
    assert main([item['id'], '--from-json', str(snapshot), '--out', str(tmp_path), '--plan-only']) == 0
    assert json.loads((tmp_path / 'scene-manifest.json').read_text())['scenes']


def test_corrupt_cache_and_model_change(item, tmp_path):
    plan = build_scene_plan(item, build_script(item, max_points=0))
    first = render_scene_plan(plan, tmp_path)
    path = Path(first['scenes'][0]['image']['path'])
    path.write_bytes(b'broken')
    path.with_suffix('.json').write_text('[]')
    repaired = render_scene_plan(plan, tmp_path)
    assert not repaired['scenes'][0]['image']['cache_hit']
    assert path.read_bytes().startswith(b'\x89PNG')
    other = render_scene_plan(plan, tmp_path, config=kb_images.Config(model='another-model'))
    assert all(not s['image']['cache_hit'] for s in other['scenes'])
    with pytest.raises(ValueError, match='512x512'):
        build_scene_plan(item, size=(1024, 1024))
    with pytest.raises(ValueError, match='durations'):
        build_scene_plan(item, durations=[float('nan')])


def test_three_finished_templates_are_distinct_source_backed_and_bounded(item):
    plans = {}
    for name in template_names():
        script = build_template_script(item, name)
        plan = build_scene_plan(item, script, aspect_ratio='9:16')
        plans[name] = plan
        assert plan['template']['id'] == name
        assert plan['aspect_ratio'] == '9:16'
        assert plan['total_duration'] <= plan['template']['max_duration']
        narration = [s['narration']['text'] for s in plan['scenes']]
        assert len(narration) == len(set(narration))
        assert plan['scenes'][-1]['source']['editorial']
        assert plan['scenes'][-1]['source']['url'] == item['url']
    assert len({tuple(s['narration']['text'] for s in p['scenes'])
                for p in plans.values()}) == 3
    assert plans['vertical-short']['scenes'][0]['theme'] == '关键数据'


def test_template_validation_and_legacy_preserved(item):
    assert build_scene_plan(item)['template']['id'] == 'legacy'
    with pytest.raises(ValueError, match='Unknown template'):
        build_scene_plan(item, template='loud-clickbait')
    with pytest.raises(ValueError, match='aspect_ratio'):
        build_scene_plan(item, aspect_ratio='1:1')
