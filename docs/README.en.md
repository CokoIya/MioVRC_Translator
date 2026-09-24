# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)
[![Stable / Beta Download](https://img.shields.io/badge/Stable%20%2F%20Beta%20Download-miovrc.com-ff6b35?style=for-the-badge)](https://miovrc.com)

**Official download: <https://miovrc.com>**

> A local real-time translation tool for VRChat users
> Author: `ここ_Mio` / Official builds are free / Source code is GPLv3-or-later
> Download site: <https://miovrc.com>

## Overview

**Mio RealTime Translator** is a desktop translation tool built for VRChat. It mainly focuses on two use cases:

- Translate your own speech and send it to the `VRChat Chatbox`
- Reverse-translate speech you hear in VRChat so it is easier to understand and verify

Current main pipelines:

- Microphone translation: `Microphone -> ASR -> Translation -> VRChat Chatbox`
- Reverse translation: `VRChat audio -> ASR -> Translation -> Chatbox / Floating display`

## Download

- Official download site: <https://miovrc.com>
- Stable builds, beta builds, and all future updates are distributed through the official website
- GitHub Releases provide the same single production installer as the official website
- New installations and upgrades use the same installer; local models download on demand and existing model files are reused
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
- **In the headset**: a subtitle board, a SteamVR menu tab, a wrist panel, and VRHandsFrame-style two-hand frame translation

## Requirements

- Recommended OS: `Windows 10 / 11`
- Reverse translation relies on `Windows WASAPI Loopback`
- The installer does not bundle SenseVoice or TTS voice models. The in-app downloader prepares each local model when it is first needed, and existing model files are not downloaded again.

## Run From Source

```bash
pip install -r requirements.txt
python main.py
```

Voice and ASR models are managed by the app at runtime. Launch the app and use the in-app model download windows when a model is missing.

To override the model cache directory:

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## First-Time Setup

1. Launch the app: the first start creates the configuration and opens the mode wizard (chatbox translation / typed input / voice output / listening to others / headset subtitles)
2. The default translation service (Microsoft Edge web translation) and speech recognition (local SenseVoice) need no `API Key`; add a key in `Settings` only if you switch to DeepL, Claude, OpenAI and the like
3. Set your target language, output format, microphone, and reverse-translation options as needed
4. Enable `OSC` in VRChat
   `Action Menu -> Options -> OSC -> Enable`

## Hotkeys (change them in Settings)

- `Alt+X`: open the text input translation panel
- `Alt+C`: mute / unmute the microphone
- `Alt+T`: screenshot translation (on the desktop it reads the whole view)

## In the Headset

Mio never launches SteamVR itself; it attaches when SteamVR is running, and can register itself to start with SteamVR if you tick that in Settings.

- **Subtitle board**: what others say and your own translations float in front of you (size, text size, plate opacity, drag or lock). A ● / ◆ mark before each translation tells your lines from others' without relying on colour.
- **Two-hand frame** (as in VRHandsFrame): hold both grips and hold your hands up at opposite corners of a sign; keep still for a moment and the text inside is translated right where it is. Pull a trigger while framing to keep that translation as a panel.
- **Panels**: kept until you close them. Point and pull the trigger to use the buttons (original/translation, picture/text, text size, pages, keep, close); point and hold the grip to pick one up and move it.
- **Wrist panel**: twist your wrist twice (or choose "look at the wrist" / "always shown"). Same controls as the Mio tab in the SteamVR menu; its Reads page brings back what you read this session, gathers or closes all panels, switches the hand frame and replays the tutorial.
- **Push to talk** (optional): bind a button to "Push to talk" in the SteamVR binding page; Mio only hears the mic while it is held.
- The controllers buzz and a short sound plays when the frame appears, a read starts, a result arrives and a button is clicked (switchable in Settings).
- While you frame or work a panel, trigger and grip are kept from the game (no grabbing or firing by accident; switchable in Settings).
- Panels can copy their text or send the translation to the chatbox for the people around you.
- Translations inside the frame are drawn in the sign's own colours; a QR code adds an "Open link" button to the panel (opened in the PC browser).
- Push the stick while framing to scale the frame; hold a panel with both hands and pull them apart or together to resize it, pull a trigger with both hands on it to turn a page or close it, and pull the trigger of the one hand holding a panel to gather every panel in front of you.
- Tap the thumb rest of Quest / Rift controllers (4 times by default) to switch the hand frame; on other controllers bind "Switch the hand frame", "Gather the panels" and "Close every panel" in the SteamVR binding page.
- The cue sounds can be replaced with your own WAV files (Settings has a button that opens the folder).
- Settings pick your dominant eye (check it first if reads land beside what you framed) and tune gesture sensitivity, trigger / grip pull and how quickly the frame appears; if the pose is rarely recognised, turn on quick mode (trigger + grip on both hands opens a frame).

## OSC Controls (turn on "Allow avatar controls" in Settings)

| Parameter | Type | Effect |
| --- | --- | --- |
| `MioToggleMic` | Bool | Microphone on / off (off mutes; listening to others continues) |
| `MioToggleListen` | Bool | Listen to others on / off |
| `MioToggleTts` | Bool | Voice output on / off |
| `MioToggleOverlay` | Bool | Desktop floating subtitles on / off |
| `MioToggleVrOverlay` | Bool | Headset subtitle board on / off |
| `MioFrameGesture` | Bool | Two-hand frame on / off |
| `MioScreenshot` | Bool | One screenshot translation per press |
| `MioTargetLanguage` | Int | Pick the target language (1-based; 0 leaves it) |

With "Enable Avatar Sync" on, Mio also writes `MioTranslating`, `MioSpeaking`, `MioMuted`, `MioError`, `MioTargetLanguage`, `MioOverlayActive`, `MioFraming` (framing) and `MioPanelHeld` (holding a panel) back to the avatar for animations.

## Privacy

- The project does not collect user data
- It does not store chat logs
- Default logs do not record recognized speech, translated text, or chatbox contents
- Chatbox text is not kept after sending
- On Windows, saved API keys are protected with the system DPAPI
- Automatic UI language detection only reads the local system locale; IP geolocation is off by default
- Only when you enable a cloud translation service will the current text be sent to the API provider you configured

## Notes

- Native VRChat OSC does not expose other players' raw chat text
- Reverse translation depends on loopback capture from your local playback device, so system audio routing matters
- VRChat Chatbox has rate limits and per-message length limits

## License and Branding

The project source code is released under [GNU GPLv3-or-later](../LICENSE) from the current version onward. If you distribute modified source code or binary builds, you must follow GPLv3-or-later and provide the corresponding source code.

`Mio RealTime Translator`, `Mio Translator`, the logo, app icon, official website materials, and release assets are not licensed under the GPL. Unofficial builds must use a different name and assets, and must clearly state that they are not official releases. See [BRANDING.md](../BRANDING.md) for details.
