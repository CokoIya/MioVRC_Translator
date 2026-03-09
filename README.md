# Mio RealTime Translator

> VRC 玩家 `みお_Mio` 制作的本地实时语音翻译工具。  
> 开源项目，禁止任何形式的付费分发。

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)

## 版本信息

- 桌面端版本：`v1.2.0_Beta3.2`
- Releases 轻量安装包版本：`V1.2.0_beta3.2_Releases`

## 下载说明

### GitHub Releases（推荐）

前往 [Releases](https://github.com/CokoIya/MioVRC_Translator/releases) 下载最新 `exe`。  
Releases 轻量版 **不内置 SenseVoice Small 模型**，首次启动后会自动下载模型，并在主界面底部显示下载进度。

### 完整包渠道（内置模型）

- QQ 1 群：`1077205718`
- QQ 2 群：`756274989`
- 百度网盘：<https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601>

## 核心能力

- 本地语音识别：固定使用 `SenseVoice Small`
- 多翻译后端：OpenAI / DeepSeek / Qianwen / Anthropic（OpenAI 兼容接口）
- VRChat 集成：通过 OSC 将翻译结果直发聊天框
- 反向翻译：监听聊天框内容并悬浮显示翻译
- 手动翻译面板：双栏输入/输出，一键发送到 VRC
- 多语言界面：中文、英语、日语、俄语、韩语

## 快速开始（源码运行）

```bash
pip install -r requirements.txt
python main.py
```

可选：如果你希望提前把模型下载到项目目录，而不是首启自动下载：

```bash
python download_models.py
```

## 配置步骤

1. 复制 `config.example.json` 为 `config.json`
2. 启动程序，点击右上角 `设置`
3. 填写翻译后端 `API Key`
4. 在 VRChat 内开启 OSC：`Action Menu -> Options -> OSC -> Enable`

## 打包命令

### Releases 轻量版（无模型）

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite.ps1
```

### Releases 轻量安装包（无模型）

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite_installer.ps1
```

## 隐私声明

- 项目本身不收集用户数据
- 程序不写入聊天日志
- 每条消息发送完成后即销毁
- 仅当你启用云翻译后端时，当前待翻译文本会发送到你自己配置的 API 提供方

## 技术栈

| 模块 | 技术 |
| --- | --- |
| UI | CustomTkinter |
| 音频采集 | sounddevice |
| VAD | webrtcvad |
| 语音识别 | FunASR / SenseVoice Small |
| 翻译 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc |

## 已知限制

- VRChat 不开放其他玩家原始语音流，反向翻译依赖聊天框文本
- 聊天框单条消息长度约 144 字，超出会被截断
- 噪音环境下 VAD 可能误触发，可在设置中调节阈值
