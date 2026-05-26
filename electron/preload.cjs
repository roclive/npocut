const { contextBridge, ipcRenderer, webUtils } = require("electron");

const taskEvents = [
  "npocut:task-started",
  "npocut:task-output",
  "npocut:task-error",
  "npocut:task-finished",
];

for (const channel of taskEvents) {
  ipcRenderer.on(channel, (_event, detail) => {
    window.dispatchEvent(new CustomEvent(channel, { detail }));
  });
}

function pathDirname(value) {
  const raw = String(value || "");
  const index = Math.max(raw.lastIndexOf("/"), raw.lastIndexOf("\\"));
  if (index < 0) return "";
  if (index === 0 && raw.startsWith("/")) return "/";
  return raw.slice(0, index);
}

function filePath(file) {
  try {
    return typeof File !== "undefined" && file instanceof File ? webUtils.getPathForFile(file) : "";
  } catch {
    return "";
  }
}

function withNativeFilePaths(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  const next = { ...payload };
  const videoPath = filePath(payload.videoFile);
  const srtPath = filePath(payload.srtFile);
  const planPath = filePath(payload.planFile);
  const basePath = filePath(payload.baseFile) || videoPath || srtPath || planPath;

  if (videoPath && !next.videoPath) next.videoPath = videoPath;
  if (srtPath && !next.srtPath) next.srtPath = srtPath;
  if (planPath && !next.planPath) next.planPath = planPath;
  if (basePath && !next.baseDir) next.baseDir = pathDirname(basePath);

  delete next.videoFile;
  delete next.srtFile;
  delete next.planFile;
  delete next.baseFile;
  return next;
}

contextBridge.exposeInMainWorld("npocut", {
  exportPlan: (payload) => ipcRenderer.invoke("npocut:export-plan", withNativeFilePaths(payload)),
  saveSrt: (payload) => ipcRenderer.invoke("npocut:save-srt", withNativeFilePaths(payload)),
  runTask: (payload) => ipcRenderer.invoke("npocut:run-task", withNativeFilePaths(payload)),
  cancelTask: (payload) => ipcRenderer.invoke("npocut:cancel-task", payload),
  copyText: (text) => ipcRenderer.invoke("npocut:copy-text", text),
  checkRuntime: (payload) => ipcRenderer.invoke("npocut:check-runtime", payload),
});
