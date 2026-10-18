import base64
import http.client
import io
import json
from pathlib import Path
import urllib.error

import pytest

from kb_images import Config, ImageError, doctor, generate


def test_gradient_without_dependencies(tmp_path):
    path = tmp_path / 'offline.png'
    result = generate('社区图书馆 <安全>', path, Config())
    assert result['ai_generated'] is False
    assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert '&lt;安全&gt;' in path.with_suffix('.svg').read_text()
    assert json.loads(path.with_suffix('.json').read_text())['sha256'] == result['sha256']


def test_missing_key_is_explicit_and_fallback_is_marked(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(ImageError, match='missing'):
        generate('library', tmp_path / 'fail.png', Config('openai'))
    result = generate('library', tmp_path / 'fallback.png', Config('openai'), fallback=True)
    assert not result['ai_generated']
    assert 'missing' in result['blocked_reason']
    assert doctor(Config('openai'))['ready'] is False


def test_api_contract_and_decoding(tmp_path, monkeypatch):
    Image = pytest.importorskip('PIL.Image')
    buf = io.BytesIO()
    Image.new('RGB', (1024, 1024), 'green').save(buf, format='PNG')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    calls = []

    def request(req, timeout):
        calls.append(json.loads(req.data))
        assert req.full_url == 'https://api.openai.com/v1/images/generations'
        return io.BytesIO(json.dumps({
            'data': [{'b64_json': base64.b64encode(buf.getvalue()).decode()}],
            'usage': {'input_tokens': 20, 'output_tokens': 196, 'total_tokens': 216},
        }).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    result = generate('library', tmp_path / 'api.png', Config('openai'))
    assert len(calls) == 1
    assert calls[0]['n'] == 1 and calls[0]['quality'] == 'low'
    assert calls[0]['model'] == 'gpt-image-2.5-flare'
    assert calls[0]['output_format'] == 'png'
    assert 'response_format' not in calls[0]
    assert result['usage'] == {'input_tokens': 20, 'output_tokens': 196, 'total_tokens': 216}
    assert Image.open(result['path']).size == (512, 512)


def test_api_failure_no_retry_or_secret_leak(tmp_path, monkeypatch):
    pytest.importorskip('PIL')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    calls = []

    def fail(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 401, 'secret-test-key', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(ImageError, match='HTTP 401') as error:
        generate('library', tmp_path / 'fail.png', Config('openai'))
    assert 'secret-test-key' not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize('fallback', [False, True])
def test_interrupted_response_uses_explicit_fallback(tmp_path, monkeypatch, fallback):
    Image = pytest.importorskip('PIL.Image')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    calls = []

    class InterruptedResponse(io.BytesIO):
        def read(self, size=-1):
            raise http.client.IncompleteRead(b'secret-test-key', 100)

    def request(req, timeout):
        calls.append(req)
        return InterruptedResponse()

    monkeypatch.setattr('urllib.request.urlopen', request)
    path = tmp_path / 'interrupted.png'
    if fallback:
        result = generate('library', path, Config('openai'), fallback=True)
        assert result['provider'] == 'gradient'
        assert result['ai_generated'] is False
        assert 'connection failed' in result['blocked_reason']
        assert 'secret-test-key' not in result['blocked_reason']
        with Image.open(path) as image:
            assert image.size == (512, 512)
            image.verify()
        assert path.with_suffix('.svg').is_file()
        assert json.loads(path.with_suffix('.json').read_text()) == result
    else:
        with pytest.raises(ImageError, match='connection failed') as error:
            generate('library', path, Config('openai'))
        assert 'secret-test-key' not in str(error.value)
        assert not path.exists()
        assert not path.with_suffix('.json').exists()
    assert len(calls) == 1


@pytest.mark.parametrize('config,prompt,filename', [
    (Config('unknown'), 'x', 'x.png'), (Config(), '', 'x.png'),
    (Config(timeout=0), 'x', 'x.png'), (Config(), 'x', 'x.jpg'),
])
def test_invalid_input(tmp_path, config, prompt, filename):
    with pytest.raises(ValueError):
        generate(prompt, tmp_path / filename, config)
