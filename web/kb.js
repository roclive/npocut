import { createMonitorRegistry, IMAGE_CONFIG_GUIDANCE, mergeOutputs } from './kb_state.js';

const panel = document.getElementById('kbPanel');
const list = document.getElementById('kbLibrary');
const message = document.getElementById('kbMessage');
const searchInput = document.getElementById('kbSearch');
const imageStatus = document.getElementById('imageBackendStatus');
const imageConfigBtn = document.getElementById('imageConfigBtn');
const aspectSelect = document.getElementById('kbAspect');
const qualitySelect = document.getElementById('kbQuality');
const styleSelect = document.getElementById('kbStyle');
const stylePreview = document.getElementById('kbStylePreview');

let source;
const outputsByItem = new Map();
const fileUrl = path => `/api/kb/file?path=${encodeURIComponent(path)}`;

async function api(url, data) {
  const response = await fetch(url, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

const langSelect = document.getElementById('kbLang');
const ttsSelect = document.getElementById('kbTts');
const voiceSelect = document.getElementById('kbVoice');
const voiceMessage = document.getElementById('kbVoiceMessage');

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  remove(key) { try { localStorage.removeItem(key); } catch {} },
};

let catalog;
let voiceRequest = 0;

function preferences() {
  try { return JSON.parse(storage.get(`npocut-kb-voices-${langSelect.value}`)) || {}; } catch { return {}; }
}

function rememberVoice() {
  const prefs = preferences();
  prefs.backend = ttsSelect.value;
  prefs[ttsSelect.value] = voiceSelect.value;
  storage.set(`npocut-kb-voices-${langSelect.value}`, JSON.stringify(prefs));
}

async function loadVoices(restoreBackend = false) {
  const request = ++voiceRequest;
  const lang = langSelect.value;
  if (restoreBackend) {
    const saved = preferences().backend;
    ttsSelect.value = ['auto', 'edge', 'say'].includes(saved) ? saved : 'auto';
  }
  voiceSelect.disabled = true;
  voiceMessage.textContent = '正在加载声音…';
  try {
    const result = await api(`/api/kb/voices?lang=${encodeURIComponent(lang)}`);
    if (request !== voiceRequest) return;
    catalog = result;
    const backend = ttsSelect.value === 'say' ? 'say' : 'edge';
    const voices = result[backend];
    voiceSelect.replaceChildren(...voices.map(voice => {
      const option = element('option', voice.label); option.value = voice.name; return option;
    }));
    const saved = preferences()[ttsSelect.value];
    voiceSelect.value = voices.some(v => v.name === saved) ? saved :
      voices.some(v => v.name === result.defaults[backend]) ? result.defaults[backend] : (voices[0]?.name || '');
    voiceSelect.disabled = !voices.length;
    voiceMessage.textContent = voices.length ? '' : '没有可用声音，请选择其他配音后端。';
    rememberVoice();
  } catch (error) {
    if (request !== voiceRequest) return;
    voiceSelect.replaceChildren();
    voiceMessage.textContent = `声音加载失败：${error.message}`;
  }
}

langSelect.addEventListener('change', () => loadVoices(true));
ttsSelect.addEventListener('change', () => loadVoices());
voiceSelect.addEventListener('change', rememberVoice);
loadVoices(true);

function narrationInfo(job) {
  if (!job.tts_backend) return '';
  const label = catalog?.edge.find(v => v.name === job.voice)?.label.split(' · ')[0] ||
    ({ 'zh-CN-YunyangNeural': '云扬', 'zh-CN-XiaoxiaoNeural': '晓晓', 'zh-CN-YunxiNeural': '云希', 'zh-CN-XiaoyiNeural': '晓伊', 'zh-CN-YunjianNeural': '云健' })[job.voice] || job.voice || '';
  if (job.fallback_reason) return `配音：已退回本机 ${label}（${job.fallback_reason}）`;
  return `配音：${job.tts_backend === 'edge' ? 'Edge' : job.tts_backend === 'say' ? '本机' : job.tts_backend} ${label}`;
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
    source = data.source;
    renderLibrary(data.library);
  } catch (error) { message.textContent = `加载失败：${error.message}。可重试或导入保存的 JSON。`; }
}

let currentLibrary = [];
function renderLibrary(library) {
  currentLibrary = library;
  const query = searchInput.value.toLowerCase();
  list.replaceChildren();

  const filtered = library.filter(item =>
    String(item.title ?? '').toLowerCase().includes(query) ||
    String(item.summary ?? '').toLowerCase().includes(query)
  );

  for (const item of filtered) {
    const itemKey = String(item.id);
    outputsByItem.set(itemKey, mergeOutputs(outputsByItem.get(itemKey) || [], item.autogen?.outputs || []));
    const row = element('article', undefined, 'kb-item');
    const title = element('h3', item.title || item.id);
    const badge = element('span', item.kind || 'article', 'kb-badge');
    const summary = element('p', item.summary || '暂无摘要', 'kb-summary');
    const status = element('p', [`${item.key_points_count} 个要点 · ${item.autogen?.status || '尚未生成'}`, narrationInfo(item.autogen || {})].filter(Boolean).join(' · '));
    const actions = element('div', undefined, 'kb-actions');
    const log = element('pre', '', 'task-log'); log.hidden = true;
    const preview = element('div', undefined, 'kb-preview');
    showOutputs(preview, outputsByItem.get(itemKey));

    const generateBtn = () => {
      const btn = element('button', '生成视频');
      btn.type = 'button';
      btn.onclick = async () => {
        if (voiceSelect.disabled || !voiceSelect.value) { status.textContent = '请先选择可用声音'; return; }

        const buttons = actions.querySelectorAll('button');
        buttons.forEach(b => { b.disabled = true; });
        log.hidden = false;
        log.textContent = '';
        status.textContent = '正在启动…';
        
        try {
          const job = await api('/api/kb/generate', {
            id: item.id,
            source,
            vertical: aspectSelect.value === 'vertical',
            size: qualitySelect.value,
            style: styleSelect.value,
            lang: langSelect.value,
            tts: ttsSelect.value,
            voice: voiceSelect.value
          });
          storage.set(`npocut-kb-${item.id}`, job.id);
          await startMonitor(job.id, item, { status, log, actions, preview });
        } catch (error) {
          status.textContent = `生成失败：${error.message}`;
        } finally {
          buttons.forEach(b => { b.disabled = false; });
        }
      };
      return btn;
    };

    actions.append(generateBtn());
    row.append(title, badge, summary, status, actions, log, preview);
    list.append(row);

    const previous = storage.get(`npocut-kb-${item.id}`);
    if (previous) {
      startMonitor(previous, item, { status, log, actions, preview }).catch(error => {
        status.textContent = error.message;
        if (storage.get(`npocut-kb-${item.id}`) === previous) storage.remove(`npocut-kb-${item.id}`);
      });
    }
  }
  message.textContent = filtered.length ? `${filtered.length} 条知识` : '没有找到匹配的知识。';
}

const monitorRegistry = createMonitorRegistry(monitor);

function startMonitor(id, item, view) {
  view.actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
  view.log.hidden = false;
  return monitorRegistry.start(String(item.id), id, view);
}

async function monitor(id, getView, isCancelled, itemKey) {
  let view = getView();
  let { status, log, actions } = view;
  const buttons = actions.querySelectorAll('button');
  buttons.forEach(b => { b.disabled = true; });
  log.hidden = false;

  try {
    for (;;) {
      if (isCancelled()) return;
      const job = await api(`/api/kb/jobs/${encodeURIComponent(id)}`);
      if (isCancelled()) return;
      view = getView();
      ({ status, log, actions } = view);
      actions.querySelectorAll('button').forEach(b => { b.disabled = true; });
      log.hidden = false;
      
      const phase = {
        narration: '🎙️ 生成旁白',
        images: '🖼️ 生成画面',
        combine: '🎬 合成视频',
      }[job.phase] || '生成中…';

      log.textContent = job.log || '正在准备…';
      status.textContent = job.status === 'running' ? phase : { done: '已生成', failed: '生成失败' }[job.status] || job.status;

      if (job.status !== 'running') {
        if (job.status === 'done') {
          const outputs = mergeOutputs(outputsByItem.get(itemKey) || [], job.outputs || []);
          outputsByItem.set(itemKey, outputs);
          showOutputs(view.preview, outputs);
          status.textContent = ['已生成', narrationInfo(job)].filter(Boolean).join(' · ');
        } else if (job.status === 'failed') {
          const reason = (job.log || '').trim().split(/\r?\n/).filter(Boolean).at(-1) || '未知错误';
          status.textContent = `生成失败：${reason}。可点击“生成视频”重试。`;
        }
        if (storage.get(`npocut-kb-${itemKey}`) === id) storage.remove(`npocut-kb-${itemKey}`);
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  } finally {
    if (!isCancelled()) getView().actions.querySelectorAll('button').forEach(b => { b.disabled = false; });
  }
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

searchInput.addEventListener('input', () => {
  if (currentLibrary.length) renderLibrary(currentLibrary);
});

imageConfigBtn.onclick = () => {
  alert(IMAGE_CONFIG_GUIDANCE);
};

async function checkImageBackend() {
  imageStatus.textContent = '检测中…';
  imageStatus.className = 'status-badge';
  try {
    const result = await api('/api/kb/image-status');
    const label = result.provider === 'openai' ? `OpenAI${result.model ? ` · ${result.model}` : ''}` : '离线渐变';
    imageStatus.textContent = result.ready ? `就绪 · ${label}` : `不可用 · ${label}`;
    imageStatus.className = `status-badge ${result.ready ? 'online' : 'offline'}`;
    imageStatus.title = result.network_checked ? '' : '仅检查本地配置，未验证网络、账单或模型权限';
  } catch (error) {
    imageStatus.textContent = `检测失败 · ${error.message}`;
    imageStatus.className = 'status-badge offline';
  }
}

styleSelect.addEventListener('change', () => { stylePreview.dataset.style = styleSelect.value; });
checkImageBackend();
