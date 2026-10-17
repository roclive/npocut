import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
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

test('HTTP offline library, generation, preview ranges and traversal', async () => {
  const { createAppServer } = await import('../server.js');
  const dir = await mkdtemp(path.join(tmpdir(), 'kb-api-'));
  const server = createAppServer({ workDir: dir, cliRoot: dir, python: process.env.NPOCUT_PYTHON || 'python3' });
  try {
    await writeFile(path.join(dir, 'kb_video.py'), `import pathlib,sys\nout=pathlib.Path(sys.argv[sys.argv.index('--out')+1])\np=out/'result.mp4'\np.write_bytes(b'0123456789')\nprint(p)\n`);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const post = (url, value) => fetch(base + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
    const library = await (await post('/api/kb/library', { library: [{ id: 'article', title: '<script>', summary: 'hello', key_points: ['one'] }] })).json();
    assert.equal(library.library[0].key_points_count, 1);
    const started = await post('/api/kb/generate', { id: 'article', source: library.source, vertical: true, lang: 'ja' });
    assert.equal(started.status, 202);
    const { id } = await started.json();
    let job;
    for (let n = 0; n < 100; n++) {
      job = await (await fetch(`${base}/api/kb/jobs/${id}`)).json();
      if (job.status !== 'running') break;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(job.status, 'done'); assert.deepEqual(job.outputs, ['result.mp4']);
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
