# 图片后端

`kb_images.py` 提供独立的 `Provider.render(prompt, output)` 接口，以及
`generate(prompt, output, config=None, fallback=False)`。默认 `gradient` 完全离线，
生成 512×512 PNG 渐变与装饰条，以及含转义标题、支持浏览器字体的 SVG 排版稿。
SVG 为可用的文字排版回退；PNG 仅为背景素材。调用方按需选择，不改动现有视频流程。

## 安装和配置

所有命令从项目根目录执行，无需修改全局 Python：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3 -m venv .venv-images
.venv-images/bin/python -m pip install --cache-dir kb_out/.pip-cache -r requirements-images.txt
.venv-images/bin/python kb_images.py doctor --provider openai
.venv-images/bin/python kb_images.py smoke --provider openai --output kb_out/images/real-smoke.png
.venv-images/bin/python kb_images.py generate --provider gradient --prompt '社区知识分享' --output kb_out/images/fallback.png
```

已有环境变量 `OPENAI_API_KEY` 用于官方 HTTPS endpoint，不写入文件或日志。
`KB_IMAGE_PROVIDER` 默认 `gradient`；`KB_IMAGE_MODEL` 默认 `gpt-image-2.5-flare`；
`KB_IMAGE_TIMEOUT` 默认 180 秒（上限 300）。doctor 不联网，ready 只代表本地条件满足，
不代表凭证/账单/模型权限已通过验证。smoke 与 generate 每次最多发出一个请求，
`n=1`、low、1024×1024，不重试，不轮询，不下载权重，响应上限 24 MiB。
当前配置针对 GPT Image 2.5 参数；覆盖模型时须确认其支持同样参数。

### GPT Image 接口契约

本实现不是 DALL·E 适配器。官方 [Create image API reference](https://developers.openai.com/api/reference/resources/images/methods/generate)
明确支持 GPT Image 的 `output_format=png`；GPT Image 直接返回 base64，
不支持旧模型的 `response_format` 参数。官方示例也包含 token `usage`，
不能仅因响应带 usage 就判断真实验收是 mock。本地契约测试使用模拟响应验证这些字段，
实际生成证据仍由独立的 smoke 元数据提供，两者不混同。
分镜中的 negative prompt 作为文字要求拼入正向提示词，没有声称模型支持专用 negative-prompt 字段；
稳定 seed 仅用于计划和缓存，manifest 的 `seed_applied=false` 明确说明未传给 API。

请求返回图片后使用 Pillow 完整解码、确认原图为 1024×1024，缩至 512×512 PNG。
相邻 JSON 记录尺寸、耗时、哈希、模型、字节和 usage。
缺凭证、网络失败、HTTP 错误或无效图像默认失败退出 1。
显式传入 `--fallback` 才在 API 失败时生成离线素材，同时记录 `blocked_reason`、
`ai_generated=false` 并退出 2；正常生成退出 0。离线素材不计入真实生图验收。

## 真实验收（2026-10-18，Asia/Tokyo）

- 硬件：macOS 15.7.3，arm64，16 GiB 内存，可用磁盘约 241 GiB。
- 工作树未包含管理器所述 `00-context.md`，因此执行保守上限：一次请求、一张图；未下载 SD 套装。
- 采用已有 API 凭证；模型 `gpt-image-2.5-flare`，质量 low。
- Prompt：`Editorial watercolor illustration of a small community library, warm sunlight, no lettering`。
- 运行命令：上方 `smoke --provider openai` 命令；实际成功一次，无重试。
- API 原图 1024×1024；最终 PNG **512×512**；总耗时 **12.191 秒**。
- 图片数据下载 **2,407,059 bytes**；最终 PNG **583,677 bytes**；模型权重下载 **0 bytes**。
- 独立环境依赖 Pillow 12.1.0（HPND 许可），wheel 下载约 4.7 MB；无全局安装。
- 模型是闭源托管服务，无本地模型许可证/权重；使用受 OpenAI API 服务条款约束，不能将其视作开源 SD 许可。
- 目视检查：完整水彩图书馆建筑、入口、书架、植物、长椅和自行车，图像正常，非空白、无明显损坏。
- 成品：`kb_out/images/real-smoke.png`；同目录原始元数据 JSON。
  本地生成媒体与依赖被 git 忽略；可版本化记录见 [kb-images-smoke.json](kb-images-smoke.json)。
- SHA-256：`f71c074fd206979ab523cd5da4918661a002b6b31112dbb69122ff837c6621cd`。
- API usage：20 input tokens，196 output image tokens，216 total tokens。

官方依据：[Image generation](https://developers.openai.com/api/docs/guides/image-generation#customize-image-output)。
官方文档要求总像素至少 655,360，故采用 1024 原图再缩图，未发送不兼容的 512 请求。
离线单元测试仅验证接口契约、边界和错误回退；真实成功证据独立记录于此。
