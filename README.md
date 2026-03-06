# VRC Realtime Translator

[![zh-CN](https://img.shields.io/badge/README-zh--CN-2ea44f?style=for-the-badge)](./README.md)
[![ja](https://img.shields.io/badge/README-ja-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-en-0366d6?style=for-the-badge)](./docs/README.en.md)

中文版本（默认）

## 项目简介

用于 VRChat 的本地实时语音翻译工具：本地 ASR（SenseVoice）+ 可切换 AI 翻译后端 + OSC 消息收发。

## 核心功能

- 本地语音识别：使用 FunASR / SenseVoice Small，本地推理，低延迟。
- 多翻译后端：OpenAI、DeepSeek、Qianwen、Anthropic，以及自定义 OpenAI 兼容接口。
- VRChat 集成：通过 OSC 将翻译结果发送到 VRChat 聊天框。
- 反向翻译：监听聊天框文本并翻译为中文，可显示在悬浮窗。
- 手动翻译面板：可输入文本、指定目标语言并一键发送到 VRC。

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

首次运行会下载 SenseVoice Small 模型（约 500MB）。可选设置模型缓存目录：

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 配置说明

1. 将 `config.example.json` 复制为 `config.json`。
2. 启动程序后，在 `Settings` 中填写翻译后端参数（API Key / Base URL / Model）。
3. 按需调整麦克风设备、VAD 静音阈值、目标语言和输出格式。

## 隐私声明

> 本项目无任何数据收集，不保存任何聊天内容，每条聊天内容发送结束都会自动销毁。
>
> 本项目为本地项目，没有云服务器，用户的任何 API Key 均不会被上传并滥用。
>
> 项目本身不包含统计、埋点或聊天日志写盘。仅当你启用云端翻译后端时，当前消息会发送到你配置的 API 服务完成翻译。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| UI | CustomTkinter |
| 音频采集 | sounddevice |
| 语音活动检测 | webrtcvad |
| 语音识别 | FunASR / SenseVoice Small |
| 翻译 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc |

## 已知限制

- VRChat 不开放他人语音流，反向翻译依赖聊天框文本。
- VRChat 聊天框单条消息上限约 144 字符，超出会自动截断。
- 噪声环境中 VAD 可能误触发，建议在 `Settings` 调整阈值。
