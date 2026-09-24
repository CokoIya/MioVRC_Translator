# Mio RealTime Translator | VRChat / VRC 实时翻译工具

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./docs/README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)
[![稳定版 / Beta 下载](https://img.shields.io/badge/稳定版%20%2F%20Beta%20下载-miovrc.com-ff6b35?style=for-the-badge)](https://miovrc.com)
[![License: GPLv3-or-later](https://img.shields.io/badge/License-GPLv3--or--later-blue.svg?style=for-the-badge)](./LICENSE)

## 官网下载

### [https://miovrc.com/](https://miovrc.com/)

> 面向 VRChat / VRC 用户的本地实时语音翻译工具、VRChat Chatbox 翻译助手
> 作者：`ここ_Mio` / 官方版本永久免费 / 代码采用 GPLv3-or-later 开源
> 下载官网：[https://miovrc.com/](https://miovrc.com/)

## 简介

**Mio RealTime Translator** 是一款面向 VRChat / VRC 的桌面实时翻译工具，支持 Chatbox 字幕翻译与面向语音对话的同声传译体验。玩家也常用 `VRC 翻译插件`、`VRChat 翻译插件`、`VRChat Chatbox 翻译`、`VRChat 语音翻译`、`VRChat 同声传译` 等关键词来搜索这类工具。

它重点解决两类场景：

- 自己说的话，快速翻译后发到 `VRChat Chatbox`
- 听到别人的语音后，快速做反向翻译，方便理解和确认内容

当前主要链路：

- 麦克风翻译：`麦克风 -> ASR -> 翻译 -> VRChat Chatbox`
- 同传语音：`麦克风 -> ASR -> 翻译 -> TTS -> MixLine 虚拟麦克风 -> VRChat`
- 反向翻译：`VRChat 音频 -> ASR -> 翻译 -> 聊天框 / 悬浮显示`

## 相关关键词：

- 中文：VRC翻译插件、VRChat翻译插件、VRChat实时翻译、VRChat语音翻译、VRChat同声传译、VRChat AI同传、VRC实时口译、VRChat Chatbox翻译、VRChat聊天框翻译、VRChat反向翻译、VRChat字幕翻译、VRChat本地翻译工具、VRChat语音转文字翻译
- English: VRChat translator, VRC translator, VRChat real-time translator, VRChat voice translator, VRChat simultaneous interpretation, VRC real-time interpreter, VRChat AI interpreter, VRChat speech translation, VRChat Chatbox translator, VRChat subtitle overlay, VRChat local translation tool
- 日本語：VRChat 翻訳ツール、VRC 翻訳、VRChat 同時通訳、VRChat リアルタイム通訳、VRChat リアルタイム翻訳、VRChat 音声翻訳、VRChat Chatbox 翻訳、VRChat 字幕表示、VRChat ローカル翻訳ツール
- 한국어: VRChat 번역기, VRC 번역기, VRChat 실시간 번역, VRChat 음성 번역, VRChat 채팅박스 번역, VRChat 자막 번역

## 下载

- 官方下载站：[https://miovrc.com/](https://miovrc.com/)
- 正式版提供单一安装包 `MioTranslator-Setup-vX.exe`
- 首次使用时，所需模型会由应用内下载器准备；已有模型会保留，不会重复下载
- GitHub 仓库主要用于源代码、问题反馈和开发记录

## 功能亮点

- 本地语音识别与实时翻译
- 翻译结果可发送到 `VRChat Chatbox`
- 支持反向翻译，监听 VRChat 播放音频并翻译
- **同声传译 / TTS语音阅读**：将翻译文字通过 AI 声线朗读，并可经 MixLine 虚拟麦克风让 VRChat 中的其他玩家听到
- 内置手动文本翻译面板，支持快捷键唤起
- 支持多种聊天框输出格式
- 支持多语言界面
- 支持 ASR 词典、自声抑制、降噪、VAD 与识别参数调节
- 支持 `Avatar / OSC` 参数同步
- 支持可选悬浮窗显示
- **VR 头显内使用**：头显字幕板、SteamVR 菜单标签、手腕面板，以及像 VRHandsFrame 一样的双手取景翻译

## 运行环境

- 推荐系统：`Windows 10 / 11`
- 反向翻译依赖 `Windows WASAPI Loopback`
- 若本地没有模型，首次使用本地识别时会由应用内下载器准备 `SenseVoice Small`；已有模型不会重复下载

## 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

如需自定义模型缓存目录：

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 首次使用

1. 启动程序：首次启动会自动生成配置，并弹出“使用模式”向导（聊天框翻译 / 手动输入 / 同传语音 / 听别人说话 / VR 头显字幕）
2. 默认的翻译服务（微软 Edge 网页翻译）和语音识别（本地 SenseVoice）都不需要 `API Key`；想换用 DeepL、Claude、OpenAI 等服务时，再到 `设置` 里填写对应的 Key
3. 根据需要设置目标语言、输出格式、麦克风和反向翻译选项
4. 在 VRChat 中启用 `OSC`
   `Action Menu -> Options -> OSC -> Enable`

### 同声传译语音配置（推荐 MixLine）

如果需要让 VRChat 中的其他玩家听到你的同传语音，需要额外配置虚拟麦克风：

1. 安装并启动 [MixLine](https://www.logitechg.com/en-us/software/mixline.html)
2. 在设置中启用“同声传译”，并打开“输出到 VRChat”
3. 将 VRChat 的麦克风设置为 MixLine 暴露的虚拟麦克风
4. 在 MixLine 里把 Mio 的输出和你的真实麦克风混在一起，送进这个虚拟麦克风

## 快捷键（可在设置里修改）

- `Alt+X`：打开文字输入翻译面板
- `Alt+C`：麦克风静音 / 取消静音
- `Alt+T`：截图翻译（桌面直接翻译整个画面）

## 在 VR 头显里使用

Mio 不会自己启动 SteamVR；SteamVR 运行时它会自动接上，也可以在设置里勾选“随 SteamVR 启动”。

- **头显字幕板**：别人说的话和你自己的译文显示在面前的字幕板上（可调大小、字号、底板透明度，可拖动或锁定位置）；译文前的 ● / ◆ 标记区分自己和他人，不只靠颜色
- **双手取景翻译**（和 VRHandsFrame 一样）：两手按住握把、在面前摆成一个框（两手在对角），保持不动片刻，框里的文字就会被翻译并直接显示在原处；取景时扣扳机会把这次翻译留成一块面板
- **翻译面板**：面板一直保留到你关闭；用手柄指着它扣扳机点按钮（原文/译文、图片/文字、字号、翻页、收藏、关闭），指着它按住握把可以拿起来移动
- **手腕面板**：快速扭两下手腕（或设置为“抬腕看手背”“一直显示”）打开，功能与 SteamVR 菜单里的 Mio 标签一致；“翻译记录”页里可以找回本次读过的内容、收拢或关闭所有面板、开关双手取景、重看操作说明
- **按住说话**：可选，在 SteamVR 绑定页给“按住说话”指定一个按键，按住时 Mio 才收音
- 取景、识别、出结果和点按钮时手柄会轻振并有提示音（可在设置里关闭或调音量）
- 取景和操作面板时，扳机和握把不会同时传给游戏（不会在游戏里抓东西或开枪，可在设置里关闭）
- 面板上可以“复制”译文，或“发聊天框”把译文发给附近的朋友
- 框里的译文按原文的字色和底色来画；识别到二维码时，面板上会多一个“打开链接”（在电脑浏览器里打开）
- 取景时推摇杆可以放大缩小取景框；双手抓住面板拉开或合拢可以缩放面板，双手抓着时扣扳机可以翻页或关闭，单手抓着时扣同一只手的扳机把所有面板收拢到面前
- Quest / Rift 手柄连点拇指托（默认 4 下）开关双手取景；其他手柄可以在 SteamVR 绑定页给“开关双手取景”“收拢面板”“关闭全部面板”指定按键
- 提示音可以换成自己的 WAV 文件（设置里有“打开自定义提示音文件夹”）
- 设置里可以选主视眼（框住的和读到的对不上时先检查这里）、调手势灵敏度、扳机 / 握把力度和取景框出现的快慢；姿势总认不出来时可以打开“快速模式”（双手同时按住扳机和握把就开框）

## OSC 控制参数（需在设置中开启“允许角色菜单控制 Mio”）

在角色的 Expression 菜单里加入这些参数，就能在游戏里直接控制 Mio：

| 参数 | 类型 | 作用 |
| --- | --- | --- |
| `MioToggleMic` | Bool | 麦克风开 / 关（关 = 静音，不会停止听别人） |
| `MioToggleListen` | Bool | 听别人说话 开 / 关 |
| `MioToggleTts` | Bool | 同传语音 开 / 关 |
| `MioToggleOverlay` | Bool | 桌面悬浮字幕 开 / 关 |
| `MioToggleVrOverlay` | Bool | 头显字幕板 开 / 关 |
| `MioFrameGesture` | Bool | 双手取景 开 / 关 |
| `MioScreenshot` | Bool | 按下时进行一次截图翻译 |
| `MioTargetLanguage` | Int | 切换翻译目标语言（1 开始的序号，0 表示不变） |

开启“把 Mio 状态发给角色”后，Mio 也会把 `MioTranslating`、`MioSpeaking`、`MioMuted`、`MioError`、`MioTargetLanguage`、`MioOverlayActive`、`MioFraming`（正在取景）、`MioPanelHeld`（正抓着面板）写回角色，供动画使用。

## 隐私说明

- 项目本身不收集用户数据
- 不保存聊天记录
- 默认日志不会记录识别文本、翻译文本或 chatbox 内容
- chatbox 文本在发送后不会长期存储
- Windows 版会使用系统 DPAPI 保护本地保存的 API Key
- 自动界面语言只读取本机区域设置；不会默认使用 IP 定位
- 只有在你启用云端翻译服务时，当前待翻译文本才会发送到你自己配置的 API 服务商

## 补充说明

- VRChat 原生 OSC 不提供其他玩家的原始聊天文本
- 反向翻译依赖本机播放设备回环采集，体验会受系统音频链路影响
- VRChat Chatbox 有发送频率和单条长度限制

## 开发者文档

- [第三方许可证](./THIRD_PARTY_LICENSES.md) - 依赖库许可证信息
- [品牌与非官方分发规则](./BRANDING.md) - Mio 名称、Logo、图标、官网素材和非官方构建说明
- [生产发版流程](./docs/RELEASE_PROCESS.md) - 构建、验证、签名、标签与 GitHub Release 发布步骤
- [发布签名说明](./docs/RELEASE_SIGNING.md) - Ed25519 信任模型、密钥保管、轮换与校验方法

## 许可证与品牌声明

本项目代码从当前版本起采用 [GNU GPLv3-or-later](./LICENSE) 发布。你可以自由使用、学习、修改和分发源码；如果你分发修改版或二进制构建，必须遵守 GPLv3-or-later 的要求，包括保留版权与许可证声明，并提供对应源码。

`Mio RealTime Translator`、`Mio Translator`、项目 Logo、应用图标、官网素材、发布页素材和其他品牌资产不随 GPL 授权。未经许可，不得使用这些名称或素材发布换皮版、付费版、镜像版，或让用户误以为非官方构建是官方版本。详细规则见 [BRANDING.md](./BRANDING.md)。

官方版本永久免费。请优先从 [https://miovrc.com/](https://miovrc.com/) 或官方 GitHub Release 下载。

## 致谢

感谢所有贡献者和用户的支持！
