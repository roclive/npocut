# ffmpeg runtime resources

This directory is a packaging placeholder for optional ffmpeg binaries.

Expected layout:

```text
resources/ffmpeg/
  <platform>-<arch>/
    ffmpeg(.exe)
    ffprobe(.exe)
```

Examples include `darwin-arm64`, `darwin-x64`, `win32-x64`, and `linux-x64`.
In development, Electron reads `resources/ffmpeg/<platform>-<arch>/`.
In packaged builds, Electron reads `process.resourcesPath/ffmpeg/<platform>-<arch>/`.

Generated media, plan files, `shorts_out`, and caches must stay in user-writable
locations such as `Documents/npocut` or an explicit payload target, not in the
app bundle or packaged resources.
