const panel = document.getElementById('kbPanel');
const list = document.getElementById('kbLibrary');
const message = document.getElementById('kbMessage');
let source;
const fileUrl = path => `/api/kb/file?path=${encodeURIComponent(path)}`;
async function api(url, data) {
  const response = await fetch(url, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function showOutputs(container, outputs) {
  container.replaceChildren();
  for (const path of outputs || []) {
    if (typeof path !== 'string') continue;
    const video = element('video'); video.controls = true; video.preload = 'metadata'; video.src = fileUrl(path); video.playsInline = true;
    const download = element('a', '下载视频'); download.href = fileUrl(path); download.download = path.split('/').at(-1);
    container.append(video, download);
  }
}
async function load(payload) {
  message.textContent = '正在加载…';
  try {
    const data = await api('/api/kb/library', payload);
    source = data.source; list.replaceChildren();
    for (const item of data.library) {
      const row = element('article', undefined, 'kb-item');
      const title = element('h3', item.title || item.id);
      const badge = element('span', item.kind || 'article', 'kb-badge');
      const summary = element('p', item.summary || '暂无摘要', 'kb-summary');
      const status = element('p', `${item.key_points_count} 个要点 · ${item.autogen?.status || '尚未生成'}`);
      const actions = element('div', undefined, 'kb-actions');
      const log = element('pre', '', 'task-log'); log.hidden = true;
      const preview = element('div', undefined, 'kb-preview');
      showOutputs(preview, item.autogen?.outputs);
      const buttons = [false, true].map(vertical => {
        const button = element('button', vertical ? '生成竖屏' : '生成横屏');
        button.type = 'button';
        button.addEventListener('click', async () => {
          buttons.forEach(b => { b.disabled = true; }); log.hidden = false; log.textContent = ''; status.textContent = '正在启动…';
          try {
            const job = await api('/api/kb/generate', { id: item.id, source, vertical, lang: document.getElementById('kbLang').value, voice: document.getElementById('kbVoice').value.trim() });
            localStorage.setItem(`npocut-kb-${item.id}`, job.id);
            await monitor(job.id);
          } catch (error) { status.textContent = `生成失败：${error.message}`; }
          finally { buttons.forEach(b => { b.disabled = false; }); }
        });
        actions.append(button); return button;
      });
      async function monitor(id) {
        buttons.forEach(b => { b.disabled = true; }); log.hidden = false;
        try {
          for (;;) {
            const job = await api(`/api/kb/jobs/${encodeURIComponent(id)}`);
            log.textContent = job.log || '正在生成旁白与画面…';
            status.textContent = { running: '生成中…', done: '已生成', failed: '生成失败' }[job.status] || job.status;
            if (job.status !== 'running') {
              if (job.status === 'done') showOutputs(preview, job.outputs);
              localStorage.removeItem(`npocut-kb-${item.id}`); break;
            }
            await new Promise(resolve => setTimeout(resolve, 1200));
          }
        } finally { buttons.forEach(b => { b.disabled = false; }); }
      }
      row.append(title, badge, summary, status, actions, log, preview); list.append(row);
      const previous = localStorage.getItem(`npocut-kb-${item.id}`);
      if (previous) monitor(previous).catch(error => { status.textContent = error.message; localStorage.removeItem(`npocut-kb-${item.id}`); });
    }
    message.textContent = data.library.length ? `${data.library.length} 条知识` : '知识库为空，可导入 OntoKB JSON。';
  } catch (error) { message.textContent = `加载失败：${error.message}。可重试或导入保存的 JSON。`; }
}
document.getElementById('kbToggle').addEventListener('click', () => {
  panel.hidden = !panel.hidden;
  document.getElementById('kbToggle').setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden && !source) load();
});
document.getElementById('kbRefresh').addEventListener('click', () => load());
document.getElementById('kbJson').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  try { await load(JSON.parse(await file.text())); } catch (error) { message.textContent = `JSON 无效：${error.message}`; }
  event.target.value = '';
});
