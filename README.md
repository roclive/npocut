# npocut - timestamp based CLI 剪辑工具

`npocut` 接在现有的 `burn_subs.py` / `burn_existing_srt.py` 后面用：先得到 SRT，再按时间戳剪长视频、删除片段、重排片段、批量生成 Shorts，并自动生成剪辑后重新计时的 SRT。

## 依赖

- Python 3.10+
- `ffmpeg` / `ffprobe`
- 可选：`faster-whisper`，仅 `generate_srt.py` / `make_srt_only.py` / `burn_subs.py` 需要
- 可选：OpenAI API key，仅 `generate_srt_openai.py` 需要
- Web UI：Node.js，直接运行 `npm run dev`，没有第三方 npm 包

## 脚本

| 脚本 | 用途 |
| --- | --- |
| `generate_srt.py` | 复用当前目录 `burn_subs.py`，只生成 SRT，不烧录 |
| `generate_srt_openai.py` | 使用 OpenAI transcription API 生成 SRT，不用本地 Whisper |
| `make_srt_only.py` | `generate_srt.py` 的兼容旧入口，只生成 SRT，不烧录 |
| `burn_existing_srt.py` | 把已有 SRT 烧录进视频 |
| `srt_find.py` | 搜索 SRT，快速找可剪的 timestamp |
| `cut_plan.py` | 按计划保留片段；行顺序就是最终视频顺序 |
| `remove_ranges.py` | 给出要删掉的区间，自动保留其余部分并拼接 |
| `srt_slice.py` | 只按计划生成重新计时的 SRT |
| `make_shorts.py` | 从长视频批量生成 9:16 Shorts，可按每个输出合并多段 |
| `ffprobe_info.py` | 查看视频时长、分辨率、帧率、音频 |

## Web UI

```bash
cd npocut
npm run dev
```

打开终端显示的地址，加载视频和 SRT。网页在浏览器本地读取文件，不上传视频；剪辑人员可以打 IN/OUT、搜索字幕、拖拽重排。点击 Export 后，计划文件会直接写到当前目录，也就是 Python 脚本所在目录：`clip_plan.csv` / `remove_ranges.txt` / `shorts_plan.csv` / `cut_plan_new.csv`。

Web UI 有 6 个模式：

- `Cut`：生成 `python3 cut_plan.py ...`
- `Submod`：直接编辑 SRT 字幕的 start/end timestamp 和字幕正文；改 start 会同步上一条 end，改 end 会同步下一条 start；打开 `Time Nav` 后点击 timestamp 会跳转视频；点击 `Save SRT` 保存
- `Shorts`：生成 `python3 make_shorts.py ...`；字幕行左侧 `Add` 会把该字幕加入保留计划，按钮变 `Keep`
- `Adv Cut`：在字幕行左侧点 `Del` 标记要删除的字幕行，再点 `Export Cut Plan` 导出反转后的保留计划 `cut_plan_new.csv`
- `SRT`：生成 `python3 generate_srt.py "video.mp4" -o "video.srt"`
- `Burn SRT`：生成 `python3 burn_existing_srt.py "video.mp4" "video.srt" -o "video.subbed.mp4"`

底部命令栏右侧有两个按钮：

- `Copy`：复制当前命令
- `Terminal`：先保存当前 plan，再在当前目录打开 macOS Terminal 并执行当前命令

## 时间格式

以下格式都支持：

```text
75.5
01:15.500
00:01:15.500
00:01:15,500
```

## 1. 只生成 SRT

本地模型版：

```bash
python3 generate_srt.py "long.mp4" -m medium -l ja
```

自定义输出字幕文件路径：

```bash
python3 generate_srt.py "long.mp4" -o "long.srt" -m medium
```

OpenAI API 版，不用本地 Whisper：

```bash
export OPENAI_API_KEY="你的 OpenAI API key"
python3 generate_srt_openai.py "long.mp4" -l zh -o "long.openai.srt"
```

`generate_srt_openai.py` 默认使用 `gpt-4o-transcribe-diarize`，因为这个模型会返回带 start/end 的分段，适合直接生成 SRT。脚本会自动把长视频切成临时音频段后逐段转写，再合并成一个 SRT。

如果沿用旧命令里的 `-m medium`，新脚本会把它识别为本地 Whisper 模型参数并忽略，仍然使用 OpenAI 默认模型。

也可以继续使用现有脚本：

```bash
python3 burn_subs.py "long.mp4"
```

它生成 SRT 后会暂停，这时可以取消烧录并保留 SRT。

## 2. 用 SRT 找剪辑点

```bash
python3 srt_find.py "long.srt" -q "keyword" -c 2
python3 srt_find.py "long.srt" --all
```

输出会显示每条字幕的开始/结束时间，适合复制到计划文件。

## 3. 保留片段、重排片段

计划文件 `clip_plan.csv`：

```csv
start,end,title
00:01:10.000,00:02:04.000,opening hook
00:08:30.000,00:09:12.000,best explanation
00:04:05.000,00:04:38.000,move this later
```

执行：

```bash
python3 cut_plan.py "long.mp4" clip_plan.csv \
  -o "long.cut.mp4" \
  --srt "long.srt"
```

输出：

- `long.cut.mp4`
- `long.cut.srt`

CSV 行顺序就是输出视频的顺序，所以调换片段只要调换行。

## 4. 删除片段并自动拼回

计划文件 `remove_ranges.txt`：

```text
00:03:12.000,00:03:28.000,dead air
00:10:00.000-00:10:20.000
00:24:15.000 --> 00:24:40.000 long pause
```

执行：

```bash
python3 remove_ranges.py "long.mp4" \
  --plan remove_ranges.txt \
  -o "long.clean.mp4" \
  --srt "long.srt" \
  --write-keep-plan keep_plan.csv
```

`keep_plan.csv` 是计算出来的保留片段，也可以再交给 `cut_plan.py` 做二次编辑。

在 Web UI 里也可以用 `Adv Cut`：加载视频和 SRT 后，在字幕行左侧点 `Del` 做删除标记。连续或重叠的标记会先合并成删除区间，然后导出时反转成剩余的保留区间。点击 `Export Cut Plan` 会生成 `cut_plan_new.csv`，页面会提示文件已生成，然后可以运行：

```bash
python3 cut_plan.py "long.mp4" cut_plan_new.csv -o "long.cut.mp4" --srt "long.srt"
```

## 5. 批量生成 Shorts

计划文件 `shorts_plan.csv`：

```csv
output,start,end,title
short_01.mp4,00:01:10.000,00:01:58.000,hook
short_02.mp4,00:12:04.000,00:12:53.000,first part
short_02.mp4,00:13:20.000,00:13:42.000,second part
```

同一个 `output` 可以有多行，脚本会按行顺序拼成同一个短视频。Web UI 的 `Shorts` 模式会把点了 `Add/Keep` 的相邻字幕先合并，再导出为同一个 `output`，所以一次只生成一个 short。

```bash
python3 make_shorts.py "long.mp4" shorts_plan.csv \
  --srt "long.srt" \
  --burn-srt \
  --out-dir shorts_out \
  --vertical crop
```

常用竖屏模式：

- `--vertical crop`：居中裁切成 9:16，适合人物在中间
- `--vertical blur`：模糊背景 + 原视频居中，适合横屏内容不想裁掉
- `--vertical pad`：黑边补齐
- `--vertical none`：不改比例

`--max-seconds` 默认是提醒阈值，可按账号/平台要求调整；加 `--strict-duration` 会把超时当成错误。

## 6. 烧录剪辑后的字幕

普通横屏成片可以先剪出重新计时的 SRT，再用根目录已有脚本烧录：

```bash
python3 burn_existing_srt.py "long.cut.mp4" "long.cut.srt" \
  -o "long.cut.subbed.mp4"
```

Shorts 可以直接在 `make_shorts.py` 里加 `--burn-srt`。

也可以在 Web UI 里选择 `Burn SRT`，加载视频和 SRT 后复制底部命令。

## 推荐流程

### YouTube 长视频

```bash
python3 generate_srt.py "long.mp4" -m medium
python3 srt_find.py "long.srt" -q "topic"
python3 cut_plan.py "long.mp4" clip_plan.csv -o "long.cut.mp4" --srt "long.srt"
python3 burn_existing_srt.py "long.cut.mp4" "long.cut.srt" -o "long.final.mp4"
```

### YouTube Shorts 批量

```bash
python3 generate_srt.py "long.mp4" -m medium
npm run dev
# 在 UI 中导出 shorts_plan.csv 到当前目录
python3 make_shorts.py "long.mp4" shorts_plan.csv --srt "long.srt" --burn-srt --vertical blur
```

## 示例文件

- `examples/clip_plan.csv`
- `examples/remove_ranges.txt`
- `examples/shorts_plan.csv`
