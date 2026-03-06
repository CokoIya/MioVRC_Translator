# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-中文-2ea44f?style=for-the-badge)](./README.md)
[![ja](https://img.shields.io/badge/README-日本語-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)

> 点击上方徽章切换语言 / Click badges above to switch language

---

## 项目简介

**Mio RealTime Translator** 是一款面向 VRChat 玩家的本地实时语音翻译工具，由 VRC 玩家 **酒寄 みお** 制作，完全开源，禁止任何形式的收费分发。

核心架构：本地 ASR（SenseVoice）+ 可切换 AI 翻译后端 + VRChat OSC 通信。

---

## 核心功能

- **本地语音识别** — 使用 FunASR / SenseVoice Small，全程本地推理，低延迟，无需联网。
- **多翻译后端** — 支持 OpenAI、DeepSeek、Qianwen、Anthropic 及任意 OpenAI 兼容接口。
- **VRChat 集成** — 通过官方 OSC 接口将翻译结果直接发送到 VRChat 聊天框。
- **反向翻译** — 监听他人聊天框文本并翻译为中文，显示在悬浮窗口。
- **手动翻译面板** — Google Translate 风格双栏布局，支持输入文本后一键发送到 VRC。
- **灵活输出格式** — 可选 `日语（中文）`、`仅日语`、`仅中文`、`中文（日语）` 四种格式。

---

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

也可以直接前往 [Releases](https://github.com/CokoIya/MioVRC_Translator/releases) 下载 Windows 安装包（`MioTranslator-Setup-v1.0.0.exe`），双击安装，选择路径，自动生成桌面快捷方式，无需 Python 环境。

> 首次运行会自动下载 SenseVoice Small 模型（约 500 MB）。
> 可选设置模型缓存目录：
> ```powershell
> $env:MODELSCOPE_CACHE = "./models"
> ```

---

## 配置说明

1. 将 `config.example.json` 复制为 `config.json`。
2. 启动程序后，在 `⚙ 设置` 中填写翻译后端参数（API Key / Base URL / Model）。
3. 按需调整麦克风设备、VAD 静音阈值、目标语言和输出格式。
4. 在 VRChat 中开启 OSC：**Action Menu → Options → OSC → Enable**。

---

## 隐私声明

> **本项目无任何数据收集，不保存任何聊天内容，每条聊天内容发送结束都会自动销毁。**
>
> 本项目为纯本地项目，没有项目自有的云服务器，用户的任何 API Key 均不会被上传或滥用。
>
> 软件本身不含任何统计、埋点或聊天日志写盘逻辑。仅当你启用云端翻译后端时，当前待翻译消息才会发送到你自己配置的 API 服务提供商完成翻译。

---

## 技术栈

| 模块 | 技术 |
| --- | --- |
| UI | CustomTkinter |
| 音频采集 | sounddevice |
| 语音活动检测 | webrtcvad |
| 语音识别 | FunASR / SenseVoice Small |
| 翻译 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc (OSC UDP) |

---

## 已知限制

- VRChat 不开放他人语音流，反向翻译依赖聊天框文本。
- VRChat 聊天框单条消息上限约 144 字符，超出会自动截断。
- 噪声环境中 VAD 可能误触发，建议在 `设置` 中调整灵敏度阈值。
