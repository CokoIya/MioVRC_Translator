# TTS 功能实现总结

## 功能概述

为 Mio RealTime Translator 添加了 **AI 语音朗读**功能，可以朗读翻译结果或原文。

## 实现方案

### ✅ 已完成的核心模块

#### 1. TTS 基础架构 (`src/tts/`)

**文件结构**:
```
src/tts/
├── __init__.py              # 包初始化
├── base.py                  # TTS 基类和接口定义
├── edge_tts_engine.py       # Edge TTS 引擎（主推）
├── pyttsx3_engine.py        # pyttsx3 离线引擎（备选）
├── factory.py               # TTS 引擎工厂
└── manager.py               # TTS 管理器（队列、缓存、播放）
```

#### 2. Edge TTS 引擎 ⭐⭐⭐⭐⭐

**优点**:
- ✅ **完全免费** - 无需 API Key
- ✅ **全球可用** - 中国大陆和海外都能访问
- ✅ **音质优秀** - 微软 Azure TTS 同款
- ✅ **多语言支持** - 100+ 语言和音色
- ✅ **无隐私问题** - 不需要账号

**支持的语言**:
- 中文（普通话）：多种音色（男/女）
- 英文：美式、英式、澳式等
- 日文：多种音色
- 韩文：多种音色
- 俄文：多种音色

**推荐音色**:
```python
RECOMMENDED_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",  # 中文女声
    "en": "en-US-JennyNeural",      # 英文女声
    "ja": "ja-JP-NanamiNeural",     # 日文女声
    "ko": "ko-KR-SunHiNeural",      # 韩文女声
    "ru": "ru-RU-SvetlanaNeural",   # 俄文女声
}
```

#### 3. pyttsx3 离线引擎 ⭐⭐⭐⭐

**优点**:
- ✅ **完全离线** - 无需网络
- ✅ **完全免费** - 无任何费用
- ✅ **隐私友好** - 数据不离开本地
- ✅ **跨平台** - Windows/Linux/macOS

**用途**:
- 网络不可用时的备选方案
- 注重隐私的用户
- 离线环境使用

#### 4. TTS 管理器

**功能**:
- ✅ **队列管理** - 支持多个朗读请求排队
- ✅ **智能缓存** - 缓存已生成的音频（最大 50MB）
- ✅ **异步处理** - 不阻塞 UI
- ✅ **音频播放** - 支持 WAV 和 MP3 格式
- ✅ **自动降级** - Edge TTS 不可用时自动切换到 pyttsx3

**缓存策略**:
- LRU（最近最少使用）淘汰
- 最大缓存大小：50 MB
- 最大缓存项数：100 个
- 缓存键：(文本, 音色, 语速, 音量)

## 依赖更新

### requirements.txt

新增依赖：
```txt
# TTS (Text-to-Speech)
edge-tts>=6.1.0,<7.0.0       # Microsoft Edge TTS (primary)
pyttsx3>=2.90,<3.0.0         # Offline TTS fallback
pydub>=0.25.1,<1.0.0         # Audio format conversion for MP3 playback
```

## 使用场景

### 1. 麦克风翻译模式
```
用户说话 → ASR → 翻译 → TTS 朗读翻译结果
```
- 帮助验证翻译准确性
- 适合语言学习

### 2. VRC 反向翻译模式
```
VRChat 音频 → ASR → 翻译 → TTS 朗读翻译结果
```
- 帮助理解他人说的话
- 可选朗读原文（如果禁用翻译）

### 3. 手动文本输入
```
用户输入文本 → 翻译 → TTS 朗读结果
```
- 快速验证翻译

## 待完成的工作

### 🔴 高优先级

1. **UI 集成 - 设置窗口**
   - [ ] TTS 启用/禁用开关
   - [ ] 引擎选择（Edge TTS / pyttsx3）
   - [ ] 音色选择下拉框
   - [ ] 语速控制滑块（0.5x - 2.0x）
   - [ ] 音量控制滑块
   - [ ] 测试按钮

2. **UI 集成 - 主窗口**
   - [ ] 翻译结果旁边添加 🔊 按钮
   - [ ] 自动朗读开关（可选）
   - [ ] 播放状态指示器

3. **配置管理**
   - [ ] 添加 TTS 配置到 `config.json`
   - [ ] 配置验证和迁移

4. **国际化**
   - [ ] 添加 TTS 相关的翻译字符串

### 🟡 中优先级

5. **测试**
   - [ ] 单元测试（TTS 引擎、管理器）
   - [ ] 集成测试（翻译 → TTS 流程）
   - [ ] 手动测试（所有语言）

6. **文档**
   - [ ] 用户文档（如何使用 TTS）
   - [ ] 更新 README

### 🟢 低优先级

7. **高级功能**
   - [ ] 情感控制
   - [ ] 自定义音色训练
   - [ ] SSML 支持

## 配置示例

### config.json

```json
{
  "tts": {
    "enabled": false,
    "engine": "edge",
    "auto_read": false,
    "read_mode": "translated",
    "edge": {
      "voice": "zh-CN-XiaoxiaoNeural",
      "rate": 1.0,
      "volume": 0.8
    },
    "pyttsx3": {
      "voice": null,
      "rate": 150,
      "volume": 1.0
    }
  }
}
```

## 技术亮点

### 1. 自动降级机制
```python
def create_tts_engine_with_fallback(preferred: str = "edge"):
    # 尝试首选引擎
    engine = create_tts_engine(preferred)
    if engine is not None:
        return engine

    # 自动降级到备选引擎
    for fallback in ["edge", "pyttsx3"]:
        engine = create_tts_engine(fallback)
        if engine is not None:
            return engine

    return None
```

### 2. 智能缓存
```python
def _generate_cache_key(text, voice, rate, volume):
    key_str = f"{text}|{voice}|{rate:.2f}|{volume:.2f}"
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()
```

### 3. 异步处理
```python
# Edge TTS 使用 asyncio
async def _synthesize_async(text, voice, rate, volume):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    audio_buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
    return audio_buffer.getvalue()
```

### 4. 多格式音频支持
```python
def _play_audio(audio_data):
    if audio_data.startswith(b"RIFF"):
        # WAV format (pyttsx3)
        audio_array, sample_rate = _decode_wav(audio_data)
    elif audio_data.startswith(b"ID3"):
        # MP3 format (Edge TTS)
        audio_array, sample_rate = _decode_mp3(audio_data)
```

## 性能优化

1. **缓存命中率** - 重复文本无需重新合成
2. **异步处理** - 不阻塞 UI 线程
3. **队列管理** - 平滑处理多个请求
4. **内存限制** - 缓存大小限制防止内存溢出

## 隐私和安全

### Edge TTS
- 文本发送到微软服务器
- 不需要用户账号
- 不保留数据（根据微软政策）
- HTTPS 加密传输

### pyttsx3
- 完全离线
- 数据不发送到任何地方
- 100% 隐私

### 用户控制
- TTS 默认禁用
- 明确指示 TTS 活动状态
- 易于禁用

## 下一步行动

1. **立即可做**:
   - 安装依赖：`pip install edge-tts pyttsx3 pydub`
   - 测试 TTS 引擎是否工作

2. **UI 集成**:
   - 修改 `src/ui/settings_window.py` 添加 TTS 设置
   - 修改 `src/ui/main_window.py` 添加朗读按钮

3. **配置集成**:
   - 修改 `config.example.json` 添加 TTS 配置
   - 修改 `src/utils/config_manager.py` 添加配置验证

4. **国际化**:
   - 修改 `src/utils/i18n.py` 添加 TTS 翻译

## 测试方法

### 快速测试

```python
# 测试 Edge TTS
from src.tts.edge_tts_engine import EdgeTTS

tts = EdgeTTS()
if tts.is_available():
    voices = tts.get_available_voices()
    print(f"Found {len(voices)} voices")

    # 测试中文
    audio = tts.synthesize("你好，世界", "zh-CN-XiaoxiaoNeural")
    print(f"Generated {len(audio)} bytes")

# 测试 pyttsx3
from src.tts.pyttsx3_engine import Pyttsx3TTS

tts = Pyttsx3TTS()
if tts.is_available():
    voices = tts.get_available_voices()
    print(f"Found {len(voices)} voices")

# 测试管理器
from src.tts.manager import TTSManager

manager = TTSManager(engine_name="edge")
manager.start()
manager.speak("Hello, world!", "en-US-JennyNeural")
time.sleep(3)
manager.stop()
```

## 总结

✅ **核心 TTS 模块已完成**:
- Edge TTS 引擎（主推）
- pyttsx3 离线引擎（备选）
- TTS 管理器（队列、缓存、播放）
- 自动降级机制

🔄 **待完成**:
- UI 集成
- 配置管理
- 国际化
- 测试

📊 **代码统计**:
- 新增文件：6 个
- 代码行数：约 800 行
- 支持语言：100+ 种
- 支持音色：数百种

🎯 **满足需求**:
- ✅ 免费
- ✅ 全球可用（中国大陆 + 海外）
- ✅ 高音质
- ✅ 多语言
- ✅ 隐私友好

---

**实现日期**: 2026-05-02
**状态**: 核心模块完成，待 UI 集成
