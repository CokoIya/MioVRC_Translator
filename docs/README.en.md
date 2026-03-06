# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-中文-2ea44f?style=for-the-badge)](../README.md)
[![ja](https://img.shields.io/badge/README-日本語-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> Click badges above to switch language

---

## Overview

**Mio RealTime Translator** is a local real-time voice translation tool for VRChat players, created by VRC player **酒寄 みお**. Fully open-source — paid redistribution of any kind is prohibited.

Core architecture: local ASR (SenseVoice) + switchable AI translation backends + VRChat OSC communication.

---

## Features

- **Local Speech Recognition** — Powered by FunASR / SenseVoice Small. Fully local inference, low latency, works offline.
- **Multiple Translation Backends** — Supports OpenAI, DeepSeek, Qianwen, Anthropic, and any custom OpenAI-compatible API.
- **VRChat Integration** — Sends translated text directly to the VRChat chatbox via the official OSC interface.
- **Reverse Translation** — Listens to incoming chatbox messages and translates them into Chinese, displayed in a floating window.
- **Manual Translation Panel** — Google Translate-style two-pane layout. Type text and send to VRC in one click.
- **Flexible Output Formats** — Choose from `Japanese (Chinese)`, `Japanese only`, `Chinese only`, or `Chinese (Japanese)`.

---

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Or download the Windows EXE from [Releases](https://github.com/CokoIya/MioVRC_Translator/releases) — no Python installation required, ready to run immediately.

> On first launch, SenseVoice Small (~500 MB) will be downloaded automatically.
> Optional: set a custom model cache directory:
> ```powershell
> $env:MODELSCOPE_CACHE = "./models"
> ```

---

## Configuration

1. Copy `config.example.json` to `config.json`.
2. After launch, open `⚙ Settings` and fill in your translation backend (API Key / Base URL / Model).
3. Adjust microphone, VAD silence threshold, target language, and output format as needed.
4. Enable OSC in VRChat: **Action Menu → Options → OSC → Enable**.

---

## Privacy Notice

> **This project performs zero data collection, stores no chat content, and each message is automatically destroyed immediately after sending.**
>
> This is a purely local application — there is no project-owned cloud server. Your API keys are never uploaded or misused.
>
> The app itself contains no analytics, telemetry, or local chat log persistence. Only when you enable a cloud translation backend will the current message be sent to your own configured API provider for translation.

---

## Tech Stack

| Module | Technology |
| --- | --- |
| UI | CustomTkinter |
| Audio Capture | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| Translation | OpenAI SDK / Anthropic SDK |
| VRChat Communication | python-osc (OSC UDP) |

---

## Known Limitations

- VRChat does not expose other users' audio streams — reverse translation relies on chatbox text only.
- VRChat chatbox messages are capped at ~144 characters and are auto-truncated.
- In noisy environments, VAD may trigger incorrectly; adjust sensitivity in `Settings`.
