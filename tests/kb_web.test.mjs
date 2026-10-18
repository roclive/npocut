import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm, readdir, readFile, utimes, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { safeKbFile, createJobs } from '../kb_web.js';

test('file guard blocks traversal, absolute paths and symlink escapes', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'kb-web-'));
  try {
    const root = path.join(dir, 'kb_out'); await mkdir(root);
    await writeFile(path.join(root, 'video.mp4'), 'test');
    await writeFile(path.join(dir, 'secret'), 'secret');
    await symlink(path.join(dir, 'secret'), path.join(root, 'link'));
    assert.equal(await safeKbFile(root, 'video.mp4'), path.join(await import('node:fs/promises').then(fs => fs.realpath(root)), 'video.mp4'));
    for (const value of ['../secret', '/etc/passwd', 'link', '..\\secret', 'video.mp4\0', '', null]) await assert.rejects(safeKbFile(root, value));
  } finally { await rm(dir, { recursive: true, force: true }); }
});
test('jobs handle success, nonzero exit, spawn error and bounded log', () => {
  const jobs = createJobs();
  const child = () => Object.assign(new EventEmitter(), { stdout: new EventEmitter(), stderr: new EventEmitter() });
  const good = child(), job = jobs.start(good);
  assert.equal(jobs.get(job.id).status, 'running');
  good.stdout.emit('data', 'x'.repeat(20000)); assert.equal(job.log.length, 16000);
  good.emit('close', 0); assert.equal(job.status, 'done');
  const bad = child(), failed = jobs.start(bad); bad.emit('close', 1); assert.equal(failed.status, 'failed');
  const missing = child(), error = jobs.start(missing); missing.emit('error', new Error('ENOENT')); missing.emit('close', 0); assert.equal(error.status, 'failed');
});

test('jobs expose the latest structured render phase even when a marker is split across chunks', () => {
  const jobs = createJobs();
  const child = Object.assign(new EventEmitter(), { stdout: new EventEmitter(), stderr: new EventEmitter() });
  const job = jobs.start(child);
  child.stderr.emit('data', 'NPOCUT_PHASE:narration\n');
  assert.equal(job.phase, 'narration');
  child.stderr.emit('data', 'NPOCUT_PHASE:ima');
  child.stderr.emit('data', 'ges\nNPOCUT_PHASE:combine\n');
  assert.equal(job.phase, 'combine');
});

test('frontend search stringifies non-text OntoKB fields and uses the structured phase', async () => {
  const source = await readFile(new URL('../web/kb.js', import.meta.url), 'utf8');
  assert.match(source, /String\(item\.title \?\? ''\)\.toLowerCase\(\)/);
  assert.match(source, /String\(item\.summary \?\? ''\)\.toLowerCase\(\)/);
  assert.match(source, /\[job\.phase\]/);
  assert.doesNotMatch(source, /job\.log\.includes\(['"]Narration/);
});

test('configuration guidance names the implemented OpenAI settings and never the removed SD URL', async () => {
  const source = await readFile(new URL('../web/kb.js', import.meta.url), 'utf8');
  assert.match(source, /KB_IMAGE_PROVIDER=openai/);
  assert.match(source, /OPENAI_API_KEY/);
  assert.match(source, /requirements-images\.txt/);
  assert.doesNotMatch(source, /ONTOKB_IMAGE_URL|Stable Diffusion/);
});

test('frontend uses the image doctor endpoint instead of a timed ready state', async () => {
  const source = await readFile(new URL('../web/kb.js', import.meta.url), 'utf8');
  assert.match(source, /api\('\/api\/kb\/image-status'\)/);
  assert.doesNotMatch(source, /setTimeout\(\(\) => \{\s*imageStatus\.textContent = '就绪'/);
});

test('style preview exposes every supported backend style', async () => {
  const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  const source = await readFile(new URL('../web/kb.js', import.meta.url), 'utf8');
  for (const style of ['editorial', 'paper', 'contrast']) assert.match(html, new RegExp(`value="${style}"`));
  assert.match(html, /id="kbStylePreview"/);
  assert.match(source, /style: styleSelect\.value/);
});

test('format controls include aspect and quality and submit both', async () => {
  const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  const source = await readFile(new URL('../web/kb.js', import.meta.url), 'utf8');
  assert.match(html, /id="kbAspect"/); assert.match(html, /id="kbQuality"/);
  assert.match(html, /value="1920x1080"/); assert.match(html, /value="1280x720"/);
  assert.match(source, /vertical: aspectSelect\.value === 'vertical'/);
  assert.match(source, /size: qualitySelect\.value/);
});

test('HTTP offline library, generation, preview ranges and traversal', async () => {
  const { createAppServer } = await import('../server.js');
  const dir = await mkdtemp(path.join(tmpdir(), 'kb-api-'));
  const server = createAppServer({ workDir: dir, cliRoot: dir, python: process.env.NPOCUT_PYTHON || 'python3' });
  try {
    await writeFile(path.join(dir, 'kb_voices.py'), `import json, pathlib, sys
p=pathlib.Path('voice_calls.txt')
p.write_text(p.read_text()+'x' if p.exists() else 'x')
print(json.dumps({'edge':[{'name':'ja-JP-KeitaNeural','label':'Keita','gender':'Male'}], 'say':[{'name':'Kyoko','label':'Kyoko'}], 'defaults':{'edge':'ja-JP-KeitaNeural','say':'Kyoko'}}))
`);
    await writeFile(path.join(dir, 'kb_video.py'), `import pathlib,sys\nout=pathlib.Path(sys.argv[sys.argv.index('--out')+1])\np=out/'result.mp4'\np.write_bytes(b'0123456789')\n(out/'args.json').write_text(__import__('json').dumps(sys.argv))\n(out/'manifest.json').write_text(__import__('json').dumps({'tts_backend':'say','voice':'Kyoko','fallback_reason':'offline'}))\nprint(p)\n`);
    await writeFile(path.join(dir, 'kb_images.py'), `import json\nprint(json.dumps({'ready':False,'provider':'openai','model':'test-model','api_key_present':False,'pillow_installed':True,'network_checked':False}))\n`);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const post = (url, value) => fetch(base + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
    const imageStatus = await (await fetch(base + '/api/kb/image-status')).json();
    assert.deepEqual(imageStatus, { ready:false, provider:'openai', model:'test-model', api_key_present:false, pillow_installed:true, network_checked:false });
    const library = await (await post('/api/kb/library', { library: [{ id: 'article', title: '<script>', summary: 'hello', key_points: ['one'] }] })).json();
    assert.equal(library.library[0].key_points_count, 1);
    assert.match(library.source, /^web-source-[a-f0-9]{16}\.json$/);
    const snapshotPath = path.join(dir, 'kb_out', library.source);
    const content = await readFile(snapshotPath, 'utf8');
    assert.equal(library.source, `web-source-${createHash('sha256').update(content).digest('hex').slice(0, 16)}.json`);
    const oldTime = new Date(Date.now() - 60000);
    await utimes(snapshotPath, oldTime, oldTime);
    const before = (await stat(snapshotPath)).mtimeMs;
    const again = await (await post('/api/kb/library', JSON.parse(content))).json();
    assert.equal(again.source, library.source);
    assert.equal((await stat(snapshotPath)).mtimeMs, before);
    assert.equal((await readdir(path.join(dir, 'kb_out'))).filter(name => name.startsWith('web-source-')).length, 1);
    const different = await (await post('/api/kb/library', { library: [{ id: 'other' }] })).json();
    assert.notEqual(different.source, library.source);
    assert.equal((await readdir(path.join(dir, 'kb_out'))).filter(name => name.startsWith('web-source-')).length, 2);
    const unusual = await (await post('/api/kb/library', { library: [{ id: 7, title: 42, summary: ['hello'], kind: { type: 'note' } }] })).json();
    assert.deepEqual(unusual.library[0], { id: '7', title: '42', summary: 'hello', kind: '[object Object]', key_points_count: 0, autogen: null });
    for (const source of ['web-source-1234.json', 'web-source-0123456789abcdef0.json', 'web-source-01234567-89ab-cdef.json', 'web-source-ABCDEF0123456789.json', '../' + library.source, 'reading.json']) {
      await writeFile(path.join(dir, 'kb_out', path.basename(source)), '{}');
      const rejected = await post('/api/kb/generate', { id: 'article', source });
      assert.equal(rejected.status, 400);
      assert.equal((await rejected.json()).error, 'Invalid snapshot');
    }
    for (const invalid of [{tts:'invalid'}, {tts:null}, {tts:'auto',voice:'Tingting'}, {tts:'say',voice:'ja-JP-KeitaNeural'}, {voice:''}, {size:'4k'}, {style:'neon'}]) {
      assert.equal((await post('/api/kb/generate', {id:'article', lang:'ja', ...invalid})).status, 400);
    }
    assert.equal((await fetch(base + '/api/kb/voices?lang=bad')).status, 400);
    const catalog = await (await fetch(base + '/api/kb/voices?lang=ja')).json();
    assert.equal(catalog.edge[0].name, 'ja-JP-KeitaNeural');
    await fetch(base + '/api/kb/voices?lang=ja');
    assert.equal(await readFile(path.join(dir, 'voice_calls.txt'), 'utf8'), 'x');
    const started = await post('/api/kb/generate', { id: 'article', source: library.source, vertical: true, size: '1280x720', style: 'paper', lang: 'ja' });
    assert.equal(started.status, 202);
    const { id } = await started.json();
    let job;
    for (let n = 0; n < 100; n++) {
      job = await (await fetch(`${base}/api/kb/jobs/${id}`)).json();
      if (job.status !== 'running') break;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(job.status, 'done'); assert.deepEqual(job.outputs, ['result.mp4']);
    assert.equal(job.tts_backend, 'say');
    assert.equal(job.voice, 'Kyoko');
    assert.equal(job.fallback_reason, 'offline');
    const args = JSON.parse(await readFile(path.join(dir, 'kb_out/args.json'), 'utf8'));
    assert.equal(args[args.indexOf('--tts') + 1], 'auto');
    assert.equal(args[args.indexOf('--voice') + 1], 'ja-JP-KeitaNeural');
    assert.equal(args[args.indexOf('--size') + 1], '1280x720');
    assert.equal(args[args.indexOf('--style') + 1], 'paper');
    for (const [tts, voice] of [['edge', 'ja-JP-KeitaNeural'], ['say', 'Kyoko']]) {
      const response = await post('/api/kb/generate', {id:'article', source:library.source, lang:'ja', tts, voice});
      assert.equal(response.status, 202);
      const next = await response.json();
      for (let n = 0; n < 100; n++) {
        const current = await (await fetch(`${base}/api/kb/jobs/${next.id}`)).json();
        if (current.status !== 'running') { assert.equal(current.status, 'done'); break; }
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      const passed = JSON.parse(await readFile(path.join(dir, 'kb_out/args.json'), 'utf8'));
      assert.equal(passed[passed.indexOf('--tts') + 1], tts);
      assert.equal(passed[passed.indexOf('--voice') + 1], voice);
    }
    const head = await fetch(base + '/api/kb/file?path=result.mp4', { method: 'HEAD' });
    assert.equal(head.headers.get('content-length'), '10'); assert.equal(await head.text(), '');
    let saved;
    for (let n = 0; n < 50; n++) {
      saved = await (await post('/api/kb/library', { library: [{ id: 'article' }] })).json();
      if (saved.library[0].autogen?.status === 'done') break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.deepEqual(saved.library[0].autogen.outputs, ['result.mp4']);
    for (const [range, expected] of [['bytes=2-5', '2345'], ['bytes=-3', '789'], ['bytes=7-', '789']]) {
      const response = await fetch(base + '/api/kb/file?path=result.mp4', { headers: { Range: range } });
      assert.equal(response.status, 206); assert.equal(await response.text(), expected);
    }
    assert.equal((await fetch(base + '/api/kb/file?path=result.mp4', { headers: { Range: 'bytes=99-' } })).status, 416);
    assert.equal((await fetch(base + '/api/kb/file?path=..%2Fsecret')).status, 403);
    assert.equal((await fetch(base + '/api/kb/jobs/missing')).status, 404);
    assert.equal((await post('/api/kb/generate', { id: 'x', lang: 'bad' })).status, 400);
  } finally { await new Promise(resolve => server.close(resolve)); await rm(dir, { recursive: true, force: true }); }
});

test('startup cleanup removes only snapshot files older than seven days', async () => {
  const { createKbHandler } = await import('../kb_web.js');
  const dir = await mkdtemp(path.join(tmpdir(), 'kb-cleanup-'));
  const out = path.join(dir, 'kb_out');
  try {
    await mkdir(out);
    const stale = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000);
    const recent = new Date(Date.now() - 6 * 24 * 60 * 60 * 1000);
    for (const name of ['web-source-0123456789abcdef.json', 'web-source-old-uuid.json', 'reading.json', 'web_state.json', 'web-source-old.txt', 'prefix-web-source-old.json']) {
      await writeFile(path.join(out, name), '{}');
      await utimes(path.join(out, name), stale, stale);
    }
    await writeFile(path.join(out, 'web-source-recent.json'), '{}');
    await utimes(path.join(out, 'web-source-recent.json'), recent, recent);
    await mkdir(path.join(out, 'web-source-directory.json'));
    await utimes(path.join(out, 'web-source-directory.json'), stale, stale);
    const handler = createKbHandler({ workDir: dir });
    await handler({ url: '/api/kb/jobs/missing', method: 'GET' }, { writeHead() {}, end() {} });
    assert.deepEqual((await readdir(out)).sort(), ['prefix-web-source-old.json', 'reading.json', 'web-source-directory.json', 'web-source-old.txt', 'web-source-recent.json', 'web_state.json'].sort());
  } finally { await rm(dir, { recursive: true, force: true }); }
});
