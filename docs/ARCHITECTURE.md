# Architecture Documentation

## Overview

Mio RealTime Translator is a desktop application for real-time voice translation in VRChat. It captures audio from microphone or desktop, performs speech recognition, translates the text, and sends it to VRChat via OSC.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Main Window (UI)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Settings   │  │   History    │  │  Floating    │      │
│  │   Window     │  │   Display    │  │   Window     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│    Audio     │    │     ASR      │    │  Translator  │
│   Capture    │───▶│   Engine     │───▶│   Service    │
└──────────────┘    └──────────────┘    └──────────────┘
        │                                       │
        │                                       ▼
        │                               ┌──────────────┐
        └──────────────────────────────▶│  OSC Sender  │
                                        └──────────────┘
                                               │
                                               ▼
                                        ┌──────────────┐
                                        │   VRChat     │
                                        └──────────────┘
```

## Module Structure

### 1. Audio Module (`src/audio/`)

**Purpose**: Capture and process audio from microphone or desktop.

**Key Components**:
- `recorder.py` - Microphone audio capture with VAD
- `desktop_recorder.py` - Desktop audio capture (loopback)
- `vad_detector.py` - Voice Activity Detection
- `adaptive_denoiser.py` - Noise reduction
- `chunk_streamer.py` - Streaming audio chunking

**Design Patterns**:
- **Observer Pattern**: Callbacks for audio segments and VAD state
- **Strategy Pattern**: Different VAD implementations (WebRTC, Silero)

**Key Features**:
- Multi-rate audio resampling
- Pre-speech buffering to capture initial consonants
- Adaptive noise reduction
- Thread-safe queue-based processing

### 2. ASR Module (`src/asr/`)

**Purpose**: Speech-to-text conversion using SenseVoice.

**Key Components**:
- `sensevoice_asr.py` - SenseVoice ASR implementation
- `model_manager.py` - Model download and caching
- `model_registry.py` - Model metadata registry
- `text_corrections.py` - Post-processing corrections
- `streaming_merger.py` - Merge partial results

**Design Patterns**:
- **Factory Pattern**: `factory.py` creates ASR instances
- **Singleton Pattern**: Model loading (one instance per process)

**Key Features**:
- Automatic model download from ModelScope
- Multi-language support (zh, en, ja, ko, ru, yue)
- Dictionary-based text correction
- Streaming partial results

### 3. Translator Module (`src/translators/`)

**Purpose**: Translate text using various LLM APIs.

**Key Components**:
- `base.py` - Base translator with caching
- `openai_translator.py` - OpenAI-compatible APIs
- `anthropic_translator.py` - Anthropic Claude API
- `factory.py` - Translator factory

**Design Patterns**:
- **Strategy Pattern**: Different translation backends
- **Template Method**: Base class defines translation flow
- **Factory Pattern**: Create translators by backend name

**Supported Backends**:
- OpenAI (GPT-4, GPT-3.5)
- DeepSeek
- Qianwen (Alibaba)
- Zhipu (GLM)
- Gemini (Google)
- Kimi (Moonshot)
- XAI (Grok)
- Mistral
- Doubao (ByteDance)
- Anthropic (Claude)
- Custom endpoints

**Key Features**:
- Translation caching to reduce API costs
- Context-aware translation
- Social mode with persona customization
- Automatic token estimation

### 4. OSC Module (`src/osc/`)

**Purpose**: Send messages to VRChat via OSC protocol.

**Key Components**:
- `sender.py` - VRChat OSC sender

**Key Features**:
- Rate limiting to respect VRChat limits
- Duplicate message suppression
- Avatar parameter synchronization
- Thread-safe message queue

**VRChat OSC Endpoints**:
- `/chatbox/input` - Send text to chatbox
- `/avatar/parameters/{name}` - Set avatar parameters

### 5. UI Module (`src/ui/`)

**Purpose**: User interface using CustomTkinter.

**Key Components**:
- `main_window.py` - Main application window
- `settings_window.py` - Settings dialog
- `floating_window.py` - Floating subtitle window
- `text_input_window.py` - Manual text input
- `update_window.py` - Update notification dialog
- `window_effects.py` - Window styling utilities

**Design Patterns**:
- **MVC Pattern**: Separation of UI and logic
- **Observer Pattern**: UI updates on state changes

**Key Features**:
- Multi-language UI (zh, en, ja, ko, ru)
- Dark/light theme support
- Real-time translation history
- Device selection and configuration

### 6. Utils Module (`src/utils/`)

**Purpose**: Shared utilities and helpers.

**Key Components**:
- `config_manager.py` - Configuration management
- `logger.py` - Logging setup
- `i18n.py` - Internationalization
- `ui_language_detection.py` - Auto-detect UI language
- `global_hotkey.py` - Global hotkey registration

**Key Features**:
- DPAPI encryption for API keys (Windows)
- Atomic config file writes
- Automatic config migration
- Language detection from system locale

### 7. Updater Module (`src/updater/`)

**Purpose**: Check for and download updates.

**Key Components**:
- `update_checker.py` - Version comparison and update checking

**Security Features**:
- SHA256 checksum verification
- HTTPS-only downloads
- Trusted domain whitelist
- Semantic version comparison

## Data Flow

### Microphone Translation Flow

```
1. User speaks into microphone
   ↓
2. AudioRecorder captures audio frames
   ↓
3. VADDetector detects speech activity
   ↓
4. Audio segment collected when speech ends
   ↓
5. SenseVoiceASR transcribes audio to text
   ↓
6. Text corrections applied (dictionary)
   ↓
7. Translator translates text
   ↓
8. VRCOSCSender sends to VRChat chatbox
   ↓
9. UI displays in history
```

### Desktop Audio (Reverse Translation) Flow

```
1. VRChat plays audio through speakers
   ↓
2. DesktopAudioRecorder captures loopback audio
   ↓
3. SileroVAD detects speech (robust to music/SFX)
   ↓
4. Audio segment collected
   ↓
5. SenseVoiceASR transcribes
   ↓
6. Translator translates
   ↓
7. Display in floating window or chatbox
```

## Threading Model

### Main Thread
- UI event loop (CustomTkinter)
- User interactions
- Configuration updates

### Audio Capture Thread
- Runs `sounddevice` callback
- Enqueues audio frames

### Audio Processing Thread
- Dequeues audio frames
- Runs VAD
- Collects segments
- Calls ASR

### OSC Sender Thread
- Dequeues OSC messages
- Rate limiting
- Sends UDP packets

### Update Checker Thread
- Background update check
- Retries on failure

## Configuration Management

### Config File Location
- Windows: `%APPDATA%\MioTranslator\config.json`
- Portable: `./config.json`

### Config Structure
```json
{
  "audio": { ... },
  "asr": { ... },
  "translation": {
    "backend": "qianwen",
    "openai": {
      "api_key": "dpapi:v1:...",  // Encrypted
      "model": "gpt-4"
    }
  },
  "vrc_listen": { ... },
  "osc": { ... },
  "ui": { ... }
}
```

### Security
- API keys encrypted with Windows DPAPI
- Atomic writes (temp file + rename)
- Automatic backup on corruption
- Config validation and migration

## Model Management

### Model Storage
- Default: `./runtime_models/`
- Can be overridden with `MODELSCOPE_CACHE` env var

### Model Download
1. Check if model exists locally
2. Download from ModelScope if missing
3. Verify integrity (file list check)
4. Load into memory

### Bundled Models
- Release builds include SenseVoice Small
- Reduces first-run download time

## Error Handling Strategy

### Recoverable Errors
- Log warning
- Show user-friendly message
- Continue operation
- Examples: Network timeout, invalid config value

### Fatal Errors
- Log error with stack trace
- Show error dialog
- Graceful shutdown
- Examples: Missing dependencies, model load failure

### Error Categories
1. **Configuration Errors** - Invalid settings, missing keys
2. **Network Errors** - API timeouts, connection failures
3. **Audio Errors** - Device not found, capture failure
4. **Model Errors** - Download failure, load failure
5. **Translation Errors** - API errors, quota exceeded

## Performance Considerations

### Memory Management
- Audio buffers limited by time window
- Translation cache with LRU eviction
- Model loaded once, reused

### CPU Optimization
- VAD runs on audio thread (lightweight)
- ASR runs on separate thread
- Resampling uses scipy when available

### Network Optimization
- Translation caching reduces API calls
- Batch OSC messages when possible
- Retry with exponential backoff

## Security Considerations

### API Key Protection
- Encrypted at rest (DPAPI on Windows)
- Never logged
- Transmitted over HTTPS only

### Update Security
- SHA256 checksum verification
- Trusted domain whitelist
- HTTPS-only downloads

### Privacy
- No telemetry or analytics
- No data sent to third parties (except chosen translation API)
- Logs don't contain sensitive data

## Testing Strategy

### Unit Tests
- Configuration management
- Version comparison
- Update checker
- Text corrections

### Integration Tests
- Audio capture → ASR
- ASR → Translation
- Translation → OSC

### Manual Testing
- Device compatibility
- VRChat integration
- Multi-language support

## Build and Release

### Build Process
1. Run PyInstaller with `MioTranslator.spec`
2. Bundle models (full) or skip (lite)
3. Create installer with Inno Setup
4. Generate SHA256 checksum
5. Upload to CDN
6. Update `latest.json` manifest

### Release Channels
- **Stable** - Tested releases
- **Beta** - Pre-release testing

### Version Scheme
- Semantic versioning: `MAJOR.MINOR.PATCH`
- Pre-release: `1.2.3-beta1`, `1.2.3-rc1`

## Future Improvements

### Planned Features
- Linux/macOS support
- GPU acceleration option
- More translation backends
- Custom ASR models
- Plugin system

### Technical Debt
- Increase test coverage
- Add type stubs for dependencies
- Refactor UI into smaller components
- Add performance profiling

## References

- [VRChat OSC Documentation](https://docs.vrchat.com/docs/osc-overview)
- [SenseVoice Model](https://github.com/FunAudioLLM/SenseVoice)
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
