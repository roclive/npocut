import { app, BrowserWindow, clipboard, ipcMain, session, shell } from "electron";
import { spawn } from "node:child_process";
import { constants, existsSync, mkdirSync, statSync, accessSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEV_ROOT = path.resolve(__dirname, "..");

const SCRIPT_NAMES = [
  "cut_plan.py",
  "remove_ranges.py",
  "make_shorts.py",
  "generate_srt.py",
  "burn_existing_srt.py",
  "burn_vertical_srt.py",
];
const SCRIPT_WHITELIST = new Set(SCRIPT_NAMES);
const PLAN_FILENAMES = new Set([
  "clip_plan.csv",
  "remove_ranges.txt",
  "shorts_plan.csv",
  "cut_plan_new.csv",
]);
const PLAN_EXTENSIONS = new Set([".csv", ".txt"]);
const SRT_EXTENSIONS = new Set([".srt"]);
const SUBTITLE_EXTENSIONS = new Set([".srt", ".ass"]);
const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".m4v", ".mkv", ".webm"]);
const MP4_EXTENSIONS = new Set([".mp4"]);
const PRESET_VALUES = new Set([
  "ultrafast",
  "superfast",
  "veryfast",
  "faster",
  "fast",
  "medium",
  "slow",
  "slower",
  "veryslow",
  "placebo",
]);
const MODEL_VALUES = new Set(["tiny", "base", "small", "medium", "large-v3"]);
const VERTICAL_VALUES = new Set(["crop", "blur", "pad", "none"]);

const activeTasks = new Map();
let mainWindow = null;

function getAppRoot() {
  return app.isPackaged ? app.getAppPath() : DEV_ROOT;
}

function getWebRoot() {
  return path.join(getAppRoot(), "web");
}

function getCliRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, "npocut-cli") : DEV_ROOT;
}

function defaultWorkDir() {
  return path.join(app.getPath("documents"), "npocut");
}

function expandHome(value) {
  if (value === "~") return app.getPath("home");
  if (value.startsWith(`~${path.sep}`) || value.startsWith("~/")) {
    return path.join(app.getPath("home"), value.slice(2));
  }
  return value;
}

function asPathString(value, label) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} path is required`);
  }
  const text = value.trim();
  if (text.includes("\0")) {
    throw new Error(`${label} path contains an invalid character`);
  }
  if (/^file:\/\//i.test(text)) {
    return fileURLToPath(text);
  }
  return expandHome(text);
}

function pathIsInside(candidate, root) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === "" || (relative && !relative.startsWith("..") && !path.isAbsolute(relative));
}

function packagedProtectedRoots() {
  if (!app.isPackaged) return [];
  const roots = [process.resourcesPath, app.getAppPath()];
  if (process.platform === "darwin") {
    roots.push(path.dirname(path.dirname(process.resourcesPath)));
  }
  return [...new Set(roots.filter(Boolean).map((root) => path.resolve(root)))];
}

function assertNotProtectedPath(candidate, label) {
  const resolved = path.resolve(candidate);
  if (resolved.split(path.sep).some((part) => part.endsWith(".asar"))) {
    throw new Error(`${label} must not be inside an app archive`);
  }
  for (const root of packagedProtectedRoots()) {
    if (pathIsInside(resolved, root)) {
      throw new Error(`${label} must not be inside the packaged app or resources`);
    }
  }
}

function assertAllowedExtension(candidate, allowedExts, label) {
  if (!allowedExts) return;
  const ext = path.extname(candidate).toLowerCase();
  if (!allowedExts.has(ext)) {
    throw new Error(`${label} has unsupported extension: ${ext || "(none)"}`);
  }
}

function ensureWritableDirectory(candidate, label = "Directory") {
  const dir = path.resolve(candidate);
  assertNotProtectedPath(dir, label);
  mkdirSync(dir, { recursive: true });
  const stat = statSync(dir);
  if (!stat.isDirectory()) {
    throw new Error(`${label} is not a directory: ${dir}`);
  }
  accessSync(dir, constants.W_OK);
  return dir;
}

function resolveAgainstBase(rawValue, baseDir) {
  const raw = asPathString(rawValue, "Path");
  return path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(baseDir, raw);
}

function resolveWritableFile(value, { baseDir, fallbackName, allowedExts, defaultExt, label }) {
  const rawValue = value ?? fallbackName;
  let resolved = resolveAgainstBase(rawValue, baseDir);
  const raw = asPathString(rawValue, label);
  if (!path.isAbsolute(raw) && !pathIsInside(resolved, baseDir)) {
    throw new Error(`${label} must stay inside baseDir when relative`);
  }
  if (!path.extname(resolved) && defaultExt) {
    resolved = `${resolved}${defaultExt}`;
  }
  assertAllowedExtension(resolved, allowedExts, label);
  assertNotProtectedPath(resolved, label);
  ensureWritableDirectory(path.dirname(resolved), `${label} parent directory`);
  return resolved;
}

function resolveWritableDirectory(value, { baseDir, fallbackName, label }) {
  const rawValue = value ?? fallbackName;
  const resolved = resolveAgainstBase(rawValue, baseDir);
  const raw = asPathString(rawValue, label);
  if (!path.isAbsolute(raw) && !pathIsInside(resolved, baseDir)) {
    throw new Error(`${label} must stay inside baseDir when relative`);
  }
  return ensureWritableDirectory(resolved, label);
}

function resolveExistingFile(value, { baseDir, allowedExts, label }) {
  const resolved = resolveAgainstBase(value, baseDir);
  const raw = asPathString(value, label);
  if (!path.isAbsolute(raw) && !pathIsInside(resolved, baseDir)) {
    throw new Error(`${label} must stay inside baseDir when relative`);
  }
  assertAllowedExtension(resolved, allowedExts, label);
  assertNotProtectedPath(resolved, label);
  const stat = statSync(resolved);
  if (!stat.isFile()) {
    throw new Error(`${label} is not a file: ${resolved}`);
  }
  return resolved;
}

function objectPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return {};
  return payload;
}

function pathLike(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const key of ["path", "filePath", "targetPath", "outputPath"]) {
      if (typeof value[key] === "string" && value[key].trim()) return value[key];
    }
  }
  return undefined;
}

function firstPath(payload, names) {
  for (const name of names) {
    const value = pathLike(payload[name]);
    if (value) return value;
  }
  return undefined;
}

function firstDefined(payload, names) {
  for (const name of names) {
    if (payload[name] !== undefined && payload[name] !== null && payload[name] !== "") {
      return payload[name];
    }
  }
  return undefined;
}

function optionalString(payload, names, label, maxLength = 200) {
  const value = firstDefined(payload, names);
  if (value === undefined) return undefined;
  const text = String(value);
  if (!text.trim()) return undefined;
  if (text.includes("\0") || /[\r\n]/.test(text)) {
    throw new Error(`${label} contains an invalid character`);
  }
  if (text.length > maxLength) {
    throw new Error(`${label} is too long`);
  }
  return text;
}

function optionalBoolean(payload, names) {
  const value = firstDefined(payload, names);
  return value === true || value === 1 || value === "1" || value === "true";
}

function optionalNumber(payload, names, label, { integer = false, min = -Infinity, max = Infinity } = {}) {
  const value = firstDefined(payload, names);
  if (value === undefined) return undefined;
  const number = Number(value);
  if (!Number.isFinite(number) || number < min || number > max) {
    throw new Error(`${label} is out of range`);
  }
  if (integer && !Number.isInteger(number)) {
    throw new Error(`${label} must be an integer`);
  }
  return number;
}

function optionalEnum(payload, names, label, values) {
  const value = optionalString(payload, names, label);
  if (value === undefined) return undefined;
  if (!values.has(value)) {
    throw new Error(`${label} is unsupported: ${value}`);
  }
  return value;
}

function appendFlag(args, flag, enabled) {
  if (enabled) args.push(flag);
}

function appendOption(args, flag, value) {
  if (value !== undefined) args.push(flag, String(value));
}

function defaultOutputName(inputPath, suffix) {
  return `${path.basename(inputPath, path.extname(inputPath))}${suffix}`;
}

function targetPathForWorkDir(payload) {
  const targetEntries = [
    ["targetPath", "file"],
    ["outputPath", "file"],
    ["output", "file"],
    ["outDir", "directory"],
  ];
  for (const [name, kind] of targetEntries) {
    const value = pathLike(payload[name]);
    if (!value) continue;
    const raw = asPathString(value, name);
    if (path.isAbsolute(raw)) {
      return kind === "directory" ? raw : path.dirname(raw);
    }
  }
  return undefined;
}

function resolveWorkDir(payload) {
  const baseDir = pathLike(payload.baseDir);
  if (baseDir) {
    const raw = asPathString(baseDir, "baseDir");
    const resolved = path.isAbsolute(raw)
      ? path.resolve(raw)
      : path.resolve(defaultWorkDir(), raw);
    return ensureWritableDirectory(resolved, "baseDir");
  }
  const inferred = targetPathForWorkDir(payload);
  return ensureWritableDirectory(inferred || defaultWorkDir(), "workDir");
}

function getVideoPath(payload, context) {
  const value = firstPath(payload, ["videoPath", "video", "inputPath", "input", "mediaPath"]);
  return resolveExistingFile(value, {
    baseDir: context.baseDir,
    allowedExts: VIDEO_EXTENSIONS,
    label: "Video",
  });
}

function getSubtitlePath(payload, context) {
  const value = firstPath(payload, ["srtPath", "srt", "subtitlePath", "subtitle"]);
  if (!value) return undefined;
  return resolveExistingFile(value, {
    baseDir: context.baseDir,
    allowedExts: SUBTITLE_EXTENSIONS,
    label: "Subtitle",
  });
}

function getOutputPath(payload, context, fallbackName, allowedExts = MP4_EXTENSIONS, defaultExt = ".mp4") {
  const value = firstPath(payload, ["outputPath", "output", "targetPath"]);
  return resolveWritableFile(value, {
    baseDir: context.baseDir,
    fallbackName,
    allowedExts,
    defaultExt,
    label: "Output",
  });
}

function getOutSrtPath(payload, context, fallbackName) {
  const value = firstPath(payload, ["outSrtPath", "outSrt", "srtOutputPath"]);
  if (!value) return undefined;
  return resolveWritableFile(value, {
    baseDir: context.baseDir,
    fallbackName,
    allowedExts: SRT_EXTENSIONS,
    defaultExt: ".srt",
    label: "Output SRT",
  });
}

function planDescriptor(payload) {
  const plan = payload.plan && typeof payload.plan === "object" && !Array.isArray(payload.plan)
    ? payload.plan
    : {};
  const pathValue =
    firstPath(payload, ["planPath"]) ||
    pathLike(plan) ||
    (typeof payload.plan === "string" ? payload.plan : undefined);
  const content = firstDefined(payload, ["planContent", "content"]) ?? firstDefined(plan, ["content"]);
  const filename =
    optionalString(payload, ["planFilename", "filename"], "Plan filename") ||
    optionalString(plan, ["filename", "name"], "Plan filename");
  return { pathValue, content, filename };
}

async function getPlanPath(payload, context, fallbackName) {
  const descriptor = planDescriptor(payload);
  if (descriptor.content !== undefined) {
    const filename = path.basename(descriptor.filename || fallbackName);
    if (!PLAN_FILENAMES.has(filename)) {
      throw new Error(`Unsupported plan filename: ${filename}`);
    }
    const target = resolveWritableFile(descriptor.pathValue, {
      baseDir: context.baseDir,
      fallbackName: filename,
      allowedExts: PLAN_EXTENSIONS,
      defaultExt: path.extname(filename),
      label: "Plan",
    });
    await writeFile(target, String(descriptor.content), "utf8");
    context.createdFiles.push(target);
    return target;
  }
  return resolveExistingFile(descriptor.pathValue, {
    baseDir: context.baseDir,
    allowedExts: PLAN_EXTENSIONS,
    label: "Plan",
  });
}

function appendCutOptions(payload, args) {
  appendFlag(args, "--copy", optionalBoolean(payload, ["copy"]));
  appendOption(args, "--crf", optionalNumber(payload, ["crf"], "CRF", { integer: true, min: 0, max: 63 }));
  appendOption(args, "--preset", optionalEnum(payload, ["preset"], "Preset", PRESET_VALUES));
  appendFlag(args, "--dry-run", optionalBoolean(payload, ["dryRun", "dry-run"]));
  appendFlag(args, "--keep-temp", optionalBoolean(payload, ["keepTemp", "keep-temp"]));
}

function appendRenderOptions(payload, args) {
  appendOption(args, "--crf", optionalNumber(payload, ["crf"], "CRF", { integer: true, min: 0, max: 63 }));
  appendOption(args, "--preset", optionalEnum(payload, ["preset"], "Preset", PRESET_VALUES));
  appendOption(args, "--font-size", optionalNumber(payload, ["fontSize", "font-size"], "Font size", { integer: true, min: 1, max: 300 }));
  appendOption(args, "--margin-v", optionalNumber(payload, ["marginV", "margin-v"], "Subtitle margin", { integer: true, min: 0, max: 2000 }));
  appendOption(args, "--subtitle-line-chars", optionalNumber(payload, ["subtitleLineChars", "subtitle-line-chars"], "Subtitle line chars", { integer: true, min: 1, max: 200 }));
  appendOption(args, "--font-name", optionalString(payload, ["fontName", "font-name"], "Font name", 100));
  appendFlag(args, "--dry-run", optionalBoolean(payload, ["dryRun", "dry-run"]));
}

function removeRangeSpec(item) {
  if (typeof item === "string") {
    if (!item.trim() || item.includes("\0") || /[\r\n]/.test(item)) {
      throw new Error("Remove range contains an invalid character");
    }
    return item;
  }
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    throw new Error("Remove range must be a string or object");
  }
  const start = firstDefined(item, ["start", "from", "in"]);
  const end = firstDefined(item, ["end", "to", "out"]);
  if (start === undefined || end === undefined) {
    throw new Error("Remove range requires start and end");
  }
  const title = optionalString(item, ["title", "label", "name"], "Remove range title", 200);
  return `${start}-${end}${title ? ` ${title}` : ""}`;
}

async function buildCutPlanTask(payload, context) {
  const video = getVideoPath(payload, context);
  const plan = await getPlanPath(payload, context, "clip_plan.csv");
  const output = getOutputPath(payload, context, defaultOutputName(video, ".cut.mp4"));
  const args = [video, plan, "-o", output];
  const srt = getSubtitlePath(payload, context);
  if (srt) {
    args.push("--srt", srt);
    const outSrt = getOutSrtPath(payload, context, output.replace(/\.mp4$/i, ".srt"));
    if (outSrt) args.push("--out-srt", outSrt);
  }
  appendCutOptions(payload, args);
  return { args, inputs: [video, plan, srt].filter(Boolean), outputs: [output] };
}

async function buildRemoveRangesTask(payload, context) {
  const video = getVideoPath(payload, context);
  const args = [video];
  const descriptor = planDescriptor(payload);
  let planInput;
  if (descriptor.pathValue || descriptor.content !== undefined) {
    planInput = await getPlanPath(payload, context, "remove_ranges.txt");
    args.push("--plan", planInput);
  }
  const ranges = Array.isArray(payload.removeRanges) ? payload.removeRanges : [];
  for (const item of ranges) {
    args.push("--remove", removeRangeSpec(item));
  }
  if (!args.includes("--plan") && ranges.length === 0) {
    throw new Error("remove_ranges.py requires a plan or removeRanges");
  }
  const output = getOutputPath(payload, context, defaultOutputName(video, ".clean.mp4"));
  args.push("-o", output);
  const srt = getSubtitlePath(payload, context);
  if (srt) {
    args.push("--srt", srt);
    const outSrt = getOutSrtPath(payload, context, output.replace(/\.mp4$/i, ".srt"));
    if (outSrt) args.push("--out-srt", outSrt);
  }
  const keepPlanValue = firstPath(payload, ["writeKeepPlanPath", "keepPlanPath"]);
  if (keepPlanValue || optionalBoolean(payload, ["writeKeepPlan"])) {
    const keepPlan = resolveWritableFile(keepPlanValue, {
      baseDir: context.baseDir,
      fallbackName: "keep_plan.csv",
      allowedExts: PLAN_EXTENSIONS,
      defaultExt: ".csv",
      label: "Keep plan",
    });
    args.push("--write-keep-plan", keepPlan);
  }
  appendCutOptions(payload, args);
  return { args, inputs: [video, planInput, srt].filter(Boolean), outputs: [output] };
}

async function buildMakeShortsTask(payload, context) {
  const video = getVideoPath(payload, context);
  const plan = await getPlanPath(payload, context, "shorts_plan.csv");
  const outDirValue = firstPath(payload, ["outDir", "targetPath"]);
  const outDir = resolveWritableDirectory(outDirValue, {
    baseDir: context.baseDir,
    fallbackName: "shorts_out",
    label: "Shorts output directory",
  });
  const args = [video, plan, "--out-dir", outDir];
  const srt = getSubtitlePath(payload, context);
  if (srt) args.push("--srt", srt);
  appendFlag(args, "--burn-srt", optionalBoolean(payload, ["burnSrt", "burn-srt"]));
  appendFlag(args, "--no-srt-output", optionalBoolean(payload, ["noSrtOutput", "no-srt-output"]));
  appendOption(args, "--vertical", optionalEnum(payload, ["vertical"], "Vertical mode", VERTICAL_VALUES));
  const target = optionalString(payload, ["target"], "Target", 20);
  if (target !== undefined) {
    if (!/^\d+x\d+$/i.test(target)) throw new Error("Target must look like 1080x1920");
    args.push("--target", target);
  }
  appendOption(args, "--max-seconds", optionalNumber(payload, ["maxSeconds", "max-seconds"], "Max seconds", { min: 0, max: 86400 }));
  appendFlag(args, "--strict-duration", optionalBoolean(payload, ["strictDuration", "strict-duration"]));
  appendRenderOptions(payload, args);
  appendFlag(args, "--keep-temp", optionalBoolean(payload, ["keepTemp", "keep-temp"]));
  return { args, inputs: [video, plan, srt].filter(Boolean), outputs: [outDir] };
}

function buildGenerateSrtTask(payload, context) {
  const video = getVideoPath(payload, context);
  const output = getOutputPath(payload, context, defaultOutputName(video, ".srt"), SRT_EXTENSIONS, ".srt");
  const args = [video, "-o", output];
  appendOption(args, "--model", optionalEnum(payload, ["model"], "Model", MODEL_VALUES));
  const language = optionalString(payload, ["language", "lang"], "Language", 20);
  if (language !== undefined) args.push("--language", language);
  return { args, inputs: [video], outputs: [output] };
}

function buildBurnExistingSrtTask(payload, context) {
  const video = getVideoPath(payload, context);
  const srt = getSubtitlePath(payload, context);
  if (!srt) throw new Error("burn_existing_srt.py requires an SRT path");
  const output = getOutputPath(payload, context, defaultOutputName(video, ".subbed.mp4"));
  return { args: [video, srt, "-o", output], inputs: [video, srt], outputs: [output] };
}

function buildBurnVerticalSrtTask(payload, context) {
  const video = getVideoPath(payload, context);
  const srt = getSubtitlePath(payload, context);
  if (!srt) throw new Error("burn_vertical_srt.py requires an SRT path");
  const output = getOutputPath(payload, context, defaultOutputName(video, ".vertical.subbed.mp4"));
  const args = [video, srt, "-o", output];
  appendOption(args, "--vertical", optionalEnum(payload, ["vertical"], "Vertical mode", new Set(["crop", "blur", "pad"])));
  const target = optionalString(payload, ["target"], "Target", 20);
  if (target !== undefined) {
    if (!/^\d+x\d+$/i.test(target)) throw new Error("Target must look like 1080x1920");
    args.push("--target", target);
  }
  appendRenderOptions(payload, args);
  return { args, inputs: [video, srt], outputs: [output] };
}

const TASK_BUILDERS = new Map([
  ["cut_plan.py", buildCutPlanTask],
  ["remove_ranges.py", buildRemoveRangesTask],
  ["make_shorts.py", buildMakeShortsTask],
  ["generate_srt.py", buildGenerateSrtTask],
  ["burn_existing_srt.py", buildBurnExistingSrtTask],
  ["burn_vertical_srt.py", buildBurnVerticalSrtTask],
]);

function scriptFromPayload(payload) {
  const modeMap = new Map([
    ["clip", "cut_plan.py"],
    ["advancedCut", "cut_plan.py"],
    ["remove", "remove_ranges.py"],
    ["shorts", "make_shorts.py"],
    ["srt", "generate_srt.py"],
    ["burnSrt", "burn_existing_srt.py"],
    ["burnVerticalSrt", "burn_vertical_srt.py"],
  ]);
  const mode = optionalString(payload, ["mode", "task"], "Mode", 50);
  const scriptValue = optionalString(payload, ["script"], "Script", 100);
  const scriptName = scriptValue ? path.basename(scriptValue) : modeMap.get(mode);
  if (!scriptName) {
    if (payload.command) {
      throw new Error("Raw shell commands are not supported; use a structured script payload");
    }
    throw new Error("Task script is required");
  }
  if (!SCRIPT_WHITELIST.has(scriptName)) {
    throw new Error(`Unsupported task script: ${scriptName}`);
  }
  return scriptName;
}

function ffmpegBundleInfo() {
  const binaryExt = process.platform === "win32" ? ".exe" : "";
  const root = app.isPackaged
    ? path.join(process.resourcesPath, "ffmpeg")
    : path.join(DEV_ROOT, "resources", "ffmpeg");
  const platformDir = path.join(root, `${process.platform}-${process.arch}`);
  const ffmpegCandidate = path.join(platformDir, `ffmpeg${binaryExt}`);
  const ffprobeCandidate = path.join(platformDir, `ffprobe${binaryExt}`);
  const ffmpegPath = existsSync(ffmpegCandidate) ? ffmpegCandidate : null;
  const ffprobePath = existsSync(ffprobeCandidate) ? ffprobeCandidate : null;
  return {
    root,
    platformDir,
    ffmpegPath,
    ffprobePath,
    bundled: Boolean(ffmpegPath || ffprobePath),
    bundledComplete: Boolean(ffmpegPath && ffprobePath),
  };
}

function pythonCommand(cliRoot) {
  const fromEnv = process.env.NPOCUT_PYTHON;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim();
  const candidates = process.platform === "win32"
    ? [
        path.join(cliRoot, ".venv", "Scripts", "python.exe"),
        path.join(cliRoot, ".venv", "Scripts", "python3.exe"),
      ]
    : [
        path.join(cliRoot, ".venv", "bin", "python3"),
        path.join(cliRoot, ".venv", "bin", "python"),
      ];
  return candidates.find((candidate) => existsSync(candidate)) || (process.platform === "win32" ? "python" : "python3");
}

function taskEnv(ffmpegInfo) {
  const env = { ...process.env, PYTHONUNBUFFERED: "1" };
  if (ffmpegInfo.ffmpegPath) env.NPOCUT_FFMPEG = ffmpegInfo.ffmpegPath;
  if (ffmpegInfo.ffprobePath) env.NPOCUT_FFPROBE = ffmpegInfo.ffprobePath;
  if (ffmpegInfo.bundled) {
    env.PATH = `${ffmpegInfo.platformDir}${path.delimiter}${env.PATH || ""}`;
  }
  const cacheRoot = path.join(app.getPath("userData"), "cache");
  env.HF_HOME = env.HF_HOME || path.join(cacheRoot, "huggingface");
  env.XDG_CACHE_HOME = env.XDG_CACHE_HOME || path.join(cacheRoot, "xdg");
  return env;
}

async function prepareTask(rawPayload) {
  const payload = objectPayload(rawPayload);
  const script = scriptFromPayload(payload);
  const cliRoot = getCliRoot();
  const scriptPath = path.join(cliRoot, script);
  if (!existsSync(scriptPath)) {
    throw new Error(`Task script not found: ${scriptPath}`);
  }
  const context = {
    baseDir: resolveWorkDir(payload),
    createdFiles: [],
  };
  const builder = TASK_BUILDERS.get(script);
  const built = await builder(payload, context);
  const ffmpegInfo = ffmpegBundleInfo();
  return {
    script,
    scriptPath,
    cwd: context.baseDir,
    executable: pythonCommand(cliRoot),
    args: ["-u", scriptPath, ...built.args],
    env: taskEnv(ffmpegInfo),
    inputs: built.inputs || [],
    outputs: built.outputs || [],
    createdFiles: context.createdFiles,
    ffmpeg: ffmpegInfo,
  };
}

function normalizeTaskId(value) {
  if (value === undefined || value === null || value === "") return randomUUID();
  const text = String(value);
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(text)) {
    throw new Error("taskId may only contain letters, numbers, dot, underscore, colon, or dash");
  }
  return text;
}

function sendToRenderer(webContents, channel, detail) {
  if (!webContents || webContents.isDestroyed()) return;
  webContents.send(channel, detail);
}

function terminateTask(task) {
  task.cancelled = true;
  const { child } = task;
  if (!child.pid) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      shell: false,
      windowsHide: true,
      stdio: "ignore",
    });
    killer.on("error", () => child.kill());
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  setTimeout(() => {
    if (!activeTasks.has(task.taskId)) return;
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
  }, 2500).unref();
}

function cancelTasks(taskId) {
  const targets = taskId ? [activeTasks.get(taskId)].filter(Boolean) : [...activeTasks.values()];
  for (const task of targets) terminateTask(task);
  return targets.length;
}

function outputChunk(text, limit = 12000) {
  const next = String(text);
  return next.length > limit ? next.slice(0, limit) : next;
}

function probeCommand(command, args, options) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        stdout: stdout.trim(),
        stderr: stderr.trim(),
        ...result,
      });
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ ok: false, error: "Timed out" });
    }, 5000);
    child.stdout.on("data", (chunk) => {
      stdout = outputChunk(stdout + chunk.toString("utf8"));
    });
    child.stderr.on("data", (chunk) => {
      stderr = outputChunk(stderr + chunk.toString("utf8"));
    });
    child.on("error", (error) => finish({ ok: false, error: error.message }));
    child.on("close", (code, signal) => finish({ ok: code === 0, code, signal }));
  });
}

function firstLine(text) {
  return String(text || "").split(/\r?\n/, 1)[0] || "";
}

async function checkRuntime(rawPayload) {
  const payload = objectPayload(rawPayload);
  const cliRoot = getCliRoot();
  const workDir = resolveWorkDir(payload);
  const ffmpegInfo = ffmpegBundleInfo();
  const env = taskEnv(ffmpegInfo);
  const python = pythonCommand(cliRoot);
  const scripts = Object.fromEntries(
    SCRIPT_NAMES.map((script) => [script, existsSync(path.join(cliRoot, script))]),
  );
  const [pythonProbe, pythonPackageProbe, ffmpegProbe, ffmpegFilterProbe, ffprobeProbe] = await Promise.all([
    probeCommand(python, ["--version"], { cwd: workDir, env }),
    probeCommand(python, ["-c", "import faster_whisper; print('faster-whisper ok')"], { cwd: workDir, env }),
    probeCommand(ffmpegInfo.ffmpegPath || "ffmpeg", ["-version"], { cwd: workDir, env }),
    probeCommand(ffmpegInfo.ffmpegPath || "ffmpeg", ["-hide_banner", "-filters"], { cwd: workDir, env }),
    probeCommand(ffmpegInfo.ffprobePath || "ffprobe", ["-version"], { cwd: workDir, env }),
  ]);
  const hasSubtitleFilter = /^\s*..?\s+subtitles\s+/m.test(ffmpegFilterProbe.stdout || "");
  return {
    ok: existsSync(cliRoot) && pythonProbe.ok && pythonPackageProbe.ok && ffmpegProbe.ok && hasSubtitleFilter && ffprobeProbe.ok,
    packaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath,
    workDir,
    cli: {
      root: cliRoot,
      exists: existsSync(cliRoot),
      scripts,
    },
    python: {
      command: python,
      ok: pythonProbe.ok,
      version: firstLine(pythonProbe.stdout || pythonProbe.stderr),
      error: pythonProbe.error || firstLine(pythonProbe.stderr),
      packages: {
        fasterWhisper: {
          ok: pythonPackageProbe.ok,
          error: pythonPackageProbe.error || firstLine(pythonPackageProbe.stderr),
        },
      },
    },
    cache: {
      hfHome: env.HF_HOME,
      xdgCacheHome: env.XDG_CACHE_HOME,
    },
    ffmpeg: {
      platformDir: ffmpegInfo.platformDir,
      bundledFfmpeg: ffmpegInfo.ffmpegPath,
      bundledFfprobe: ffmpegInfo.ffprobePath,
      bundledComplete: ffmpegInfo.bundledComplete,
      fallbackToPath: !ffmpegInfo.bundledComplete,
      ffmpeg: {
        command: ffmpegInfo.ffmpegPath || "ffmpeg",
        ok: ffmpegProbe.ok && hasSubtitleFilter,
        version: firstLine(ffmpegProbe.stdout || ffmpegProbe.stderr),
        error: ffmpegProbe.error || firstLine(ffmpegProbe.stderr),
        subtitlesFilter: hasSubtitleFilter,
      },
      ffprobe: {
        command: ffmpegInfo.ffprobePath || "ffprobe",
        ok: ffprobeProbe.ok,
        version: firstLine(ffprobeProbe.stdout || ffprobeProbe.stderr),
        error: ffprobeProbe.error || firstLine(ffprobeProbe.stderr),
      },
    },
  };
}

function registerIpcHandlers() {
  ipcMain.handle("npocut:export-plan", async (_event, rawPayload) => {
    const payload = objectPayload(rawPayload);
    const baseDir = resolveWorkDir(payload);
    const filename = path.basename(optionalString(payload, ["filename"], "Plan filename") || "");
    if (!PLAN_FILENAMES.has(filename)) {
      throw new Error("Unsupported plan filename");
    }
    const target = resolveWritableFile(firstPath(payload, ["targetPath", "path"]), {
      baseDir,
      fallbackName: filename,
      allowedExts: PLAN_EXTENSIONS,
      defaultExt: path.extname(filename),
      label: "Plan",
    });
    await writeFile(target, String(payload.content ?? ""), "utf8");
    return { ok: true, path: target, filename: path.basename(target), baseDir };
  });

  ipcMain.handle("npocut:save-srt", async (_event, rawPayload) => {
    const payload = objectPayload(rawPayload);
    const baseDir = resolveWorkDir(payload);
    const filename = path.basename(optionalString(payload, ["filename"], "SRT filename") || "subtitles.srt");
    const target = resolveWritableFile(firstPath(payload, ["targetPath", "path"]), {
      baseDir,
      fallbackName: filename,
      allowedExts: SRT_EXTENSIONS,
      defaultExt: ".srt",
      label: "SRT",
    });
    await writeFile(target, String(payload.content ?? ""), "utf8");
    return { ok: true, path: target, filename: path.basename(target), baseDir };
  });

  ipcMain.handle("npocut:run-task", async (event, rawPayload) => {
    const payload = objectPayload(rawPayload);
    const taskId = normalizeTaskId(payload.taskId);
    if (activeTasks.has(taskId)) {
      throw new Error(`Task already exists: ${taskId}`);
    }
    const task = await prepareTask(payload);
    const child = spawn(task.executable, task.args, {
      cwd: task.cwd,
      env: task.env,
      shell: false,
      detached: process.platform !== "win32",
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const record = {
      taskId,
      child,
      webContents: event.sender,
      cancelled: false,
      finished: false,
    };
    const finishTask = (detail) => {
      if (record.finished) return;
      record.finished = true;
      activeTasks.delete(taskId);
      sendToRenderer(record.webContents, "npocut:task-finished", {
        taskId,
        ...detail,
      });
    };
    activeTasks.set(taskId, record);
    sendToRenderer(event.sender, "npocut:task-started", {
      taskId,
      pid: child.pid,
      script: task.script,
      cwd: task.cwd,
      executable: task.executable,
      args: task.args,
      inputs: task.inputs,
      outputs: task.outputs,
      createdFiles: task.createdFiles,
      ffmpeg: {
        bundledFfmpeg: task.ffmpeg.ffmpegPath,
        bundledFfprobe: task.ffmpeg.ffprobePath,
        fallbackToPath: !task.ffmpeg.bundledComplete,
      },
    });
    child.stdout.on("data", (chunk) => {
      sendToRenderer(record.webContents, "npocut:task-output", {
        taskId,
        stream: "stdout",
        text: chunk.toString("utf8"),
      });
    });
    child.stderr.on("data", (chunk) => {
      sendToRenderer(record.webContents, "npocut:task-output", {
        taskId,
        stream: "stderr",
        text: chunk.toString("utf8"),
      });
    });
    child.on("error", (error) => {
      sendToRenderer(record.webContents, "npocut:task-error", {
        taskId,
        error: error.message,
      });
      finishTask({
        code: null,
        signal: null,
        cancelled: record.cancelled,
        error: error.message,
      });
    });
    child.on("close", (code, signal) => {
      finishTask({
        code,
        signal,
        cancelled: record.cancelled,
      });
    });
    return {
      ok: true,
      taskId,
      pid: child.pid,
      cwd: task.cwd,
      script: task.script,
      executable: task.executable,
      args: task.args,
      inputs: task.inputs,
      outputs: task.outputs,
      createdFiles: task.createdFiles,
    };
  });

  ipcMain.handle("npocut:cancel-task", async (_event, rawPayload) => {
    const payload = objectPayload(rawPayload);
    const taskId = typeof rawPayload === "string" ? rawPayload : payload.taskId;
    const cancelled = cancelTasks(taskId);
    return { ok: cancelled > 0, taskId: taskId || null, cancelled };
  });

  ipcMain.handle("npocut:copy-text", async (_event, value) => {
    const text = typeof value === "string" ? value : String(objectPayload(value).text ?? "");
    clipboard.writeText(text);
    return { ok: true };
  });

  ipcMain.handle("npocut:check-runtime", async (_event, rawPayload) => checkRuntime(rawPayload));
}

function isAppUrl(urlText) {
  try {
    const url = new URL(urlText);
    if (url.protocol !== "file:") return false;
    const filePath = fileURLToPath(url);
    return pathIsInside(filePath, getWebRoot());
  } catch {
    return false;
  }
}

function openExternalHttp(urlText) {
  try {
    const url = new URL(urlText);
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    shell.openExternal(url.toString());
    return true;
  } catch {
    return false;
  }
}

function configureNavigation(win) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    openExternalHttp(url);
    return { action: "deny" };
  });
  win.webContents.on("preload-error", (_event, preloadPath, error) => {
    console.error(`[npocut] preload failed: ${preloadPath}`, error);
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (isAppUrl(url)) return;
    event.preventDefault();
    openExternalHttp(url);
  });
  win.webContents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  mainWindow = win;
  configureNavigation(win);
  win.once("ready-to-show", () => win.show());
  win.webContents.once("did-finish-load", async () => {
    const bridgeReady = await win.webContents.executeJavaScript("Boolean(window.npocut)").catch(() => false);
    if (!bridgeReady) {
      console.error("[npocut] Electron preload bridge is unavailable; task execution will not work.");
    }
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });
  await win.loadFile(path.join(getWebRoot(), "index.html"));
}

registerIpcHandlers();

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("before-quit", () => {
  cancelTasks();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
