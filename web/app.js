const $ = (id) => document.getElementById(id);

const els = {
  video: $("video"),
  videoShell: $("videoShell"),
  videoResizeHandle: $("videoResizeHandle"),
  emptyVideo: $("emptyVideo"),
  videoInput: $("videoInput"),
  srtInput: $("srtInput"),
  planInput: $("planInput"),
  projectStatus: $("projectStatus"),
  timeReadout: $("timeReadout"),
  seekStepInput: $("seekStepInput"),
  startInput: $("startInput"),
  endInput: $("endInput"),
  titleInput: $("titleInput"),
  outputInput: $("outputInput"),
  addSegment: $("addSegment"),
  rowCount: $("rowCount"),
  totalDuration: $("totalDuration"),
  srtQuery: $("srtQuery"),
  timestampNavMode: $("timestampNavMode"),
  subtitleHeight: $("subtitleHeight"),
  cueList: $("cueList"),
  segmentRows: $("segmentRows"),
  planTitle: $("planTitle"),
  planMeta: $("planMeta"),
  clearPlan: $("clearPlan"),
  exportPlan: $("exportPlan"),
  commandText: $("commandText"),
  copyCommand: $("copyCommand"),
  runCommand: $("runCommand"),
  runtimeStatus: $("runtimeStatus"),
  taskStatus: $("taskStatus"),
  taskLog: $("taskLog"),
  cancelTask: $("cancelTask"),
};

const state = {
  mode: localStorage.getItem("npocut.mode") || "clip",
  videoName: "",
  videoPath: "",
  videoFile: null,
  srtName: "",
  srtPath: "",
  srtFile: null,
  srtBackupName: "",
  planName: "",
  planPath: "",
  planFile: null,
  userDataPath: "",
  videoUrl: "",
  cues: [],
  originalCues: [],
  advancedCutMarks: new Set(),
  submodTimeNav: localStorage.getItem("npocut.submodTimeNav") === "1",
  segments: JSON.parse(localStorage.getItem("npocut.segments") || "[]"),
  dragIndex: null,
  srtBackupTimer: null,
  taskRunning: false,
  taskLogHasContent: false,
  currentTaskId: null,
};

const ADVANCED_CUT_MERGE_GAP_SECONDS = 0.5;
const SRT_BACKUP_SAVE_DELAY_MS = 1200;
const DEFAULT_SEEK_STEP_SECONDS = 5;
const MIN_SEEK_STEP_SECONDS = 0.1;
const MAX_SEEK_STEP_SECONDS = 600;
const DEFAULT_VIDEO_HEIGHT = 520;
const MIN_VIDEO_HEIGHT = 260;
const MAX_VIDEO_HEIGHT = 960;
const subtitleHeight = clamp(
  Number(localStorage.getItem("npocut.subtitleHeight") || 560),
  360,
  1400,
);
const seekStepSeconds = clamp(
  Number(localStorage.getItem("npocut.seekStepSeconds")) || DEFAULT_SEEK_STEP_SECONDS,
  MIN_SEEK_STEP_SECONDS,
  MAX_SEEK_STEP_SECONDS,
);
const savedVideoPreviewHeight = localStorage.getItem("npocut.videoPreviewHeight");
const videoPreviewHeight = savedVideoPreviewHeight
  ? clamp(Number(savedVideoPreviewHeight) || DEFAULT_VIDEO_HEIGHT, MIN_VIDEO_HEIGHT, MAX_VIDEO_HEIGHT)
  : null;

function setSubtitleHeight(height) {
  const value = clamp(Number(height) || 560, 360, 1400);
  document.documentElement.style.setProperty("--subtitle-panel-height", `${value}px`);
  els.subtitleHeight.value = String(value);
  localStorage.setItem("npocut.subtitleHeight", String(value));
}

function setVideoPreviewHeight(height) {
  const value = clamp(Number(height) || DEFAULT_VIDEO_HEIGHT, MIN_VIDEO_HEIGHT, MAX_VIDEO_HEIGHT);
  document.documentElement.style.setProperty("--video-preview-height", `${value}px`);
  localStorage.setItem("npocut.videoPreviewHeight", String(Math.round(value)));
}

const modeMeta = {
  clip: {
    title: "剪辑计划",
    file: "clip_plan.csv",
    script: "cut_plan.py",
  },
  remove: {
    title: "字幕编辑",
    file: "",
    script: "",
  },
  shorts: {
    title: "Shorts 短视频计划",
    file: "shorts_plan.csv",
    script: "make_shorts.py",
  },
  advancedCut: {
    title: "精细剪辑计划",
    file: "cut_plan_new.csv",
    script: "cut_plan.py",
  },
  srt: {
    title: "生成字幕",
    file: "",
    script: "generate_srt.py",
  },
  burnSrt: {
    title: "烧录字幕",
    file: "",
    script: "burn_existing_srt.py",
  },
  burnVerticalSrt: {
    title: "竖屏字幕烧录",
    file: "",
    script: "burn_vertical_srt.py",
  },
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatTime(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const msTotal = Math.round(safe * 1000);
  const ms = msTotal % 1000;
  const totalSeconds = Math.floor(msTotal / 1000);
  const sec = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const min = totalMinutes % 60;
  const hour = Math.floor(totalMinutes / 60);
  const body = hour
    ? `${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${body}.${String(ms).padStart(3, "0")}`;
}

function formatSrtTime(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const msTotal = Math.round(safe * 1000);
  const ms = msTotal % 1000;
  const totalSeconds = Math.floor(msTotal / 1000);
  const sec = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const min = totalMinutes % 60;
  const hour = Math.floor(totalMinutes / 60);
  return `${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}

function parseTime(value) {
  const raw = String(value || "").trim().replace(",", ".");
  if (!raw) return NaN;
  if (!raw.includes(":")) return Number(raw);
  const parts = raw.split(":").map(Number);
  if (parts.some((part) => Number.isNaN(part))) return NaN;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return NaN;
}

function formatPlainNumber(value) {
  const rounded = Math.round(Number(value) * 1000) / 1000;
  return String(rounded).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

function setSeekStepSeconds(value) {
  const seconds = Number(String(value || "").trim().replace(",", "."));
  if (!Number.isFinite(seconds) || seconds <= 0) {
    els.seekStepInput.classList.add("invalid");
    setStatus("Invalid seek step");
    return false;
  }

  const clamped = clamp(seconds, MIN_SEEK_STEP_SECONDS, MAX_SEEK_STEP_SECONDS);
  els.seekStepInput.classList.remove("invalid");
  els.seekStepInput.value = formatPlainNumber(clamped);
  localStorage.setItem("npocut.seekStepSeconds", String(clamped));
  return true;
}

function currentSeekStepSeconds() {
  const seconds = Number(String(els.seekStepInput.value || "").trim().replace(",", "."));
  if (!Number.isFinite(seconds) || seconds <= 0) return DEFAULT_SEEK_STEP_SECONDS;
  return clamp(seconds, MIN_SEEK_STEP_SECONDS, MAX_SEEK_STEP_SECONDS);
}

function savedSeekStepSeconds() {
  return clamp(
    Number(localStorage.getItem("npocut.seekStepSeconds")) || DEFAULT_SEEK_STEP_SECONDS,
    MIN_SEEK_STEP_SECONDS,
    MAX_SEEK_STEP_SECONDS,
  );
}

function updateTimeReadout(seconds, force = false) {
  if (!force && document.activeElement === els.timeReadout) return;
  els.timeReadout.value = formatTime(seconds);
  els.timeReadout.classList.remove("invalid");
}

function commitTimeReadout() {
  const seconds = parseTime(els.timeReadout.value);
  if (!Number.isFinite(seconds)) {
    els.timeReadout.classList.add("invalid");
    setStatus("Invalid timestamp");
    return false;
  }
  els.timeReadout.classList.remove("invalid");
  seekTo(seconds);
  return true;
}

function isInteractiveTypingTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, textarea, select, button, [contenteditable='true']"));
}

async function toggleVideoPlayback() {
  if (!els.video.src) return;
  if (els.video.paused || els.video.ended) {
    try {
      await els.video.play();
    } catch (error) {
      setStatus(error.message || "Video playback failed");
    }
    return;
  }
  els.video.pause();
}

function handleGlobalKeydown(event) {
  if (event.code !== "Space" || event.metaKey || event.ctrlKey || event.altKey) return;
  if (isInteractiveTypingTarget(event.target)) return;
  event.preventDefault();
  toggleVideoPlayback();
}

function startVideoResize(event) {
  if (event.button !== 0) return;
  event.preventDefault();
  const startY = event.clientY;
  const startHeight = els.videoShell.getBoundingClientRect().height;
  els.videoShell.classList.add("resizing");
  els.videoResizeHandle.setPointerCapture(event.pointerId);

  const onPointerMove = (moveEvent) => {
    setVideoPreviewHeight(startHeight + moveEvent.clientY - startY);
  };
  const onPointerUp = () => {
    els.videoShell.classList.remove("resizing");
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);
  };

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);
}

function escapeCsv(value) {
  const text = String(value ?? "");
  if (/[",\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function stem(name) {
  return name.replace(/\.[^.]+$/, "");
}

function srtBackupName(name) {
  return `${stem(name || "subtitles")}.submod-backup.srt`;
}

function cleanOutputName(value, fallback) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[^\w.\-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  const base = cleaned || fallback;
  return base.toLowerCase().endsWith(".mp4") ? base : `${base}.mp4`;
}

function electronBridge() {
  return window.npocut || null;
}

function isElectronRuntime() {
  return Boolean(electronBridge());
}

function isFileRuntime() {
  return window.location.protocol === "file:";
}

function pathBasename(value) {
  return String(value || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function pathDirname(value) {
  const raw = String(value || "");
  const index = Math.max(raw.lastIndexOf("/"), raw.lastIndexOf("\\"));
  if (index < 0) return "";
  if (index === 0 && raw.startsWith("/")) return "/";
  return raw.slice(0, index);
}

function joinPath(base, ...parts) {
  const rawBase = String(base || "");
  if (!rawBase) return parts.filter(Boolean).join("/");
  const sep = rawBase.includes("\\") && !rawBase.includes("/") ? "\\" : "/";
  const cleanBase = rawBase === sep ? rawBase : rawBase.replace(/[\\/]+$/, "");
  const cleanParts = parts
    .filter(Boolean)
    .map((part) => String(part).replace(/^[\\/]+|[\\/]+$/g, ""));
  if (cleanBase === sep) return `${sep}${cleanParts.join(sep)}`;
  return [cleanBase, ...cleanParts].join(sep);
}

function fileAbsolutePath(file) {
  const value = file && typeof file.path === "string" ? file.path : "";
  return value && /[\\/]/.test(value) ? value : "";
}

function loadedFileInfo(file) {
  const filePath = fileAbsolutePath(file);
  return {
    name: pathBasename(filePath) || file?.name || "",
    path: filePath,
  };
}

function cloneCues(cues) {
  return cues.map((cue) => ({ ...cue }));
}

function saveState() {
  localStorage.setItem("npocut.mode", state.mode);
  localStorage.setItem("npocut.segments", JSON.stringify(state.segments));
}

function setStatus(text) {
  els.projectStatus.textContent = text;
}

function normalizeBridgeResult(result, fallback = {}) {
  const normalized =
    typeof result === "string"
      ? { ok: true, path: result, filename: pathBasename(result) }
      : { ok: true, ...fallback, ...(result || {}) };
  if (normalized.ok === false) {
    throw new Error(normalized.error || "Electron request failed");
  }
  return normalized;
}

async function callBridge(methodNames, payload, label) {
  const api = electronBridge();
  const names = Array.isArray(methodNames) ? methodNames : [methodNames];
  const methodName = names.find((name) => typeof api?.[name] === "function");
  if (!methodName) {
    throw new Error(`Electron API unavailable: ${label || names[0]}`);
  }
  return await api[methodName](payload);
}

async function postJson(url, payload) {
  if (isFileRuntime()) {
    throw new Error("Electron preload bridge is unavailable; cannot use browser fetch from file://");
  }
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}

const appApi = {
  async exportPlan(payload) {
    if (isElectronRuntime()) {
      const result = await callBridge(["exportPlan", "savePlan"], payload, "exportPlan");
      return normalizeBridgeResult(result, { filename: payload.filename, path: payload.targetPath });
    }
    return await postJson("/api/export-plan", {
      filename: payload.filename,
      content: payload.content,
    });
  },

  async saveSrt(payload) {
    if (isElectronRuntime()) {
      const result = await callBridge("saveSrt", payload, "saveSrt");
      return normalizeBridgeResult(result, { filename: payload.filename, path: payload.targetPath });
    }
    return await postJson("/api/save-srt", {
      filename: payload.filename,
      content: payload.content,
    });
  },

  async runBrowserCommand(payload) {
    return await postJson("/api/run-command", payload);
  },

  async runTask(payload) {
    const result = await callBridge("runTask", payload, "runTask");
    return normalizeBridgeResult(result);
  },

  async cancelTask(payload) {
    const result = await callBridge("cancelTask", payload, "cancelTask");
    return normalizeBridgeResult(result);
  },

  async checkRuntime(payload = {}) {
    const result = await callBridge("checkRuntime", payload, "checkRuntime");
    return result || {};
  },

  async copyText(text) {
    if (isElectronRuntime()) {
      await callBridge("copyText", String(text), "copyText");
      return;
    }
    await navigator.clipboard.writeText(text);
  },
};

function configuredUserDataPath() {
  const api = electronBridge();
  const value = api?.userDataPath || api?.paths?.userData;
  return typeof value === "string" ? value : "";
}

async function ensureElectronUserDataPath() {
  if (!isElectronRuntime()) return "";
  if (state.userDataPath) return state.userDataPath;

  const configured = configuredUserDataPath();
  if (configured) {
    state.userDataPath = configured;
    return state.userDataPath;
  }

  const api = electronBridge();
  if (typeof api?.getUserDataPath === "function") {
    const result = await api.getUserDataPath();
    state.userDataPath =
      typeof result === "string"
        ? result
        : typeof result?.path === "string"
          ? result.path
          : "";
  }

  if (typeof api?.checkRuntime === "function") {
    const result = await appApi.checkRuntime();
    state.userDataPath = result.workDir || result.userDataPath || "";
  }

  return state.userDataPath;
}

async function electronBaseDir(preferredPath = "") {
  const direct = pathDirname(preferredPath);
  if (direct) return direct;

  const loadedDir = pathDirname(state.videoPath) || pathDirname(state.srtPath);
  if (loadedDir) return loadedDir;

  const userDataPath = await ensureElectronUserDataPath();
  if (userDataPath) return userDataPath;

  throw new Error("Electron needs a loaded file path or userData path before saving");
}

async function savePayload(filename, content, preferredTargetPath = "") {
  if (!isElectronRuntime()) return { filename, content };

  const directBaseDir =
    pathDirname(preferredTargetPath) || pathDirname(state.videoPath) || pathDirname(state.srtPath);
  const baseFile = state.videoFile || state.srtFile || state.planFile || null;
  const fallbackBaseDir = directBaseDir || (baseFile ? "" : await electronBaseDir(preferredTargetPath));
  const payload = {
    filename: pathBasename(filename) || filename,
    content,
    baseFile,
    baseDir: fallbackBaseDir,
  };
  if (preferredTargetPath) {
    payload.targetPath = preferredTargetPath;
  } else if (fallbackBaseDir) {
    payload.targetPath = joinPath(fallbackBaseDir, pathBasename(filename) || filename);
  }
  return payload;
}

function setTaskLogPlaceholder() {
  if (!els.taskLog) return;
  state.taskLogHasContent = false;
  els.taskLog.textContent = "任务输出将在这里显示。";
}

function appendTaskLog(text, stream = "info") {
  if (!els.taskLog || text === undefined || text === null) return;
  const body = String(text);
  if (!body) return;
  if (!state.taskLogHasContent) {
    els.taskLog.textContent = "";
    state.taskLogHasContent = true;
  }
  const prefix = stream === "stdout" || stream === "stderr" || stream === "exit" ? `[${stream}] ` : "";
  const needsBreak = els.taskLog.textContent && !els.taskLog.textContent.endsWith("\n");
  els.taskLog.textContent += `${needsBreak ? "\n" : ""}${prefix}${body}`;
  if (!els.taskLog.textContent.endsWith("\n")) els.taskLog.textContent += "\n";
  els.taskLog.scrollTop = els.taskLog.scrollHeight;
}

function setTaskStatus(text) {
  els.taskStatus.textContent = text;
}

function updateRuntimeStatus() {
  const runtime = isElectronRuntime() ? "Electron" : "Browser";
  const suffix = isElectronRuntime() && state.userDataPath ? ` / ${state.userDataPath}` : "";
  els.runtimeStatus.textContent = `Runtime: ${runtime}${suffix}`;
  els.runCommand.textContent = isElectronRuntime() ? "执行任务" : "Terminal";
}

function updateTaskControls() {
  els.cancelTask.disabled = !isElectronRuntime() || !state.taskRunning;
}

function setTaskRunning(running, statusText) {
  state.taskRunning = running;
  if (statusText) setTaskStatus(statusText);
  updateTaskControls();
}

function taskEventKind(eventName, detail) {
  if (eventName.includes("task-started")) return "status";
  if (eventName.includes("task-error")) return "error";
  if (eventName.includes("task-finished")) return "exit";
  const stream = String(detail?.stream || "").toLowerCase();
  if (stream === "stdout" || stream === "stderr" || stream === "exit" || stream === "status") {
    return stream;
  }
  const type = String(detail?.type || detail?.kind || "").toLowerCase();
  if (type) return type;
  if (eventName.includes("stdout")) return "stdout";
  if (eventName.includes("stderr")) return "stderr";
  if (eventName.includes("exit")) return "exit";
  if (eventName.includes("status")) return "status";
  return "log";
}

function taskEventText(detail) {
  if (typeof detail === "string") return detail;
  return detail?.text ?? detail?.data ?? detail?.chunk ?? detail?.message ?? (typeof detail?.status === "string" ? detail.status : "");
}

function formatTaskCommand(detail) {
  const executable = detail?.executable || "";
  const args = Array.isArray(detail?.args) ? detail.args : [];
  if (!executable && !args.length) return "";
  return [executable, ...args]
    .filter(Boolean)
    .map((part) => {
      const text = String(part);
      return /\s/.test(text) ? `"${text.replaceAll('"', '\\"')}"` : text;
    })
    .join(" ");
}

function handleTaskEvent(event) {
  const detail = event.detail || {};
  const eventTaskId = detail.taskId ?? detail.id;
  if (state.currentTaskId && eventTaskId && String(eventTaskId) !== String(state.currentTaskId)) return;

  const kind = taskEventKind(event.type, detail);
  if (kind === "exit") {
    const code = detail.code ?? detail.exitCode ?? detail.status;
    const signal = detail.signal ? ` signal ${detail.signal}` : "";
    const cancelled = detail.cancelled ? " cancelled" : "";
    appendTaskLog(`code ${code ?? "unknown"}${signal}${cancelled}`, "exit");
    const completed = code === 0 && !detail.cancelled;
    setTaskRunning(false, completed ? "Completed" : detail.cancelled ? "Cancelled" : "Exited");
    state.currentTaskId = null;
    setStatus(completed ? "Task completed" : detail.cancelled ? "Task cancelled" : `Task exited: ${code ?? "unknown"}`);
    return;
  }

  if (kind === "error") {
    const text = taskEventText(detail) || detail.error || "Task error";
    appendTaskLog(text, "stderr");
    setTaskRunning(false, "Failed");
    setStatus(text);
    return;
  }

  if (kind === "status") {
    const text = taskEventText(detail) || (detail.script ? `Started ${detail.script}` : "");
    if (text) setTaskStatus(text);
    if (detail.taskId) {
      state.currentTaskId = detail.taskId;
      appendTaskLog(`task ${detail.taskId} started`);
    }
    if (detail.cwd) appendTaskLog(`cwd: ${detail.cwd}`);
    const command = formatTaskCommand(detail);
    if (command) appendTaskLog(`command: ${command}`);
    if (detail.ffmpeg?.fallbackToPath) {
      appendTaskLog("bundled ffmpeg is incomplete for this platform; falling back to PATH", "stderr");
    }
    return;
  }

  const stream = kind === "stderr" ? "stderr" : kind === "stdout" ? "stdout" : "info";
  appendTaskLog(taskEventText(detail), stream);
}

function attachTaskEventListeners() {
  [
    "npocut:task",
    "npocut:task-started",
    "npocut:task-error",
    "npocut:task-finished",
    "npocut-task",
    "npocut:task-log",
    "npocut:task-output",
    "npocut:task-stdout",
    "npocut:task-stderr",
    "npocut:task-exit",
    "npocut:task-status",
    "npocut:task:stdout",
    "npocut:task:stderr",
    "npocut:task:exit",
    "npocut:task:status",
    "npocut-task-log",
    "npocut-task-output",
    "npocut-task-stdout",
    "npocut-task-stderr",
    "npocut-task-exit",
    "npocut-task-status",
    "npocut:stdout",
    "npocut:stderr",
    "npocut:exit",
  ].forEach((name) => window.addEventListener(name, handleTaskEvent));
}

async function initRuntime() {
  try {
    if (isElectronRuntime()) await ensureElectronUserDataPath();
  } catch (error) {
    setStatus(error.message || "Runtime init failed");
  }
  updateRuntimeStatus();
  updateTaskControls();
}

function updateMode(mode) {
  if (!modeMeta[mode]) mode = "clip";
  state.mode = mode;
  document.body.dataset.mode = mode;
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  els.planTitle.textContent = modeMeta[mode].title;
  els.planMeta.textContent = modeMeta[mode].file || modeMeta[mode].script;
  saveState();
  render();
}

function currentTime() {
  return els.video.duration ? els.video.currentTime : 0;
}

function seekBy(delta) {
  if (!els.video.duration) return;
  els.video.currentTime = clamp(els.video.currentTime + delta, 0, els.video.duration);
  updateTimeReadout(els.video.currentTime, true);
}

function seekTo(seconds) {
  if (!els.video.duration) return;
  els.video.currentTime = clamp(seconds, 0, els.video.duration);
  updateTimeReadout(els.video.currentTime, true);
}

function loadVideo(file) {
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
  const info = loadedFileInfo(file);
  state.videoName = info.name;
  state.videoPath = info.path;
  state.videoFile = file;
  state.videoUrl = URL.createObjectURL(file);
  els.video.src = state.videoUrl;
  els.emptyVideo.classList.add("hidden");
  setStatus(`${state.videoName}${state.videoPath ? ` / ${pathDirname(state.videoPath)}` : ""}`);
  renderCommand();
}

async function fileText(file) {
  return await file.text();
}

function parseSrtTimestamp(value) {
  return parseTime(String(value).trim().split(/\s+/)[0]);
}

function parseSrt(text) {
  const blocks = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim().split(/\n{2,}/);
  const cues = [];
  for (const block of blocks) {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    const timingIndex = lines.findIndex((line) => line.includes("-->"));
    if (timingIndex === -1) continue;
    const [startRaw, endRaw] = lines[timingIndex].split("-->");
    const start = parseSrtTimestamp(startRaw);
    const end = parseSrtTimestamp(endRaw);
    const textLines = lines.slice(timingIndex + 1);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start && textLines.length) {
      cues.push({ id: cues.length, start, end, text: textLines.join("\n") });
    }
  }
  return cues;
}

async function loadSrt(file) {
  if (state.srtBackupTimer) {
    clearTimeout(state.srtBackupTimer);
    state.srtBackupTimer = null;
  }
  const info = loadedFileInfo(file);
  state.srtName = info.name;
  state.srtPath = info.path;
  state.srtFile = file;
  state.srtBackupName = srtBackupName(state.srtName);
  state.cues = parseSrt(await fileText(file));
  state.originalCues = cloneCues(state.cues);
  state.advancedCutMarks.clear();
  setStatus(`${state.videoName || "No video"} / ${state.srtName} / ${state.cues.length} cues / backup: ${state.srtBackupName}`);
  render();
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];
    if (quoted) {
      if (char === '"' && next === '"') {
        cell += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell.trim());
      cell = "";
    } else if (char === "\n") {
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
    } else if (char !== "\r") {
      cell += char;
    }
  }
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  return rows;
}

function parseRangeLine(line) {
  const raw = String(line || "").trim();
  if (!raw || raw.startsWith("#")) return null;
  let parts = [];
  if (raw.includes(",")) {
    parts = parseCsv(`${raw}\n`)[0] || [];
  } else if (raw.includes("-->")) {
    const [left, right] = raw.split("-->");
    const [end, ...title] = right.trim().split(/\s+/);
    parts = [left.trim(), end, title.join(" ")];
  } else if (raw.includes("..")) {
    const [left, right] = raw.split("..");
    const [end, ...title] = right.trim().split(/\s+/);
    parts = [left.trim(), end, title.join(" ")];
  } else if (raw.includes("-")) {
    const [left, right] = raw.split(/\s*-\s*/, 2);
    const [end, ...title] = right.trim().split(/\s+/);
    parts = [left.trim(), end, title.join(" ")];
  } else {
    parts = raw.split(/\s+/, 3);
  }
  const start = parseTime(parts[0]);
  const end = parseTime(parts[1]);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { start, end, title: parts[2] || "", output: "" };
}

function importPlanText(text) {
  const cleanText = text
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith("#"))
    .join("\n");
  const rows = parseCsv(cleanText);
  if (!rows.length) return;

  const header = rows[0].map((cell) => cell.toLowerCase());
  const hasHeader = header.includes("start") || header.includes("end") || header.includes("output");
  if (state.mode === "remove" && !hasHeader) {
    const imported = cleanText
      .split(/\r?\n/)
      .map(parseRangeLine)
      .filter(Boolean);
    state.segments = imported;
    saveState();
    render();
    return;
  }

  const dataRows = hasHeader ? rows.slice(1) : rows;
  const startIndex = hasHeader ? header.findIndex((cell) => ["start", "begin", "from", "in"].includes(cell)) : 0;
  const endIndex = hasHeader ? header.findIndex((cell) => ["end", "stop", "to", "out"].includes(cell)) : 1;
  const titleIndex = hasHeader ? header.findIndex((cell) => ["title", "label", "name", "note"].includes(cell)) : 2;
  const outputIndex = hasHeader ? header.findIndex((cell) => ["output", "file", "short", "short_id", "id"].includes(cell)) : 3;

  if (hasHeader && outputIndex >= 0) updateMode("shorts");

  const imported = [];
  for (const row of dataRows) {
    const start = parseTime(row[startIndex]);
    const end = parseTime(row[endIndex]);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;
    imported.push({
      start,
      end,
      title: titleIndex >= 0 ? row[titleIndex] || "" : "",
      output: outputIndex >= 0 ? row[outputIndex] || "" : "",
    });
  }
  state.segments = imported;
  saveState();
  render();
}

function addSegmentFromForm() {
  const start = parseTime(els.startInput.value);
  const end = parseTime(els.endInput.value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    setStatus("Invalid range");
    return;
  }
  const next = state.segments.length + 1;
  const title = els.titleInput.value.trim();
  const output =
    state.mode === "shorts"
      ? cleanOutputName(els.outputInput.value, `short_${String(next).padStart(2, "0")}`)
      : "";
  state.segments.push({ start, end, title, output });
  els.titleInput.value = "";
  if (state.mode !== "shorts") els.outputInput.value = "";
  saveState();
  render();
}

function shortsSegmentIndexForCue(cue) {
  return state.segments.findIndex(
    (segment) =>
      segment.sourceCueId === cue.id ||
      (Math.abs(segment.start - cue.start) < 0.001 &&
        Math.abs(segment.end - cue.end) < 0.001 &&
        segment.title === cue.text),
  );
}

function toggleShortsCue(cue) {
  const existingIndex = shortsSegmentIndexForCue(cue);
  if (existingIndex >= 0) {
    state.segments.splice(existingIndex, 1);
    saveState();
    render();
    setStatus(`Removed from Shorts plan: ${formatTime(cue.start)} -> ${formatTime(cue.end)}`);
    return;
  }

  const next = state.segments.length + 1;
  const output = cleanOutputName(
    els.outputInput.value,
    `short_${String(next).padStart(2, "0")}`,
  );
  state.segments.push({
    start: cue.start,
    end: cue.end,
    title: cue.text,
    output,
    sourceCueId: cue.id,
  });
  saveState();
  render();
  setStatus(`Added to Shorts plan: ${output} / ${formatTime(cue.start)} -> ${formatTime(cue.end)}`);
}

function currentShortsOutputName() {
  const firstOutput = state.segments.find((segment) => segment.output)?.output;
  return cleanOutputName(els.outputInput.value || firstOutput, "short_01");
}

function buildShortsSegments() {
  const output = currentShortsOutputName();
  return [...state.segments]
    .filter((segment) => Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.end > segment.start)
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .map((segment, index) => ({
      start: segment.start,
      end: segment.end,
      title: segment.title || "",
      output,
      sourceCueId: segment.sourceCueId || `shorts_segment_${index}`,
    }));
}

function moveRow(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= state.segments.length) return;
  const [item] = state.segments.splice(index, 1);
  state.segments.splice(next, 0, item);
  saveState();
  render();
}

function deleteRow(index) {
  state.segments.splice(index, 1);
  saveState();
  render();
}

function buildSrtText() {
  const lines = [];
  state.cues.forEach((cue, index) => {
    lines.push(String(index + 1));
    lines.push(`${formatSrtTime(cue.start)} --> ${formatSrtTime(cue.end)}`);
    lines.push(String(cue.text || "").trim());
    lines.push("");
  });
  return lines.join("\n");
}

function validateCues() {
  for (const cue of state.cues) {
    if (!Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.end <= cue.start) {
      return `Invalid timestamp at subtitle ${cue.id + 1}`;
    }
    if (!String(cue.text || "").trim()) {
      return `Empty subtitle text at subtitle ${cue.id + 1}`;
    }
  }
  return "";
}

async function saveSrtEdits() {
  if (!state.srtName) {
    setStatus("Load an SRT before saving");
    return;
  }
  const validationError = validateCues();
  if (validationError) {
    setStatus(validationError);
    window.alert(validationError);
    return;
  }

  try {
    const payload = await savePayload(state.srtName, buildSrtText(), state.srtPath);
    const result = await appApi.saveSrt(payload);
    if (isElectronRuntime() && result.path) state.srtPath = result.path;
    state.originalCues = cloneCues(state.cues);
    const message = `SRT 已保存：${result.path}`;
    setStatus(message);
    window.alert(message);
  } catch (error) {
    setStatus(error.message || "SRT save failed");
  }
}

async function saveSrtBackup() {
  if (!state.srtName || !state.srtBackupName) return;
  const validationError = validateCues();
  if (validationError) {
    setStatus(`${validationError}; backup not saved`);
    return;
  }

  try {
    const backupTargetPath = state.srtPath
      ? joinPath(pathDirname(state.srtPath), state.srtBackupName)
      : "";
    const payload = await savePayload(state.srtBackupName, buildSrtText(), backupTargetPath);
    const result = await appApi.saveSrt(payload);
    setStatus(`Auto-saved backup SRT: ${result.filename}`);
  } catch (error) {
    setStatus(error.message || "SRT backup save failed");
  }
}

function scheduleSrtBackupSave() {
  if (!state.srtName || state.mode !== "remove") return;
  if (state.srtBackupTimer) {
    clearTimeout(state.srtBackupTimer);
  }
  setStatus(`Unsaved SRT edits: ${state.srtName} / backup pending: ${state.srtBackupName}`);
  state.srtBackupTimer = window.setTimeout(() => {
    state.srtBackupTimer = null;
    saveSrtBackup();
  }, SRT_BACKUP_SAVE_DELAY_MS);
}

function updateCueTime(cue, field, value, input) {
  const seconds = parseTime(value);
  if (!Number.isFinite(seconds)) {
    input.classList.add("invalid");
    setStatus(`Invalid timestamp at subtitle ${cue.id + 1}`);
    return;
  }
  input.classList.remove("invalid");
  cue[field] = seconds;
  if (cue.end <= cue.start) {
    input.classList.add("invalid");
    setStatus(`End must be after start at subtitle ${cue.id + 1}`);
    return;
  }
  syncAdjacentCueBoundary(cue, field, seconds);
  scheduleSrtBackupSave();
  renderCommand();
}

function updateCueText(cue, value) {
  cue.text = value;
  scheduleSrtBackupSave();
}

function toggleSubmodTimeNav() {
  state.submodTimeNav = !state.submodTimeNav;
  localStorage.setItem("npocut.submodTimeNav", state.submodTimeNav ? "1" : "0");
  render();
  setStatus(state.submodTimeNav ? "Submod timestamp navigation mode" : "Submod timestamp edit mode");
}

function setRenderedCueTimeInput(cue, field) {
  const input = els.cueList.querySelector(
    `.cue[data-cue-id="${cue.id}"] .cue-time-input[data-field="${field}"]`,
  );
  if (!input) return;
  input.value = formatTime(cue[field]);
  input.classList.toggle("invalid", cue.end <= cue.start);
}

function syncAdjacentCueBoundary(cue, field, seconds) {
  if (field === "start" && cue.id > 0) {
    const previous = state.cues[cue.id - 1];
    previous.end = seconds;
    setRenderedCueTimeInput(previous, "end");
    return;
  }

  if (field === "end" && cue.id < state.cues.length - 1) {
    const next = state.cues[cue.id + 1];
    next.start = seconds;
    setRenderedCueTimeInput(next, "start");
  }
}

function buildAdvancedCutSegments() {
  const marked = state.cues
    .filter((cue) => state.advancedCutMarks.has(cue.id))
    .sort((a, b) => a.start - b.start || a.end - b.end || a.id - b.id);
  const merged = [];

  for (const cue of marked) {
    const previous = merged[merged.length - 1];
    const joinsPrevious =
      previous &&
      (cue.id === previous.lastCueId + 1 ||
        cue.start <= previous.end + ADVANCED_CUT_MERGE_GAP_SECONDS);

    if (!joinsPrevious) {
      merged.push({
        start: cue.start,
        end: cue.end,
        titleParts: [cue.text],
        lastCueId: cue.id,
      });
      continue;
    }

    previous.end = Math.max(previous.end, cue.end);
    previous.titleParts.push(cue.text);
    previous.lastCueId = Math.max(previous.lastCueId, cue.id);
  }

  return merged.map((segment) => ({
    start: segment.start,
    end: segment.end,
    title: segment.titleParts.join(" "),
  }));
}

function projectDuration() {
  const videoDuration = Number(els.video.duration);
  if (Number.isFinite(videoDuration) && videoDuration > 0) return videoDuration;
  return state.cues.reduce((max, cue) => Math.max(max, cue.end), 0);
}

function buildAdvancedKeepSegments() {
  const duration = projectDuration();
  if (!duration) return [];

  const removals = buildAdvancedCutSegments();
  if (!removals.length) return [];

  const keep = [];
  let cursor = 0;

  removals.forEach((removal) => {
    const start = clamp(removal.start, 0, duration);
    const end = clamp(removal.end, 0, duration);
    if (start > cursor + 0.001) {
      keep.push({
        start: cursor,
        end: start,
        title: `keep_${String(keep.length + 1).padStart(2, "0")}`,
      });
    }
    cursor = Math.max(cursor, end);
  });

  if (cursor < duration - 0.001) {
    keep.push({
      start: cursor,
      end: duration,
      title: `keep_${String(keep.length + 1).padStart(2, "0")}`,
    });
  }

  return keep;
}

function toggleAdvancedCutMark(cue) {
  if (state.advancedCutMarks.has(cue.id)) {
    state.advancedCutMarks.delete(cue.id);
  } else {
    state.advancedCutMarks.add(cue.id);
  }
  render();
  const cutRanges = buildAdvancedCutSegments();
  const keepRanges = buildAdvancedKeepSegments();
  setStatus(`${state.advancedCutMarks.size} marked / ${cutRanges.length} cut ranges / ${keepRanges.length} keep ranges`);
}

function buildPlanText() {
  if (state.mode === "advancedCut") {
    const rows = ["start,end,title"];
    buildAdvancedKeepSegments().forEach((segment) => {
      rows.push(
        [formatTime(segment.start), formatTime(segment.end), escapeCsv(segment.title)].join(","),
      );
    });
    return rows.join("\n").concat("\n");
  }

  if (state.mode === "remove") {
    return state.segments
      .map((segment) =>
        [formatTime(segment.start), formatTime(segment.end), escapeCsv(segment.title)].join(","),
      )
      .join("\n")
      .concat("\n");
  }

  if (state.mode === "shorts") {
    const rows = ["output,start,end,title"];
    buildShortsSegments().forEach((segment) => {
      rows.push(
        [
          escapeCsv(segment.output),
          formatTime(segment.start),
          formatTime(segment.end),
          escapeCsv(segment.title),
        ].join(","),
      );
    });
    return rows.join("\n").concat("\n");
  }

  const rows = ["start,end,title"];
  state.segments.forEach((segment) => {
    rows.push(
      [formatTime(segment.start), formatTime(segment.end), escapeCsv(segment.title)].join(","),
    );
  });
  return rows.join("\n").concat("\n");
}

async function exportPlan() {
  if (state.mode === "remove") {
    await saveSrtEdits();
    return;
  }

  const filename = modeMeta[state.mode].file;
  if (!filename) {
    setStatus("This mode has no plan file to export");
    return;
  }
  if (state.mode === "advancedCut") {
    if (!buildAdvancedCutSegments().length) {
      setStatus("No subtitle rows marked for deletion");
      return;
    }
    if (!buildAdvancedKeepSegments().length) {
      setStatus("No keep ranges left after deletion marks");
      return;
    }
  }
  const content = buildPlanText();
  try {
    const payload = await savePayload(filename, content);
    const result = await appApi.exportPlan(payload);
    if (isElectronRuntime() && result.path) {
      state.planName = filename;
      state.planPath = result.path;
    }
    if (state.mode === "advancedCut") {
      const keepCount = buildAdvancedKeepSegments().length;
      const message = `cut_plan_new.csv 已生成：${result.path} / 保留片段 ${keepCount} 行`;
      setStatus(message);
      window.alert(message);
    } else if (state.mode === "shorts") {
      const segmentCount = buildShortsSegments().length;
      const outputName = currentShortsOutputName();
      const message = `shorts_plan.csv 已生成：${result.path} / 输出 ${outputName} / 保留片段 ${segmentCount} 行`;
      setStatus(message);
      window.alert(message);
    } else {
      setStatus(`Saved: ${result.path}`);
    }
  } catch (error) {
    setStatus(error.message || "Export failed");
  }
}

async function savePlanIfNeeded() {
  const filename = modeMeta[state.mode].file;
  if (!filename) return null;
  if (state.mode === "advancedCut") {
    if (!buildAdvancedCutSegments().length) {
      throw new Error("No subtitle rows marked for deletion");
    }
    if (!buildAdvancedKeepSegments().length) {
      throw new Error("No keep ranges left after deletion marks");
    }
  }

  const payload = await savePayload(filename, buildPlanText());
  const result = await appApi.exportPlan(payload);
  if (isElectronRuntime() && result.path) {
    state.planName = filename;
    state.planPath = result.path;
  }
  return result.path;
}

function outputPathForMode(baseDir, videoBase) {
  if (state.mode === "srt") return joinPath(baseDir, `${videoBase}.srt`);
  if (state.mode === "burnSrt") return joinPath(baseDir, `${videoBase}.subbed.mp4`);
  if (state.mode === "burnVerticalSrt") return joinPath(baseDir, `${videoBase}.vertical.subbed.mp4`);
  if (state.mode === "shorts") return joinPath(baseDir, "shorts_out", currentShortsOutputName());
  return joinPath(baseDir, `${videoBase}.cut.mp4`);
}

function srtPathForTask(baseDir, videoBase) {
  if (state.mode === "srt") return "";
  if (state.srtPath) return state.srtPath;
  if (state.srtName) return joinPath(baseDir, state.srtName);
  if (state.mode === "advancedCut" || state.mode === "burnSrt" || state.mode === "burnVerticalSrt") {
    return joinPath(baseDir, `${videoBase}.srt`);
  }
  return "";
}

async function buildRunTaskPayload(planPath) {
  if (!state.videoPath && !state.videoFile) {
    throw new Error("Electron task needs a loaded video file");
  }

  const baseDir = state.videoPath ? await electronBaseDir(state.videoPath) : "";
  const videoName = pathBasename(state.videoPath) || state.videoName || "input.mp4";
  const videoBase = stem(videoName);
  const srtPath = srtPathForTask(baseDir, videoBase);
  const outputPath = outputPathForMode(baseDir, videoBase);
  const options = {
    baseDir,
    planFilename: modeMeta[state.mode].file || "",
    srtLoaded: Boolean(state.srtPath || state.srtName),
  };

  if (state.mode === "shorts") {
    options.outDir = joinPath(baseDir, "shorts_out");
    options.outputName = currentShortsOutputName();
    options.burnSrt = Boolean(srtPath);
  } else if (state.mode === "burnVerticalSrt") {
    options.vertical = "crop";
    options.target = "1080x1920";
  }

  return {
    mode: state.mode,
    videoPath: state.videoPath || videoName,
    videoFile: state.videoFile,
    srtPath,
    srtFile: state.srtFile,
    planPath: planPath || "",
    outputPath,
    baseDir,
    baseFile: state.videoFile || state.srtFile || null,
    planFilename: modeMeta[state.mode].file || "",
    outDir: state.mode === "shorts" ? joinPath(baseDir, "shorts_out") : "",
    burnSrt: state.mode === "shorts" ? Boolean(srtPath) : false,
    vertical: state.mode === "burnVerticalSrt" ? "crop" : "",
    target: state.mode === "burnVerticalSrt" ? "1080x1920" : "",
    options,
  };
}

async function runCommandInTerminal() {
  const command = els.commandText.textContent.trim();

  try {
    if (isElectronRuntime()) {
      setTaskLogPlaceholder();
      appendTaskLog(`Preparing ${modeMeta[state.mode]?.title || state.mode}`);
      setTaskRunning(true, "Preparing");
      const savedPath = await savePlanIfNeeded();
      if (savedPath) appendTaskLog(`plan saved: ${savedPath}`);
      const payload = await buildRunTaskPayload(savedPath);
      appendTaskLog(`starting mode: ${payload.mode}`);
      const result = await appApi.runTask(payload);
      state.currentTaskId = result.taskId ?? result.id ?? null;
      if (result.cwd) appendTaskLog(`cwd: ${result.cwd}`);
      if (result.executable && Array.isArray(result.args)) {
        appendTaskLog(`command: ${formatTaskCommand(result)}`);
      }
      const taskIdText = state.currentTaskId ? ` / task ${state.currentTaskId}` : "";
      setTaskRunning(true, "Running");
      setStatus(`Task started: ${payload.mode}${taskIdText}`);
      return;
    }

    if (!command.startsWith("python3 ")) {
      setStatus("No runnable command");
      return;
    }

    const savedPath = await savePlanIfNeeded();
    const result = await appApi.runBrowserCommand({ command, videoName: state.videoName });
    const saved = savedPath ? ` / plan saved: ${savedPath}` : "";
    setStatus(`Terminal launched: ${result.cwd}${saved}`);
  } catch (error) {
    setTaskRunning(false, "Idle");
    appendTaskLog(error.message || "Task failed", "stderr");
    setStatus(error.message || "Terminal launch failed");
  }
}

function renderCommand() {
  const video = state.videoName || "input.mp4";
  const base = stem(video);
  const srt = state.srtName || `${base}.srt`;
  const plan = modeMeta[state.mode].file;
  const srtArg = state.srtName ? ` --srt "${srt}"` : "";

  let command = "";
  if (state.mode === "clip") {
    command = `python3 ${modeMeta.clip.script} "${video}" ${plan} -o "${base}.cut.mp4"${srtArg}`;
  } else if (state.mode === "remove") {
    command = state.srtName
      ? `Edit timestamps/text, then click Save SRT: "${srt}"`
      : "Load an SRT, edit timestamps/text, then click Save SRT.";
  } else if (state.mode === "shorts") {
    const burn = state.srtName ? " --burn-srt" : "";
    command = `python3 ${modeMeta.shorts.script} "${video}" ${plan} --out-dir shorts_out${srtArg}${burn}`;
  } else if (state.mode === "srt") {
    command = `python3 ${modeMeta.srt.script} "${video}" -o "${base}.srt"`;
  } else if (state.mode === "burnSrt") {
    command = `python3 ${modeMeta.burnSrt.script} "${video}" "${srt}" -o "${base}.subbed.mp4"`;
  } else if (state.mode === "burnVerticalSrt") {
    command = `python3 ${modeMeta.burnVerticalSrt.script} "${video}" "${srt}" -o "${base}.vertical.subbed.mp4" --vertical crop --target 1080x1920`;
  } else if (state.mode === "advancedCut") {
    command = `python3 ${modeMeta.advancedCut.script} "${video}" ${plan} -o "${base}.cut.mp4" --srt "${srt}"`;
  } else {
    command = "Choose a mode to generate a command.";
  }
  els.commandText.textContent = command;
}

function renderRows() {
  els.segmentRows.innerHTML = "";
  state.segments.forEach((segment, index) => {
    const tr = document.createElement("tr");
    tr.draggable = true;
    tr.addEventListener("dragstart", () => {
      state.dragIndex = index;
    });
    tr.addEventListener("dragover", (event) => event.preventDefault());
    tr.addEventListener("drop", () => {
      if (state.dragIndex === null || state.dragIndex === index) return;
      const [item] = state.segments.splice(state.dragIndex, 1);
      state.segments.splice(index, 0, item);
      state.dragIndex = null;
      saveState();
      render();
    });
    tr.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      els.video.currentTime = segment.start;
      els.startInput.value = formatTime(segment.start);
      els.endInput.value = formatTime(segment.end);
      els.titleInput.value = segment.title || "";
      els.outputInput.value = segment.output || "";
    });

    tr.innerHTML = `
      <td class="grab">::</td>
      <td class="time-cell">${formatTime(segment.start)}</td>
      <td class="time-cell">${formatTime(segment.end)}</td>
      <td class="duration-cell">${formatTime(segment.end - segment.start)}</td>
      <td>${escapeHtml(segment.title || "")}</td>
      <td class="output-cell">${escapeHtml(segment.output || "")}</td>
      <td>
        <div class="row-actions">
          <button title="Move up" aria-label="Move up" data-action="up">↑</button>
          <button title="Move down" aria-label="Move down" data-action="down">↓</button>
          <button class="delete" title="Delete" aria-label="Delete" data-action="delete">×</button>
        </div>
      </td>
    `;
    tr.querySelector('[data-action="up"]').addEventListener("click", () => moveRow(index, -1));
    tr.querySelector('[data-action="down"]').addEventListener("click", () => moveRow(index, 1));
    tr.querySelector('[data-action="delete"]').addEventListener("click", () => deleteRow(index));
    els.segmentRows.appendChild(tr);
  });
}

function renderCues() {
  const query = els.srtQuery.value.trim().toLowerCase();
  let cues = state.cues;
  if (query) {
    cues = cues.filter((cue) => cue.text.toLowerCase().includes(query));
  }
  els.cueList.innerHTML = "";

  for (const cue of cues) {
    const marked = state.advancedCutMarks.has(cue.id);
    const shortsIndex = state.mode === "shorts" ? shortsSegmentIndexForCue(cue) : -1;
    const addedToShorts = shortsIndex >= 0;
    const item = document.createElement("div");
    item.className = `cue${marked ? " marked-for-cut" : ""}${addedToShorts ? " marked-for-shorts" : ""}`;
    item.dataset.cueId = String(cue.id);
    if (state.mode === "remove") {
      item.className = "cue cue-edit";
      const readonly = state.submodTimeNav ? "readonly" : "";
      const navClass = state.submodTimeNav ? " nav-mode" : "";
      item.innerHTML = `
        <div class="cue-edit-times">
          <input class="cue-time-input${navClass}" data-field="start" value="${escapeHtml(formatTime(cue.start))}" aria-label="Subtitle start" title="${state.submodTimeNav ? "Click to seek video" : "Edit subtitle start"}" ${readonly} />
          <input class="cue-time-input${navClass}" data-field="end" value="${escapeHtml(formatTime(cue.end))}" aria-label="Subtitle end" title="${state.submodTimeNav ? "Click to seek video" : "Edit subtitle end"}" ${readonly} />
        </div>
        <textarea class="cue-text-input" aria-label="Subtitle text">${escapeHtml(cue.text)}</textarea>
      `;
      item.querySelectorAll(".cue-time-input").forEach((input) => {
        input.addEventListener("click", (event) => {
          if (!state.submodTimeNav) return;
          event.stopPropagation();
          seekTo(cue[event.target.dataset.field]);
        });
        input.addEventListener("input", (event) => {
          if (state.submodTimeNav) return;
          updateCueTime(cue, event.target.dataset.field, event.target.value, event.target);
        });
      });
      item.querySelector(".cue-text-input").addEventListener("input", (event) => {
        updateCueText(cue, event.target.value);
      });
    } else if (state.mode === "shorts") {
      item.innerHTML = `
        <button
          class="cue-keep${addedToShorts ? " marked" : ""}"
          title="Add this subtitle range to Shorts plan"
          aria-label="Add this subtitle range to Shorts plan"
          aria-pressed="${addedToShorts ? "true" : "false"}"
        >${addedToShorts ? "Keep" : "Add"}</button>
        <div class="cue-time">${formatTime(cue.start)} → ${formatTime(cue.end)}</div>
        <div class="cue-text">${escapeHtml(cue.text)}</div>
      `;
      item.querySelector(".cue-keep").addEventListener("click", (event) => {
        event.stopPropagation();
        toggleShortsCue(cue);
      });
    } else {
      item.innerHTML = `
        <button
          class="cue-delete${marked ? " marked" : ""}"
          title="Mark this subtitle range for deletion"
          aria-label="Mark this subtitle range for deletion"
          aria-pressed="${marked ? "true" : "false"}"
        >${marked ? "Cut" : "Del"}</button>
        <div class="cue-time">${formatTime(cue.start)} → ${formatTime(cue.end)}</div>
        <div class="cue-text">${escapeHtml(cue.text)}</div>
      `;
      item.querySelector(".cue-delete").addEventListener("click", (event) => {
        event.stopPropagation();
        toggleAdvancedCutMark(cue);
      });
    }
    item.addEventListener("click", (event) => {
      if (event.target.closest(".cue-delete, .cue-keep, input, textarea")) return;
      if (els.video.duration) els.video.currentTime = cue.start;
      els.startInput.value = formatTime(cue.start);
      els.endInput.value = formatTime(cue.end);
    });
    els.cueList.appendChild(item);
  }

  if (state.srtName) {
    const visible = query ? `${cues.length}/${state.cues.length}` : `${state.cues.length}`;
    const marks =
      state.mode === "advancedCut"
        ? ` / ${state.advancedCutMarks.size} marked`
        : state.mode === "shorts"
          ? ` / ${state.segments.length} kept`
        : state.mode === "remove"
          ? ` / editable / backup: ${state.srtBackupName}`
          : "";
    setStatus(`${state.videoName || "No video"} / ${state.srtName} / ${visible} cues${marks}`);
  }
}

function render() {
  const activeSegments =
    state.mode === "advancedCut"
      ? buildAdvancedKeepSegments()
      : state.mode === "remove"
        ? state.cues
        : state.mode === "shorts"
          ? buildShortsSegments()
        : state.segments;
  const total =
    state.mode === "remove"
      ? state.cues.reduce((max, cue) => Math.max(max, cue.end), 0)
      : activeSegments.reduce((sum, segment) => sum + segment.end - segment.start, 0);
  els.rowCount.textContent = String(activeSegments.length);
  els.totalDuration.textContent = formatTime(total);
  els.planTitle.textContent = modeMeta[state.mode].title;
  els.planMeta.textContent =
    state.mode === "advancedCut"
      ? `${modeMeta[state.mode].file} / ${state.advancedCutMarks.size} marked / ${activeSegments.length} keep`
      : state.mode === "remove"
        ? state.srtName
          ? `${state.srtName} / backup: ${state.srtBackupName}`
          : "Load SRT"
      : modeMeta[state.mode].file || modeMeta[state.mode].script;
  els.exportPlan.textContent =
    state.mode === "advancedCut" ? "导出剪辑计划" : state.mode === "remove" ? "保存字幕" : "导出计划";
  els.clearPlan.textContent = state.mode === "remove" ? "重置字幕" : "清空计划";
  els.timestampNavMode.classList.toggle("active", state.submodTimeNav);
  els.timestampNavMode.textContent = state.submodTimeNav ? "编辑时间" : "时间跳转";
  els.timestampNavMode.title = state.submodTimeNav
    ? "Switch timestamps back to edit mode"
    : "Click timestamps to seek video instead of editing";
  renderRows();
  renderCues();
  renderCommand();
}

document.querySelectorAll(".mode-tab").forEach((button) => {
  button.addEventListener("click", () => updateMode(button.dataset.mode));
});

els.videoInput.addEventListener("change", (event) => {
  const [file] = event.target.files || [];
  if (file) loadVideo(file);
});

els.srtInput.addEventListener("change", async (event) => {
  const [file] = event.target.files || [];
  if (file) await loadSrt(file);
});

els.planInput.addEventListener("change", async (event) => {
  const [file] = event.target.files || [];
  if (file) {
    const info = loadedFileInfo(file);
    state.planName = info.name;
    state.planPath = info.path;
    state.planFile = file;
    importPlanText(await fileText(file));
  }
});

els.video.addEventListener("timeupdate", () => {
  updateTimeReadout(currentTime());
});

els.video.addEventListener("loadedmetadata", () => {
  if (els.video.videoWidth && els.video.videoHeight) {
    document.documentElement.style.setProperty(
      "--video-aspect-ratio",
      `${els.video.videoWidth} / ${els.video.videoHeight}`,
    );
  }
  updateTimeReadout(0, true);
});

$("back5").addEventListener("click", () => seekBy(-currentSeekStepSeconds()));
$("back1").addEventListener("click", () => seekBy(-1));
$("forward1").addEventListener("click", () => seekBy(1));
$("forward5").addEventListener("click", () => seekBy(currentSeekStepSeconds()));

els.seekStepInput.addEventListener("blur", (event) => {
  if (!setSeekStepSeconds(event.target.value)) {
    event.target.value = formatPlainNumber(savedSeekStepSeconds());
  }
});

els.seekStepInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    setSeekStepSeconds(event.target.value);
    event.target.blur();
  } else if (event.key === "Escape") {
    event.target.value = formatPlainNumber(savedSeekStepSeconds());
    event.target.classList.remove("invalid");
    event.target.blur();
  }
});

els.timeReadout.addEventListener("blur", () => {
  if (!commitTimeReadout()) updateTimeReadout(currentTime(), true);
});

els.timeReadout.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    commitTimeReadout();
    event.target.blur();
  } else if (event.key === "Escape") {
    updateTimeReadout(currentTime(), true);
    event.target.blur();
  }
});

$("setIn").addEventListener("click", () => {
  els.startInput.value = formatTime(currentTime());
});

$("setOut").addEventListener("click", () => {
  els.endInput.value = formatTime(currentTime());
});

els.addSegment.addEventListener("click", addSegmentFromForm);
els.srtQuery.addEventListener("input", renderCues);
els.timestampNavMode.addEventListener("click", toggleSubmodTimeNav);
els.subtitleHeight.addEventListener("input", (event) => {
  setSubtitleHeight(event.target.value);
});

els.clearPlan.addEventListener("click", () => {
  if (state.mode === "advancedCut") {
    state.advancedCutMarks.clear();
  } else if (state.mode === "remove") {
    state.cues = cloneCues(state.originalCues);
    scheduleSrtBackupSave();
  } else {
    state.segments = [];
  }
  saveState();
  render();
});

els.exportPlan.addEventListener("click", exportPlan);

els.copyCommand.addEventListener("click", async () => {
  try {
    await appApi.copyText(els.commandText.textContent);
    setStatus("Command copied");
  } catch (error) {
    setStatus(error.message || "Copy failed");
  }
});

els.runCommand.addEventListener("click", runCommandInTerminal);
els.cancelTask.addEventListener("click", async () => {
  if (!state.taskRunning) return;
  try {
    await appApi.cancelTask({ taskId: state.currentTaskId });
    appendTaskLog("Cancel requested");
    setTaskStatus("Cancelling");
  } catch (error) {
    setStatus(error.message || "Cancel failed");
  }
});
document.addEventListener("keydown", handleGlobalKeydown);
els.videoResizeHandle.addEventListener("pointerdown", startVideoResize);

attachTaskEventListeners();
setTaskLogPlaceholder();
setSubtitleHeight(subtitleHeight);
setSeekStepSeconds(seekStepSeconds);
if (videoPreviewHeight) setVideoPreviewHeight(videoPreviewHeight);
updateTimeReadout(currentTime(), true);
initRuntime();
updateMode(state.mode);
