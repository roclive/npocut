# NPO-9 界面验收

石墨深色工作台，以预览为主体，中文功能导航、统一间距与薄荷绿操作色。保留全部原有 DOM ID 和事件绑定。

- 指定 Python 验收：63 passed；Node 知识库测试：4 passed。
- JavaScript 语法、git diff --check 通过。
- Playwright / Chrome 真实 MP4 上传、空格播放暂停、时间跳转、IN/OUT、添加片段通过。
- CSV 实际导出并重新导入通过：00:00.225 至 00:04.000，片段名称为「开场片段」。
- 字幕中文编辑与保存通过，HTTP 200，落盘 SRT 包含修改文本；编辑时间/跳转切换通过。
- Shorts 选段生成计划行，精细剪辑标记、七种模式切换、知识库展开收起通过。
- 探索检查：搜索空结果再恢复、错误入点输入、字幕高度键盘调整（560 → 580）。无 pageerror。
- 上传入口和 IN 按钮焦点可见，导航 aria-pressed 与输入框名称完整，支持 reduced-motion。
- WCAG 对比度：正文 16.52:1，次要文字 8.39:1，占位文字 7.38:1，主按钮 11.43:1。
- 1440×1000 和 390×844 截图经视觉检查，无控件重叠或文字遮挡；窄屏 document scrollWidth = 390，计划表与命令局部滚动。

截图：[桌面](desktop-loaded.png)、[字幕编辑](desktop-subtitles.png)、[窄屏](narrow-loaded.png)。

Terminal 与 Electron 任务执行/取消沿用原逻辑，本次未启动终端任务或运行 Electron 桥接；生成字幕、烧录仅检查模式与命令，未调用在线转写或媒体渲染。

工作树未找到 00-context.md。临时素材、依赖及导出文件已清理。
