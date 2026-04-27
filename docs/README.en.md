# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)
[![Stable / Beta Download](https://img.shields.io/badge/Stable%20%2F%20Beta%20Download-78hejiu.top-ff6b35?style=for-the-badge)](https://78hejiu.top)

**Official download: <https://78hejiu.top>**

> A local real-time translation tool for VRChat users  
> Author: `ここ_Mio` / Open-source project / No paid redistribution  
> Download site: <https://78hejiu.top>

## Overview

**Mio RealTime Translator** is a desktop translation tool built for VRChat. It mainly focuses on two use cases:

- Translate your own speech and send it to the `VRChat Chatbox`
- Reverse-translate speech you hear in VRChat so it is easier to understand and verify

Current main pipelines:

- Microphone translation: `Microphone -> ASR -> Translation -> VRChat Chatbox`
- Reverse translation: `VRChat audio -> ASR -> Translation -> Chatbox / Floating display`

## Download

- Official download site: <https://78hejiu.top>
- Stable builds, beta builds, and all future updates are distributed through the official website
- GitHub Releases also provide the `full` installer and the `lite` update installer
- New players should download `full`; existing users and online updates can use `lite`
- GitHub is now mainly used for source code, issue tracking, and development history

## Highlights

- Local speech recognition and live translation
- Send translated text to the `VRChat Chatbox`
- Reverse translation by listening to VRChat playback audio
- Built-in manual text translation panel
- Multiple chatbox output formats
- Multilingual UI
- ASR dictionary, self-voice suppression, denoise, VAD, and recognition tuning
- `Avatar / OSC` parameter sync
- Optional floating window display

## Requirements

- Recommended OS: `Windows 10 / 11`
- Reverse translation relies on `Windows WASAPI Loopback`
- If the model is missing locally, `SenseVoice Small` will be downloaded automatically on first launch
- The `full` installer includes the `SenseVoice Small` model. The `lite` installer is intended for existing installs that already have the model.

## Run From Source

```bash
pip install -r requirements.txt
python main.py
```

To download the model in advance:

```bash
python download_models.py
```

To override the model cache directory:

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## First-Time Setup

1. Copy `config.example.json` to `config.json`
2. Launch the app and open `Settings`
3. Choose a translation service and enter the corresponding `API Key`
4. Set your target language, output format, microphone, and reverse-translation options as needed
5. Enable `OSC` in VRChat  
   `Action Menu -> Options -> OSC -> Enable`

## Privacy

- The project does not collect user data
- It does not store chat logs
- Chatbox text is not kept after sending
- Only when you enable a cloud translation service will the current text be sent to the API provider you configured

## Notes

- Native VRChat OSC does not expose other players' raw chat text
- Reverse translation depends on loopback capture from your local playback device, so system audio routing matters
- VRChat Chatbox has rate limits and per-message length limits
