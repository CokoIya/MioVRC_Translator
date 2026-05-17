# TTS (Text-to-Speech) Feature Design

## Overview

Add voice reading capability to read translated text or original text in "original only" mode.

## Requirements

1. **Free** - No cost for users
2. **Global Access** - Works in both mainland China and overseas
3. **Multi-language** - Support Chinese, English, Japanese, Korean, Russian
4. **High Quality** - Good voice quality
5. **Privacy** - Respect user privacy

## Recommended Solution

### Primary: Edge TTS (Microsoft Edge Read Aloud)

**Why Edge TTS?**
- ✅ Completely free
- ✅ Works globally (including mainland China)
- ✅ Excellent voice quality (same as Azure TTS)
- ✅ 100+ languages and voices
- ✅ No API key required
- ✅ Mature Python library: `edge-tts`

**Supported Languages**:
- Chinese (Mandarin): Multiple voices (male/female)
- English: US, UK, AU, etc.
- Japanese: Multiple voices
- Korean: Multiple voices
- Russian: Multiple voices

### Fallback: pyttsx3 (Offline TTS)

**Why pyttsx3?**
- ✅ Completely offline
- ✅ No network required
- ✅ Cross-platform
- ✅ Privacy-friendly

**Use Case**: When network is unavailable or user prefers offline

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Translation Result                       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  TTS Manager  │
                    └───────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Edge TTS    │    │  pyttsx3     │    │  (Future)    │
│  (Primary)   │    │  (Fallback)  │    │   gTTS       │
└──────────────┘    └──────────────┘    └──────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
                    ┌───────────────┐
                    │  Audio Player │
                    └───────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   Speakers    │
                    └───────────────┘
```

## Implementation Plan

### Phase 1: Core TTS Module

1. **Create TTS Module** (`src/tts/`)
   - `base.py` - Base TTS interface
   - `edge_tts_engine.py` - Edge TTS implementation
   - `pyttsx3_engine.py` - pyttsx3 implementation
   - `factory.py` - TTS engine factory
   - `manager.py` - TTS manager with queue

2. **Audio Playback**
   - Use `sounddevice` or `pygame` for playback
   - Support volume control
   - Support playback queue

### Phase 2: UI Integration

1. **Settings Window**
   - TTS enable/disable toggle
   - Engine selection (Edge TTS / pyttsx3)
   - Voice selection dropdown
   - Speed control slider (0.5x - 2.0x)
   - Volume control slider
   - Test button

2. **Main Window**
   - Speaker icon button next to translation result
   - Auto-read toggle (optional)
   - Playback status indicator

### Phase 3: Configuration

Add to `config.json`:

```json
{
  "tts": {
    "enabled": false,
    "engine": "edge",
    "auto_read": false,
    "read_mode": "translated",
    "edge": {
      "voice": "zh-CN-XiaoxiaoNeural",
      "rate": "+0%",
      "volume": "+0%"
    },
    "pyttsx3": {
      "voice": null,
      "rate": 150,
      "volume": 1.0
    },
    "playback": {
      "device": null,
      "volume": 0.8
    }
  }
}
```

## Voice Selection

### Edge TTS Recommended Voices

**Chinese (Mandarin)**:
- `zh-CN-XiaoxiaoNeural` - Female, natural
- `zh-CN-YunxiNeural` - Male, natural
- `zh-CN-YunyangNeural` - Male, news anchor style

**English (US)**:
- `en-US-JennyNeural` - Female, natural
- `en-US-GuyNeural` - Male, natural

**Japanese**:
- `ja-JP-NanamiNeural` - Female, natural
- `ja-JP-KeitaNeural` - Male, natural

**Korean**:
- `ko-KR-SunHiNeural` - Female, natural
- `ko-KR-InJoonNeural` - Male, natural

**Russian**:
- `ru-RU-SvetlanaNeural` - Female, natural
- `ru-RU-DmitryNeural` - Male, natural

## User Experience

### Use Cases

1. **Mic Translation Mode**
   - User speaks → ASR → Translation → TTS reads translation
   - Helps verify translation accuracy
   - Useful for language learning

2. **VRC Listen Mode (Reverse Translation)**
   - VRChat audio → ASR → Translation → TTS reads translation
   - Helps understand what others said
   - Can read original if translation disabled

3. **Manual Text Input**
   - User types text → Translation → TTS reads result
   - Quick translation verification

### UI Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Translation Result: "Hello, how are you?"                   │
│  ┌──────┐  ┌──────┐  ┌──────┐                              │
│  │ 🔊   │  │ Copy │  │ Send │                              │
│  └──────┘  └──────┘  └──────┘                              │
└─────────────────────────────────────────────────────────────┘
     │
     ▼ (Click speaker icon)
┌─────────────────────────────────────────────────────────────┐
│  🔊 Reading: "Hello, how are you?"                          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  [⏸ Pause] [⏹ Stop]                                        │
└─────────────────────────────────────────────────────────────┘
```

## Privacy & Security

1. **Edge TTS**:
   - Text sent to Microsoft servers
   - No user account required
   - No data retention (per Microsoft policy)
   - HTTPS encrypted

2. **pyttsx3**:
   - Completely offline
   - No data sent anywhere
   - 100% private

3. **User Control**:
   - TTS disabled by default
   - Clear indication when TTS is active
   - Easy to disable

## Performance Considerations

1. **Caching**:
   - Cache generated audio for repeated phrases
   - LRU cache with size limit (e.g., 50 MB)
   - Cache key: (text, voice, rate, volume)

2. **Async Processing**:
   - TTS generation in background thread
   - Non-blocking UI
   - Queue management for multiple requests

3. **Network**:
   - Edge TTS requires network
   - Fallback to pyttsx3 if network unavailable
   - Retry logic with exponential backoff

## Dependencies

Add to `requirements.txt`:

```txt
# TTS
edge-tts>=6.1.0,<7.0.0          # Microsoft Edge TTS
pyttsx3>=2.90,<3.0.0            # Offline TTS fallback
```

## Testing

1. **Unit Tests**:
   - TTS engine initialization
   - Voice selection
   - Rate/volume control
   - Cache management

2. **Integration Tests**:
   - Translation → TTS flow
   - Audio playback
   - Queue management

3. **Manual Tests**:
   - Test all supported languages
   - Test network failure scenarios
   - Test audio device switching

## Localization

Add to `src/utils/i18n.py`:

```python
"tts_enable": {
    "zh-CN": "启用语音朗读",
    "en": "Enable Text-to-Speech",
    "ja": "音声読み上げを有効にする",
    "ko": "음성 읽기 활성화",
    "ru": "Включить озвучивание",
},
"tts_engine": {
    "zh-CN": "语音引擎",
    "en": "TTS Engine",
    "ja": "音声エンジン",
    "ko": "TTS 엔진",
    "ru": "Движок TTS",
},
"tts_voice": {
    "zh-CN": "语音音色",
    "en": "Voice",
    "ja": "音声",
    "ko": "음성",
    "ru": "Голос",
},
"tts_speed": {
    "zh-CN": "语速",
    "en": "Speed",
    "ja": "速度",
    "ko": "속도",
    "ru": "Скорость",
},
"tts_test": {
    "zh-CN": "测试",
    "en": "Test",
    "ja": "テスト",
    "ko": "테스트",
    "ru": "Тест",
},
```

## Future Enhancements

1. **More Engines**:
   - Azure TTS (paid, high quality)
   - Coqui TTS (local, open-source)
   - Bark (local, AI-generated)

2. **Advanced Features**:
   - Emotion control
   - Custom voice training
   - SSML support for advanced control

3. **Accessibility**:
   - Screen reader integration
   - Keyboard shortcuts for TTS control
   - Visual feedback for hearing-impaired users

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Edge TTS service down | High | Fallback to pyttsx3 |
| Network unavailable | Medium | Auto-switch to offline TTS |
| Voice quality poor | Low | Provide multiple voice options |
| Latency too high | Medium | Cache + async processing |
| Privacy concerns | Medium | Clear documentation + offline option |

## Success Metrics

1. **Adoption**: % of users who enable TTS
2. **Usage**: Average TTS requests per session
3. **Satisfaction**: User feedback on voice quality
4. **Performance**: Average latency from text to audio
5. **Reliability**: TTS success rate

## Timeline

- **Week 1**: Core TTS module implementation
- **Week 2**: UI integration and testing
- **Week 3**: Documentation and polish
- **Week 4**: Beta testing and feedback

## Conclusion

Edge TTS provides the best balance of:
- ✅ Free
- ✅ Global access
- ✅ High quality
- ✅ Easy integration

With pyttsx3 as fallback, we ensure the feature works even offline. This solution meets all requirements and provides excellent user experience.
