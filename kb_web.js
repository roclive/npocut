import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, realpath, stat, readdir, lstat, unlink } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import path from 'node:path';

export async function safeKbFile(root, name) {
  if (typeof name !== 'string' || !name || name.includes('\0') || name.includes('\\') || path.isAbsolute(name) || name.split('/').includes('..')) throw new Error('Forbidden path');
  const base = await realpath(root);
  const target = await realpath(path.resolve(base, name));
  if (!target.startsWith(base + path.sep)) throw new Error('Forbidden path');
  return target;
}
export function createJobs() {
  const jobs = new Map();
  return {
    get: id => jobs.get(id),
    start(child, detail = {}) {
      const job = { id: randomUUID(), ...detail, status: 'running', log: '', outputs: [] };
      jobs.set(job.id, job);
      const append = chunk => {
        job.log = (job.log + chunk.toString()).slice(-16000);
        const phases = [...job.log.matchAll(/(?:^|\n)NPOCUT_PHASE:(narration|images|combine)(?=\r?$)/gm)];
        if (phases.length) job.phase = phases.at(-1)[1];
      };
      child.stdout?.on('data', append); child.stderr?.on('data', append);
      child.once('error', error => { append(error.message); job.status = 'failed'; });
      child.once('close', code => { if (job.status === 'running') job.status = code === 0 ? 'done' : 'failed'; });
      return job;
    },
  };
}
export function createKbHandler({ workDir, cliRoot = workDir, python = process.env.NPOCUT_PYTHON || 'python3', env = process.env }) {
  const out = path.join(workDir, 'kb_out');
  const jobs = createJobs();
  const cleanup = (async () => {
    await mkdir(out, { recursive: true });
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    for (const name of await readdir(out)) {
      if (!/^web-source-.*\.json$/.test(name)) continue;
      const file = path.join(out, name);
      try {
        const info = await lstat(file);
        if (info.isFile() && info.mtimeMs < cutoff) await unlink(file);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  })().catch(error => { console.warn(`Could not clean KB snapshots: ${error.message}`); });
  let saving = Promise.resolve();
  const voiceCache = new Map();
  async function voices(lang) {
    if (!['zh', 'ja', 'en'].includes(lang)) throw new Error('Invalid language');
    const cached = voiceCache.get(lang);
    if (cached && Date.now() - cached.time < 600000) return cached.value;
    const value = await new Promise((resolve, reject) => {
      const child = spawn(python, [path.join(cliRoot, 'kb_voices.py'), '--lang', lang], { cwd: workDir, env });
      let output = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error('Voice listing timed out')); }, 20000);
      child.stdout.on('data', chunk => { output += chunk; });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('close', code => {
        clearTimeout(timer);
        try {
          if (code !== 0) throw new Error('Could not list voices');
          const result = JSON.parse(output);
          if (!Array.isArray(result.edge) || !Array.isArray(result.say) || !result.defaults) throw new Error('Invalid voice listing');
          resolve(result);
        } catch (error) { reject(error); }
      });
    });
    voiceCache.set(lang, { time: Date.now(), value });
    return value;
  }
  async function imageDoctor() {
    return new Promise((resolve, reject) => {
      const child = spawn(python, [path.join(cliRoot, 'kb_images.py'), 'doctor'], { cwd: workDir, env });
      let output = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error('Image backend check timed out')); }, 10000);
      child.stdout.on('data', chunk => { output += chunk; });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('close', code => {
        clearTimeout(timer);
        try {
          if (code !== 0) throw new Error('Image backend check failed');
          const result = JSON.parse(output);
          resolve({ ready: !!result.ready, provider: result.provider, model: result.model,
                    api_key_present: !!result.api_key_present, pillow_installed: !!result.pillow_installed,
                    network_checked: false });
        } catch (error) { reject(error); }
      });
    });
  }
  const templates = new Set(['knowledge-brief', 'concept-explainer', 'vertical-short']);
  async function snapshot(value) {
    if (!/^web-source-[a-f0-9]{16}\.json$/.test(value || '')) throw new Error('Invalid snapshot');
    return safeKbFile(out, value);
  }
  function storyboardLocation(data) {
    if (!templates.has(data.template)) throw new Error('Invalid storyboard template');
    const aspect = data.vertical ? '9:16' : '16:9';
    const key = createHash('sha256').update(`${data.source}\0${data.id}\0${data.template}\0${aspect}`).digest('hex').slice(0, 16);
    return { aspect, relative: `storyboards/${key}/scene-manifest.json`, directory: path.join(out, 'storyboards', key) };
  }
  async function publicStoryboard(relative) {
    const target = await safeKbFile(out, relative);
    const manifest = JSON.parse(await readFile(target, 'utf8'));
    return {
      template: manifest.template,
      aspect_ratio: manifest.aspect_ratio,
      total_duration: manifest.total_duration,
      manifest: relative,
      scenes: (manifest.scenes || []).map((scene, index) => {
        const value = scene.image?.path;
        let image = null;
        if (value) {
          const absolute = path.resolve(value);
          const candidate = path.relative(out, absolute);
          if (candidate && !candidate.startsWith('..') && !path.isAbsolute(candidate)) image = candidate;
        }
        return { index, id: scene.id, theme: scene.theme, duration: scene.duration,
                 narration: scene.narration?.text || '', image,
                 ai_generated: !!scene.image?.ai_generated,
                 cache_hit: !!scene.image?.cache_hit,
                 blocked_reason: scene.image?.blocked_reason || null };
      }),
    };
  }
  function storyboardArgs(data, location, planOnly = false) {
    const args = [path.join(cliRoot, 'kb_scenes.py'), data.id, '--from-json', data.snapshot,
                  '--out', location.directory, '--template', data.template,
                  '--aspect-ratio', location.aspect];
    if (planOnly) args.push('--plan-only');
    if (data.scene !== undefined) args.push('--scene', String(data.scene + 1), '--force-scene');
    return args;
  }
  function runCommand(args, timeout = 20000) {
    return new Promise((resolve, reject) => {
      const child = spawn(python, args, { cwd: workDir, env: { ...env, PYTHONUNBUFFERED: '1' } });
      let stderr = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error('Storyboard planning timed out')); }, timeout);
      child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000); });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('close', code => {
        clearTimeout(timer);
        if (code === 0) resolve();
        else reject(new Error(stderr.trim().split(/\r?\n/).at(-1) || 'Storyboard planning failed'));
      });
    });
  }
  const json = (res, code, data) => { res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); };
  async function body(req) {
    let text = '';
    for await (const chunk of req) { text += chunk; if (text.length > 5000000) throw new Error('JSON too large'); }
    return JSON.parse(text || '{}');
  }
  async function library() {
    if (process.env.ONTOKB_FROM_JSON) return JSON.parse(await readFile(process.env.ONTOKB_FROM_JSON, 'utf8'));
    const response = await fetch(`${(process.env.ONTOKB_URL || 'http://127.0.0.1:8765').replace(/\/$/, '')}/api/reading`, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`OntoKB HTTP ${response.status}`);
    return response.json();
  }
  return async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (!url.pathname.startsWith('/api/kb/')) return false;
    try {
      await cleanup;
      if (url.pathname === '/api/kb/voices' && req.method === 'GET') {
        json(res, 200, await voices(url.searchParams.get('lang') ?? 'zh'));
      } else if (url.pathname === '/api/kb/image-status' && req.method === 'GET') {
        json(res, 200, await imageDoctor());
      } else if (url.pathname === '/api/kb/file' && ['GET', 'HEAD'].includes(req.method)) {
        let file;
        try { file = await safeKbFile(out, url.searchParams.get('path')); } catch { json(res, 403, { error: 'Forbidden path' }); return true; }
        const info = await stat(file);
        if (!info.isFile()) throw new Error('Not a file');
        let start = 0, end = info.size - 1, status = 200;
        if (req.headers.range) {
          const match = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
          if (match && (match[1] || match[2])) {
            start = match[1] ? Number(match[1]) : Math.max(0, info.size - Number(match[2]));
            end = match[1] && match[2] ? Math.min(Number(match[2]), end) : end;
          } else start = -1;
          if (start < 0 || start > end || start >= info.size) { res.writeHead(416, { 'Content-Range': `bytes */${info.size}` }); res.end(); return true; }
          status = 206;
        }
        const contentType = file.endsWith('.mp4') ? 'video/mp4' : file.endsWith('.png') ? 'image/png' : 'application/octet-stream';
        res.writeHead(status, { 'Content-Type': contentType, 'Accept-Ranges': 'bytes', 'Content-Length': info.size ? end - start + 1 : 0, ...(status === 206 ? { 'Content-Range': `bytes ${start}-${end}/${info.size}` } : {}) });
        if (req.method === 'HEAD' || !info.size) res.end(); else createReadStream(file, { start, end }).on('error', () => res.destroy()).pipe(res);
      } else if (url.pathname === '/api/kb/library' && ['GET', 'POST'].includes(req.method)) {
        const payload = req.method === 'POST' ? await body(req) : await library();
        const items = Array.isArray(payload) ? payload : payload.library;
        if (!Array.isArray(items) || items.some(item => !item || typeof item !== 'object')) throw new Error('Expected reading JSON with library array');
        let state = {};
        try { state = JSON.parse(await readFile(path.join(out, 'autogen_state.json'), 'utf8')); } catch (e) { if (e.code !== 'ENOENT') throw e; }
        try {
          const webState = JSON.parse(await readFile(path.join(out, 'web_state.json'), 'utf8'));
          state = { ...state, ...webState };
        } catch (e) { if (e.code !== 'ENOENT') throw e; }
        await mkdir(out, { recursive: true });
        const content = JSON.stringify({ library: items });
        const snapshot = `web-source-${createHash('sha256').update(content).digest('hex').slice(0, 16)}.json`;
        try { await writeFile(path.join(out, snapshot), content, { flag: 'wx' }); }
        catch (error) { if (error.code !== 'EEXIST') throw error; }
        json(res, 200, { source: snapshot, library: items.map(item => ({
          id: String(item.id),
          title: item.title == null ? '' : String(item.title),
          kind: item.kind == null ? '' : String(item.kind),
          summary: item.summary == null ? '' : String(item.summary),
          key_points_count: Array.isArray(item.key_points) ? item.key_points.length : 0,
          autogen: state[String(item.id)] || null,
        })) });
      } else if (url.pathname === '/api/kb/storyboard' && req.method === 'POST') {
        const data = await body(req);
        if (typeof data.id !== 'string' || !data.id || typeof data.vertical !== 'boolean') throw new Error('Invalid storyboard options');
        data.snapshot = await snapshot(data.source);
        const location = storyboardLocation(data);
        await mkdir(location.directory, { recursive: true });
        try { await stat(await safeKbFile(out, location.relative)); }
        catch { await runCommand(storyboardArgs(data, location, true)); }
        json(res, 200, await publicStoryboard(location.relative));
      } else if (url.pathname === '/api/kb/storyboard/generate' && req.method === 'POST') {
        const data = await body(req);
        if (typeof data.id !== 'string' || !data.id || typeof data.vertical !== 'boolean' ||
            (data.scene !== undefined && (!Number.isInteger(data.scene) || data.scene < 0 || data.scene > 999))) throw new Error('Invalid storyboard options');
        data.snapshot = await snapshot(data.source);
        const location = storyboardLocation(data);
        await stat(await safeKbFile(out, location.relative));
        const child = spawn(python, storyboardArgs(data, location), { cwd: workDir, env: { ...env, PYTHONUNBUFFERED: '1' } });
        const job = jobs.start(child, { itemId: data.id, kind: 'storyboard', manifest: location.relative, scene: data.scene ?? null });
        child.once('close', async () => {
          if (job.status === 'done') {
            try { job.storyboard = await publicStoryboard(location.relative); }
            catch (error) { job.status = 'failed'; job.log += `\nCould not read storyboard: ${error.message}`; }
          }
        });
        json(res, 202, { id: job.id, image_count: data.scene === undefined ? (await publicStoryboard(location.relative)).scenes.length : 1 });
      } else if (url.pathname === '/api/kb/generate' && req.method === 'POST') {
        const data = await body(req);
        if (typeof data.id !== 'string' || !data.id || (data.vertical !== undefined && typeof data.vertical !== 'boolean') || (data.lang !== undefined && !['zh', 'ja', 'en'].includes(data.lang)) || (data.voice !== undefined && (typeof data.voice !== 'string' || data.voice.length > 200))) throw new Error('Invalid generation options');
        const sizes = new Set(['1920x1080', '1280x720']);
        const styles = new Set(['editorial', 'paper', 'contrast']);
        if (data.size !== undefined && !sizes.has(data.size)) throw new Error('Invalid video size');
        if (data.style !== undefined && !styles.has(data.style)) throw new Error('Invalid visual style');
        const tts = data.tts === undefined ? 'auto' : data.tts;
        if (!['auto', 'edge', 'say'].includes(tts)) throw new Error('Invalid TTS backend');
        if (data.id.startsWith('-')) throw new Error('Invalid item id');
        const args = [path.join(cliRoot, 'kb_video.py'), data.id, '--out', out];
        if (data.source) {
          args.push('--from-json', await snapshot(data.source));
        } else if (process.env.ONTOKB_FROM_JSON) args.push('--from-json', process.env.ONTOKB_FROM_JSON);
        else args.push('--base-url', process.env.ONTOKB_URL || 'http://127.0.0.1:8765');
        const catalog = await voices(data.lang || 'zh');
        const backend = tts === 'auto' ? 'edge' : tts;
        const voice = data.voice === undefined ? catalog.defaults[backend] : data.voice;
        if (!catalog[backend].some(item => item.name === voice)) throw new Error('Invalid voice for backend and language');
        args.push('--tts', tts, '--voice', voice);
        if (data.size) args.push('--size', data.size);
        if (data.style) args.push('--style', data.style);
        if (data.vertical) args.push('--vertical');
        if (data.lang) args.push('--lang', data.lang);
        if (data.template !== undefined) {
          if (!templates.has(data.template)) throw new Error('Invalid video template');
          args.push('--template', data.template);
        }
        if (data.sceneManifest !== undefined) {
          const expected = storyboardLocation(data).relative;
          if (data.sceneManifest !== expected) throw new Error('Invalid storyboard manifest');
          args.push('--scenes', await safeKbFile(out, data.sceneManifest));
        }
        const child = spawn(python, args, { cwd: workDir, env: { ...env, PYTHONUNBUFFERED: '1' } });
        const job = jobs.start(child, { itemId: data.id, vertical: !!data.vertical });
        child.once('close', async () => {
          if (job.status === 'done') {
            const final = job.log.trim().split(/\r?\n/).at(-1);
            const relative = path.relative(out, final || '');
            if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) {
              job.status = 'running';
              try {
                const manifestPath = await safeKbFile(out, path.join(path.dirname(relative), 'manifest.json'));
                const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
                for (const key of ['tts_backend', 'voice', 'fallback_reason']) job[key] = manifest[key] ?? null;
              } catch (error) { job.log += `\nCould not read narration manifest: ${error.message}`; }
              job.outputs = [relative];
              job.status = 'done';
              saving = saving.then(async () => {
                let state = {};
                try { state = JSON.parse(await readFile(path.join(out, 'web_state.json'), 'utf8')); } catch (e) { if (e.code !== 'ENOENT') throw e; }
                const old = state[data.id]?.outputs || [];
                state[data.id] = { status: 'done', outputs: [...new Set([...old, relative])], tts_backend: job.tts_backend, voice: job.voice, fallback_reason: job.fallback_reason };
                await writeFile(path.join(out, 'web_state.json'), JSON.stringify(state));
              }).catch(error => { job.log += `\nCould not save status: ${error.message}`; });
            }
            else { job.status = 'failed'; job.log += '\nNo output path returned'; }
          }
        });
        json(res, 202, { id: job.id });
      } else if (url.pathname.startsWith('/api/kb/jobs/') && req.method === 'GET') {
        const job = jobs.get(url.pathname.slice('/api/kb/jobs/'.length));
        json(res, job ? 200 : 404, job || { error: 'Unknown job' });
      } else json(res, 404, { error: 'Unknown KB endpoint' });
    } catch (error) { json(res, 400, { error: error.message }); }
    return true;
  };
}
