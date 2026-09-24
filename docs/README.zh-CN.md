# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)
[![稳定版 / Beta 下载](https://img.shields.io/badge/稳定版%20%2F%20Beta%20下载-miovrc.com-ff6b35?style=for-the-badge)](https://miovrc.com)

**官网下载安装：<https://miovrc.com>**

> 面向 VRChat 用户的本地实时语音翻译工具
> 作者：`ここ_Mio` / 官方版本永久免费 / 代码采用 GPLv3-or-later 开源
> 下载官网：<https://miovrc.com>

## 简介

**Mio RealTime Translator** 是一款面向 VRChat 的桌面实时翻译工具，重点解决两类场景：

- 自己说的话，快速翻译后发到 `VRChat Chatbox`
- 听到别人的语音后，快速做反向翻译，方便理解和确认内容

当前主要链路：

- 麦克风翻译：`麦克风 -> ASR -> 翻译 -> VRChat Chatbox`
- 反向翻译：`VRChat 音频 -> ASR -> 翻译 -> 聊天框 / 悬浮显示`

## 下载

- 官方下载站：<https://miovrc.com>
- 稳定版、beta 测试版，以及未来所有更新，统一在官网提供下载
- GitHub Release 同步提供与官网相同的单一正式版安装包
- 新玩家与老玩家升级使用同一个安装包；本地模型按需下载，已有模型会被检测并复用
- GitHub 仓库主要用于源代码、问题反馈和开发记录

## 功能亮点

- 本地语音识别与实时翻译
- 翻译结果可发送到 `VRChat Chatbox`
- 支持反向翻译，监听 VRChat 播放音频并翻译
- 内置手动文本翻译面板
- 支持多种聊天框输出格式
- 支持多语言界面
- 支持 ASR 词典、自声抑制、降噪、VAD 与识别参数调节
- 支持 `Avatar / OSC` 参数同步
- 支持可选悬浮窗显示
- **VR 头显内使用**：头显字幕板、SteamVR 菜单标签、手腕面板，以及像 VRHandsFrame 一样的双手取景翻译

## 运行环境

- 推荐系统：`Windows 10 / 11`
- 反向翻译依赖 `Windows WASAPI Loopback`
- 安装包不内置 `SenseVoice Small` 或 TTS 语音模型；首次使用相应本地功能时由应用内下载器按需准备，已有模型不会重复下载。

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

## 许可证与品牌声明

本项目代码从当前版本起采用 [GNU GPLv3-or-later](../LICENSE) 发布。分发修改版或二进制构建时，必须遵守 GPLv3-or-later，并提供对应源码。

`Mio RealTime Translator`、`Mio Translator`、Logo、图标、官网素材和发布素材不随 GPL 授权。非官方构建必须使用不同名称和素材，并明确标注不是官方版本。详细规则见 [BRANDING.md](../BRANDING.md)。
