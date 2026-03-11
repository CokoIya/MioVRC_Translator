# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> 面向 VRChat 用户的本地实时语音翻译工具  
> 作者：`ここ_Mio` / 开源项目 / 禁止收费再分发

## 版本

- 桌面版：`v1.2.1`
- GitHub Releases 轻量版安装器：`v1.2.1_release`

## 简介

**Mio RealTime Translator** 是一款面向 VRChat 的本地实时语音翻译工具，当前核心链路为：

- 本地语音识别：`SenseVoice Small`
- 翻译服务：`GPT` / `DeepSeek` / `GLM` / `Qwen` / `Claude`
- VRChat 通信：`OSC`

## 功能特性

- 本地识别麦克风语音，并将结果发送到 VRChat chatbox
- 内置手动文本翻译面板，可直接输入并发送到 VRC
- 支持多种输出格式：`译文（原文）`、`仅译文`、`仅原文`、`原文（译文）`
- 翻译服务内置预设模型列表，并显示速度、质量和插件建议
- 支持降噪强度、VAD 静音阈值和流式识别参数调节
- 主界面与设置页已做紧凑化桌面 UI 优化，弹窗位置与图标统一
- 支持界面语言切换

## 下载

### GitHub Releases

可以从 [Releases](https://github.com/CokoIya/MioVRC_Translator/releases) 下载最新的 Windows 可执行版本。

`Releases` 中的轻量版 **不内置 SenseVoice Small 模型**。如果首次启动时本地没有模型，程序会自动开始下载，并在底部状态栏显示进度。

### 完整离线模型包

- QQ 1 群：`1077205718`
- QQ 2 群：`756274989`
- 百度网盘：`https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601`

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

如需提前下载模型：

```bash
python download_models.py
```

首次启动时，SenseVoice Small 也会自动下载。若想自定义模型缓存目录，可设置：

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 配置步骤

1. 将 `config.example.json` 复制为 `config.json`
2. 启动程序后，打开右上角 `设置`
3. 选择翻译服务并填写对应的 `API Key`
4. 按需调整麦克风、降噪、VAD、目标语言和输出格式
5. 在 VRChat 中启用 OSC：`Action Menu -> Options -> OSC -> Enable`

## 构建

### Releases 轻量版

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite.ps1
```

### Releases 轻量版安装器

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite_installer.ps1
```

## 隐私说明

- 本项目不收集任何用户数据
- 不保存聊天记录
- chatbox 消息在发送完成后立即丢弃
- 项目本身没有自建云服务
- 只有在启用云端翻译服务时，当前待翻译文本才会发送到你自己配置的 API 服务商

## 技术栈

| 模块 | 技术 |
| --- | --- |
| UI | CustomTkinter |
| 音频输入 | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| 翻译 | OpenAI SDK / Anthropic SDK / OpenAI Compatible API |
| VRChat 通信 | python-osc |

## 已知限制

- VRChat 原生 OSC 不提供其他玩家聊天文本或原始音频，因此当前版本只处理你自己的麦克风和手动文本输入
- VRChat chatbox 单条消息上限约为 144 个字符
- 在嘈杂环境下，仍然可能出现误触发，建议结合降噪强度与 VAD 参数手动调整
