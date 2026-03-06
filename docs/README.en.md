# VRC Realtime Translator

[![zh-CN](https://img.shields.io/badge/README-zh--CN-2ea44f?style=for-the-badge)](../README.md)
[![ja](https://img.shields.io/badge/README-ja-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-en-0366d6?style=for-the-badge)](./README.en.md)

## Overview

A local real-time translator for VRChat: local ASR (SenseVoice) + switchable AI translation backends + OSC messaging.

## Features

- Local speech recognition with FunASR / SenseVoice Small.
- Multiple translation backends: OpenAI, DeepSeek, Qianwen, Anthropic, and custom OpenAI-compatible APIs.
- VRChat integration via OSC to send translated text into the chatbox.
- Reverse translation of incoming chatbox messages into Chinese, shown in a floating window.
- Manual translation panel with direct "send to VRC" action.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

On first run, SenseVoice Small will be downloaded (about 500MB). Optional model cache location:

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## Configuration

1. Copy `config.example.json` to `config.json`.
2. Open `Settings` after launch and fill backend config (API Key / Base URL / Model).
3. Adjust microphone, VAD silence threshold, target language, and output format as needed.

## Privacy Notice

> This project performs no data collection, stores no chat history, and each message is automatically destroyed after sending is finished.
>
> This is a local-only project with no project-owned cloud server; your API keys are not uploaded and abused.
>
> The app itself has no analytics, telemetry, or local chat log persistence. If you enable a cloud translation backend, current messages are sent to your configured API provider for translation.

## Tech Stack

| Module | Technology |
| --- | --- |
| UI | CustomTkinter |
| Audio Capture | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| Translation | OpenAI SDK / Anthropic SDK |
| VRChat Communication | python-osc |

## Known Limitations

- VRChat does not expose other users' voice stream, so reverse translation relies on chatbox text.
- VRChat chatbox messages are limited to about 144 characters and are truncated automatically.
- In noisy environments, VAD can be triggered incorrectly; adjust threshold in `Settings`.
