"""Bounded image providers. doctor is offline; smoke makes exactly one API request."""
import argparse
import base64
from dataclasses import dataclass
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import time
from typing import Protocol
import urllib.error
import urllib.request
import zlib


class ImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    provider: str = 'gradient'
    model: str = 'gpt-image-2.5-flare'
    timeout: float = 180

    @classmethod
    def from_env(cls):
        return cls(os.getenv('KB_IMAGE_PROVIDER', 'gradient'),
                   os.getenv('KB_IMAGE_MODEL', 'gpt-image-2.5-flare'),
                   float(os.getenv('KB_IMAGE_TIMEOUT', '180')))


class Provider(Protocol):
    def render(self, prompt: str, output: Path) -> dict: ...


def _chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))


class GradientProvider:
    """Offline PNG gradient with abstract editorial panels; no dependencies."""
    def render(self, prompt, output):
        rows = bytearray()
        accent = hashlib.sha256(prompt.encode()).digest()[0]
        for y in range(512):
            rows.append(0)
            for x in range(512):
                panel = 48 <= x < 464 and (320 <= y < 332 or 356 <= y < 364 or 388 <= y < 396)
                rows.extend((235, 240, 244) if panel else
                            (int(18 + y / 16), int(35 + x / 12), int(65 + accent / 4 + y / 10)))
        png = b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('!2I5B', 512, 512, 8, 2, 0, 0, 0))
        png += _chunk(b'IDAT', zlib.compress(rows)) + _chunk(b'IEND', b'')
        output.write_bytes(png)
        # Editable text layout avoids font dependencies and supports CJK in browsers.
        from html import escape
        output.with_suffix('.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">'
            '<defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#122341"/>'
            '<stop offset="1" stop-color="#526784"/></linearGradient></defs>'
            '<rect width="512" height="512" fill="url(#g)"/>'
            '<text x="40" y="70" fill="#a8dcca" font-size="16">NPO / KNOWLEDGE</text>'
            + ''.join(f'<text x="40" y="{160+i*38}" fill="white" font-size="26">{escape(prompt[i*16:(i+1)*16])}</text>' for i in range(6))
            + '</svg>', encoding='utf-8')
        return {'provider': 'gradient', 'ai_generated': False, 'model': None,
                'width': 512, 'height': 512, 'layout': str(output.with_suffix('.svg'))}


class OpenAIProvider:
    def __init__(self, config):
        self.config = config

    def render(self, prompt, output):
        key = os.getenv('OPENAI_API_KEY')
        if not key:
            raise ImageError('OPENAI_API_KEY is missing')
        try:
            from PIL import Image
        except ImportError:
            raise ImageError('Pillow missing; install requirements-images.txt in .venv-images') from None
        body = json.dumps({'model': self.config.model, 'prompt': prompt, 'n': 1,
                           'size': '1024x1024', 'quality': 'low', 'output_format': 'png'}).encode()
        request = urllib.request.Request('https://api.openai.com/v1/images/generations', data=body,
                                        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read(24 * 1024 * 1024 + 1)
                if len(raw) > 24 * 1024 * 1024:
                    raise ImageError('API response exceeds 24 MiB limit')
                result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ImageError('Invalid JSON image response') from None
        except urllib.error.HTTPError as error:
            # Do not echo response bodies, which can contain credentials or prompts.
            raise ImageError(f'OpenAI HTTP {error.code}; check account access, billing and model availability') from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead):
            raise ImageError('OpenAI connection failed or timed out; no automatic retry') from None
        try:
            image_bytes = base64.b64decode(result['data'][0]['b64_json'], validate=True)
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.size != (1024, 1024):
                    raise ValueError('unexpected dimensions')
                image.load()
                image.convert('RGB').resize((512, 512), Image.Resampling.LANCZOS).save(output, format='PNG')
        except (KeyError, IndexError, TypeError, ValueError, OSError):
            raise ImageError('Invalid image response; expected base64 1024x1024 image') from None
        return {'provider': 'openai', 'ai_generated': True, 'model': self.config.model,
                'width': 512, 'height': 512, 'source_width': 1024, 'source_height': 1024,
                'download_bytes': len(image_bytes), 'model_download_bytes': 0,
                'license': 'Hosted proprietary model; OpenAI API service terms apply; no weights distributed',
                'usage': result.get('usage')}


def generate(prompt, output, config=None, fallback=False):
    config = config or Config.from_env()
    if not prompt.strip() or len(prompt) > 4000:
        raise ValueError('prompt must contain 1–4000 characters')
    if config.provider not in ('openai', 'gradient'):
        raise ValueError('unknown image provider')
    if not 0 < config.timeout <= 300:
        raise ValueError('timeout must be between 0 and 300 seconds')
    output = Path(output)
    if output.suffix.lower() != '.png':
        raise ValueError('output must end in .png')
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        result = (OpenAIProvider(config) if config.provider == 'openai' else GradientProvider()).render(prompt, output)
    except ImageError as error:
        if not fallback:
            raise
        result = GradientProvider().render(prompt, output)
        result['blocked_reason'] = str(error)
    result.update(path=str(output), seconds=round(time.monotonic() - started, 3),
                  sha256=hashlib.sha256(output.read_bytes()).hexdigest(), bytes=output.stat().st_size)
    output.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def doctor(config):
    import importlib.util
    memory = None
    if platform.system() == 'Darwin':
        memory = int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True))
    pillow = importlib.util.find_spec('PIL') is not None
    return {'platform': platform.platform(), 'architecture': platform.machine(), 'memory_bytes': memory,
            'provider': config.provider, 'model': config.model, 'api_key_present': bool(os.getenv('OPENAI_API_KEY')),
            'pillow_installed': pillow, 'network_checked': False,
            'ready': config.provider == 'gradient' or (config.provider == 'openai' and pillow and bool(os.getenv('OPENAI_API_KEY'))),
            'limits': {'requests_per_smoke': 1, 'automatic_retries': 0, 'images': 1, 'size': '1024x1024 → 512x512', 'response_bytes': 25165824}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'smoke', 'generate'])
    parser.add_argument('--provider', choices=['gradient', 'openai'])
    parser.add_argument('--prompt', default='Editorial watercolor illustration of a small community library, warm sunlight, no lettering')
    parser.add_argument('--output', default='kb_out/images/smoke.png')
    parser.add_argument('--fallback', action='store_true', help='explicitly allow non-AI fallback on provider failure')
    args = parser.parse_args()
    config = Config.from_env()
    if args.provider:
        config = Config(args.provider, config.model, config.timeout)
    try:
        result = doctor(config) if args.command == 'doctor' else generate(args.prompt, args.output, config, args.fallback)
    except (ImageError, ValueError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get('blocked_reason') else 0


if __name__ == '__main__':
    raise SystemExit(main())
