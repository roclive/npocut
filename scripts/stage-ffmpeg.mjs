import { access, chmod, copyFile, mkdir, realpath, stat } from "node:fs/promises";
import { constants } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const platformDir = join(root, "resources", "ffmpeg", `${process.platform}-${process.arch}`);
const binaryExt = process.platform === "win32" ? ".exe" : "";

function splitPath(value) {
  return String(value || "")
    .split(delimiter)
    .filter(Boolean);
}

async function executableExists(candidate) {
  try {
    const info = await stat(candidate);
    await access(candidate, constants.X_OK);
    return info.isFile();
  } catch {
    return false;
  }
}

async function findOnPath(name) {
  const command = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(command, [name], { encoding: "utf8" });
  if (result.status === 0) {
    const first = result.stdout.split(/\r?\n/).find(Boolean);
    if (first && await executableExists(first.trim())) return first.trim();
  }

  for (const dir of splitPath(process.env.PATH)) {
    const candidate = join(dir, `${name}${binaryExt}`);
    if (await executableExists(candidate)) return candidate;
  }
  return "";
}

function preferredPaths(name) {
  if (process.platform !== "darwin") return [];
  return [
    `/opt/homebrew/opt/ffmpeg-full/bin/${name}`,
    `/usr/local/opt/ffmpeg-full/bin/${name}`,
    `/opt/homebrew/bin/${name}`,
    `/usr/local/bin/${name}`,
  ];
}

async function sourcePath(name, envName) {
  const configured = process.env[envName];
  if (configured && await executableExists(configured)) return configured;
  for (const candidate of preferredPaths(name)) {
    if (await executableExists(candidate)) return candidate;
  }
  return await findOnPath(name);
}

function ffmpegHasSubtitlesFilter(source) {
  const result = spawnSync(source, ["-hide_banner", "-filters"], { encoding: "utf8" });
  const text = `${result.stdout || ""}\n${result.stderr || ""}`;
  return result.status === 0 && /^\s*..?\s+subtitles\s+/m.test(text);
}

async function stageBinary(name, envName, { requireSubtitles = false } = {}) {
  const source = await sourcePath(name, envName);
  if (!source) {
    console.warn(`[stage-ffmpeg] ${name} not found; packaged app will fall back to PATH.`);
    return false;
  }

  if (requireSubtitles && !ffmpegHasSubtitlesFilter(source)) {
    throw new Error(
      `[stage-ffmpeg] ${source} does not provide the ffmpeg subtitles filter. ` +
      "Install ffmpeg-full or set NPOCUT_FFMPEG to an ffmpeg build with libass."
    );
  }

  await mkdir(platformDir, { recursive: true });
  const target = join(platformDir, `${name}${binaryExt}`);
  const resolvedSource = await realpath(source);
  await copyFile(resolvedSource, target);
  if (process.platform !== "win32") await chmod(target, 0o755);
  console.log(`[stage-ffmpeg] staged ${name}: ${resolvedSource} -> ${target}`);
  return true;
}

await Promise.all([
  stageBinary("ffmpeg", "NPOCUT_FFMPEG", { requireSubtitles: true }),
  stageBinary("ffprobe", "NPOCUT_FFPROBE"),
]);
