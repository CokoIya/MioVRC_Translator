# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> A local real-time voice translation tool for VRChat users  
> Author: `ここ_Mio` / Open-source project / Paid redistribution is prohibited

## Version

- Desktop release: `v1.2.1`
- GitHub Releases lite installer: `v1.2.1_release`

## Overview

**Mio RealTime Translator** is a local real-time voice translation tool for VRChat. Its current core pipeline is:

- Local speech recognition: `SenseVoice Small`
- Translation services: `GPT` / `DeepSeek` / `GLM` / `Qwen` / `Claude`
- VRChat communication: `OSC`

## Features

- Recognizes microphone input locally and sends the result to the VRChat chatbox
- Built-in manual translation panel for typing and sending text to VRC
- Multiple output formats such as `Translation (Original)`, `Translation only`, `Original only`, and `Original (Translation)`
- Built-in preset model lists for each translation service, with speed, quality, and plugin-fit notes
- Adjustable denoise strength, VAD silence threshold, and streaming recognition parameters
- More compact desktop UI for the main window and settings window, with unified popup positioning and icons
- UI language switching

## Download

### GitHub Releases

You can download the latest Windows build from [Releases](https://github.com/CokoIya/MioVRC_Translator/releases).

The lite release **does not bundle the SenseVoice Small model**. If the model is missing on first launch, the app downloads it automatically and shows progress in the bottom status area.

### Full Offline Package

- QQ Group 1: `1077205718`
- QQ Group 2: `756274989`
- Baidu Netdisk: `https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601`

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

To download the model in advance:

```bash
python download_models.py
```

SenseVoice Small will also be downloaded automatically on first launch. To override the model cache directory:

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## Configuration

1. Copy `config.example.json` to `config.json`
2. Launch the app and open `Settings`
3. Choose a translation service and fill in the corresponding `API Key`
4. Adjust microphone, denoise, VAD, target language, and output format as needed
5. Enable OSC in VRChat: `Action Menu -> Options -> OSC -> Enable`

## Build

### Releases Lite Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite.ps1
```

### Releases Lite Installer

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite_installer.ps1
```

## Privacy

- This project does not collect user data
- It does not store chat logs
- Chatbox messages are discarded immediately after sending
- The project does not operate its own cloud service
- Only when a cloud translation service is enabled will the current text be sent to the API endpoint you configured

## Tech Stack

| Module | Technology |
| --- | --- |
| UI | CustomTkinter |
| Audio Input | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| Translation | OpenAI SDK / Anthropic SDK / OpenAI Compatible API |
| VRChat Communication | python-osc |

## Known Limitations

- Native VRChat OSC does not expose other players' chat text or raw audio, so the current version only handles your own microphone input and manual text input
- A single VRChat chatbox message is limited to about 144 characters
- In noisy environments, false triggers may still happen, so denoise and VAD settings may need manual tuning
