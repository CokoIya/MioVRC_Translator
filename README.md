# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)

> 点击上方徽章可切换语言

## 项目简介

**Mio RealTime Translator** 是一款面向 VRChat 玩家使用的本地实时语音翻译工具，由 VRC 玩家 **酒寄 みお** 制作并开源发布。

核心流程：

- 本地 ASR 语音识别
- 可切换的 AI 翻译后端
- 通过 VRChat OSC 将结果直接发送到聊天框

## 主要功能

- 本地语音识别：基于 `faster-whisper`，支持 `whisper-base` 与 `whisper-small` 本地模型。
- 多翻译后端：支持 OpenAI 兼容接口，也支持 Anthropic。
- VRChat 集成：识别和翻译结果可直接发送到 VRChat 聊天框。
- 反向翻译：监听聊天框文字并翻译为中文，在悬浮窗显示。
- 手动翻译面板：双栏输入输出布局，支持一键发送到 VRC。
- 灵活输出格式：支持 `日语（中文）`、`仅日语`、`仅中文`、`中文（日语）`。

## 快速开始

### 直接运行源码

```bash
pip install -r requirements.txt
python download_models.py
python main.py
```

### 使用 Windows 安装包

前往 [Releases](https://github.com/CokoIya/MioVRC_Translator/releases) 下载最新安装包。

- 安装包文件名：`MioTranslator-Setup-v1.1.2.exe`
- 支持自定义安装路径
- 默认安装目录：`C:\Mio RealTime Translator`
- 安装完成后可创建桌面快捷方式
- 无需单独安装 Python

> 源码运行前需要先执行 `python download_models.py`，将模型下载到项目根目录的 `models/` 中。


## 配置说明

1. 将 `config.example.json` 复制为 `config.json`
2. 启动程序后，打开右上角 `设置`
3. 填写翻译服务配置，例如 `API Key`、`Base URL`、`Model`
4. 根据需要调整麦克风、VAD、目标语言和输出格式
5. 在 VRChat 中启用 OSC：

```text
Action Menu -> Options -> OSC -> Enable
```

## 隐私说明

- 项目本身不收集用户数据。
- 程序不内置统计、埋点或聊天记录持久化。
- 只有在你启用云端翻译后端时，当前待翻译文本才会发送到你自行配置的服务提供方。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| UI | CustomTkinter |
| 音频采集 | sounddevice |
| VAD | webrtcvad |
| 语音识别 | faster-whisper / whisper-base / whisper-small |
| 翻译 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc |

## 已知限制

- VRChat 不开放其他玩家的原始语音流，反向翻译依赖聊天框文本。
- 聊天框单条消息有长度上限，超出会被截断。
- 噪声环境下 VAD 可能误触发，需要在设置中调整灵敏度。
