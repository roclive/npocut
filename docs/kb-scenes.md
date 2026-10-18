# OntoKB 分镜与素材 manifest

从快照生成可检查分镜（无需凭证、网络、TTS 或 FFmpeg）：

```sh
python kb_scenes.py article:0552d0796a761a267b95990a \
  --from-json tests/fixtures/ontokb_reading.json \
  --out kb_out/scenes --plan-only
```

去掉 `--plan-only` 即调用现有图片后端生成素材。默认 `gradient` 完全离线；`--provider openai` 使用 `OPENAI_API_KEY`、`KB_IMAGE_MODEL` 和 `KB_IMAGE_TIMEOUT`。可用 `--script existing.script.json` 保留已有脚本，`--cache-dir` 共享图片缓存。输出文件是 `scene-manifest.json`。fixture 分镜示例见 `examples/ontokb.scene-plan.json`。

每个 scene 对应一个原始脚本段落，包含来源 ID、URL、字段、原文、对应旁白、主题、起点、时长、图片 prompt、negative prompt、seed、风格和尺寸。旁白必须能在来源字段中找到；结尾只允许脚本生成器的编辑性结语，并明确标记。中文事实不经过模型改写；图片仅作概念配图，不作为新闻现场或事实证据。提示词禁止文字、中文标题、品牌标记和虚构统计图。实际图片仍需人工检查。

Python 调用 `build_scene_plan(item, script, durations=[...])` 可传入实际 TTS 时长；缺省时长按每秒五字加 0.35 秒估算，最少两秒，manifest 标记为 estimated。当前图片后端输出固定 512×512，其他尺寸会被拒绝。seed 由来源与旁白稳定计算；当前后端没有 seed 参数，manifest 中 `seed_applied=false` 明确说明不保证远程模型重绘像素一致。

缓存键涵盖完整来源内容摘要、provider、模型、prompt、negative prompt、风格、尺寸和 seed。命中前检查图片存在、PNG 签名与 SHA-256；缺图或内容损坏会重建。来源内容任何变化都会失效。单场景 API 失败或无凭证会生成明确标记的离线渐变占位图，继续后续场景；占位图不会被当作成功 AI 缓存，下一次运行会重新尝试。素材无法写入时保留该场景、标记 `path=null`。manifest 不包含 API 密钥。

离线测试：`python -m pytest -q tests/test_kb_scenes.py`。

## 成片模板

`--template` 提供三个克制的预设，不传参时继续使用旧模板：

| 预设 | 时长上限 | 镜头节奏 | 开场 |
| --- | ---: | --- | --- |
| `knowledge-brief` | 90 s | 5–12 s | 来源标题 |
| `concept-explainer` | 150 s | 8–18 s | 来源标题 |
| `vertical-short` | 45 s | 3–8 s | 第一条来源要点 |

模板还分别记录字号系数和完整色板。内容只从标题、摘要和要点选取，会去重并按时长预算停止；素材少时直接缩短，不重复旁白。最后一幕始终是来源页。模板配置、`aspect_ratio` 和每幕来源都写入 manifest。

```sh
python kb_scenes.py article:0552d0796a761a267b95990a \
  --from-json tests/fixtures/ontokb_reading.json \
  --template knowledge-brief --aspect-ratio 16:9 --plan-only

python kb_video.py article:0552d0796a761a267b95990a \
  --from-json tests/fixtures/ontokb_reading.json \
  --template vertical-short --vertical
```

`知识简报`和`概念讲解`也可以传 `--vertical`，`竖版短视频`也可输出 16:9；模板与画幅是两个独立选项。
