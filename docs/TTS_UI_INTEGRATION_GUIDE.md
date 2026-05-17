# TTS UI 集成实现指南

## 已完成的工作

✅ 添加了 Google TTS (gTTS) 引擎
✅ 更新了 requirements.txt
✅ 更新了 config.example.json（删除 Avatar 同步，添加 TTS 配置）
✅ 添加了国际化文本（中英文）

## 待实现的 UI 集成

### 1. 设置窗口 - 删除 Avatar 同步折叠栏

**文件**: `src/ui/settings_window.py`

**需要删除的代码**:
```python
# 搜索并删除包含 "avatar_sync" 或 "Avatar 同步" 的所有代码
# 大约在 settings_window.py 中的某个位置
```

**查找关键词**:
- `avatar_sync`
- `Avatar 同步`
- `MioTranslating`
- `MioSpeaking`

### 2. 设置窗口 - 添加 TTS 折叠栏

**文件**: `src/ui/settings_window.py`

**位置**: 在删除 Avatar 同步折叠栏后的位置添加

**代码示例**:
```python
# TTS 折叠栏
self.tts_section = ctk.CTkFrame(self.scrollable_frame)
self.tts_section.pack(fill="x", padx=20, pady=(10, 0))

# TTS 标题行（可折叠）
self.tts_header = ctk.CTkFrame(self.tts_section, fg_color="transparent")
self.tts_header.pack(fill="x", padx=10, pady=5)

self.tts_toggle_btn = ctk.CTkButton(
    self.tts_header,
    text="▼ " + tr("tts_section"),
    command=self._toggle_tts_section,
    width=200,
    anchor="w",
)
self.tts_toggle_btn.pack(side="left")

# TTS 内容区域
self.tts_content = ctk.CTkFrame(self.tts_section, fg_color="transparent")
self.tts_content.pack(fill="x", padx=10, pady=5)
self.tts_content_visible = True

# TTS 启用开关
self.tts_enable_var = ctk.BooleanVar(value=False)
self.tts_enable_check = ctk.CTkCheckBox(
    self.tts_content,
    text=tr("tts_enable"),
    variable=self.tts_enable_var,
    command=self._on_tts_enable_changed,
)
self.tts_enable_check.pack(anchor="w", pady=5)

# TTS 引擎选择
engine_frame = ctk.CTkFrame(self.tts_content, fg_color="transparent")
engine_frame.pack(fill="x", pady=5)

ctk.CTkLabel(engine_frame, text=tr("tts_engine") + ":").pack(side="left", padx=(0, 10))
self.tts_engine_var = ctk.StringVar(value="edge")
self.tts_engine_menu = ctk.CTkOptionMenu(
    engine_frame,
    variable=self.tts_engine_var,
    values=["edge", "gtts", "pyttsx3"],
    command=self._on_tts_engine_changed,
)
self.tts_engine_menu.pack(side="left", fill="x", expand=True)

# TTS 音色选择
voice_frame = ctk.CTkFrame(self.tts_content, fg_color="transparent")
voice_frame.pack(fill="x", pady=5)

ctk.CTkLabel(voice_frame, text=tr("tts_voice") + ":").pack(side="left", padx=(0, 10))
self.tts_voice_var = ctk.StringVar(value="zh-CN-XiaoxiaoNeural")
self.tts_voice_menu = ctk.CTkOptionMenu(
    voice_frame,
    variable=self.tts_voice_var,
    values=["zh-CN-XiaoxiaoNeural"],
)
self.tts_voice_menu.pack(side="left", fill="x", expand=True)

# TTS 语速滑块
speed_frame = ctk.CTkFrame(self.tts_content, fg_color="transparent")
speed_frame.pack(fill="x", pady=5)

ctk.CTkLabel(speed_frame, text=tr("tts_speed") + ":").pack(side="left", padx=(0, 10))
self.tts_speed_var = ctk.DoubleVar(value=1.0)
self.tts_speed_slider = ctk.CTkSlider(
    speed_frame,
    from_=0.5,
    to=2.0,
    variable=self.tts_speed_var,
)
self.tts_speed_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
self.tts_speed_label = ctk.CTkLabel(speed_frame, text="1.0x", width=50)
self.tts_speed_label.pack(side="left")

# 绑定滑块更新标签
self.tts_speed_var.trace_add("write", self._update_tts_speed_label)

# TTS 音量滑块
volume_frame = ctk.CTkFrame(self.tts_content, fg_color="transparent")
volume_frame.pack(fill="x", pady=5)

ctk.CTkLabel(volume_frame, text=tr("tts_volume") + ":").pack(side="left", padx=(0, 10))
self.tts_volume_var = ctk.DoubleVar(value=0.8)
self.tts_volume_slider = ctk.CTkSlider(
    volume_frame,
    from_=0.0,
    to=1.0,
    variable=self.tts_volume_var,
)
self.tts_volume_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
self.tts_volume_label = ctk.CTkLabel(volume_frame, text="80%", width=50)
self.tts_volume_label.pack(side="left")

# 绑定滑块更新标签
self.tts_volume_var.trace_add("write", self._update_tts_volume_label)

# TTS 自动朗读开关
self.tts_auto_read_var = ctk.BooleanVar(value=False)
self.tts_auto_read_check = ctk.CTkCheckBox(
    self.tts_content,
    text=tr("tts_auto_read"),
    variable=self.tts_auto_read_var,
)
self.tts_auto_read_check.pack(anchor="w", pady=5)

# TTS 测试按钮
self.tts_test_btn = ctk.CTkButton(
    self.tts_content,
    text=tr("tts_test"),
    command=self._test_tts,
    width=100,
)
self.tts_test_btn.pack(anchor="w", pady=5)

# TTS 提示
tts_hint = ctk.CTkLabel(
    self.tts_content,
    text=tr("tts_hint"),
    wraplength=500,
    justify="left",
    text_color="gray",
)
tts_hint.pack(anchor="w", pady=5)
```

**需要添加的方法**:
```python
def _toggle_tts_section(self):
    """切换 TTS 折叠栏显示/隐藏"""
    if self.tts_content_visible:
        self.tts_content.pack_forget()
        self.tts_toggle_btn.configure(text="▶ " + tr("tts_section"))
        self.tts_content_visible = False
    else:
        self.tts_content.pack(fill="x", padx=10, pady=5)
        self.tts_toggle_btn.configure(text="▼ " + tr("tts_section"))
        self.tts_content_visible = True

def _on_tts_enable_changed(self):
    """TTS 启用状态改变"""
    enabled = self.tts_enable_var.get()
    # 启用/禁用所有 TTS 控件
    state = "normal" if enabled else "disabled"
    self.tts_engine_menu.configure(state=state)
    self.tts_voice_menu.configure(state=state)
    self.tts_speed_slider.configure(state=state)
    self.tts_volume_slider.configure(state=state)
    self.tts_auto_read_check.configure(state=state)
    self.tts_test_btn.configure(state=state)

def _on_tts_engine_changed(self, engine: str):
    """TTS 引擎改变，更新可用音色列表"""
    # 根据引擎加载可用音色
    # 这里需要调用 TTS 引擎获取音色列表
    pass

def _update_tts_speed_label(self, *args):
    """更新语速标签"""
    speed = self.tts_speed_var.get()
    self.tts_speed_label.configure(text=f"{speed:.1f}x")

def _update_tts_volume_label(self, *args):
    """更新音量标签"""
    volume = self.tts_volume_var.get()
    self.tts_volume_label.configure(text=f"{int(volume * 100)}%")

def _test_tts(self):
    """测试 TTS"""
    # 播放测试音频
    test_text = {
        "zh-CN": "你好，这是语音测试。",
        "en": "Hello, this is a voice test.",
        "ja": "こんにちは、これは音声テストです。",
    }.get(self.ui_language, "Hello, this is a voice test.")

    # 调用 TTS 管理器播放
    # self.tts_manager.speak(test_text, self.tts_voice_var.get(), ...)
    pass
```

**加载配置**:
```python
def _load_config_to_ui(self):
    # ... 现有代码 ...

    # 加载 TTS 配置
    tts_cfg = self.config.get("tts", {})
    self.tts_enable_var.set(tts_cfg.get("enabled", False))
    self.tts_engine_var.set(tts_cfg.get("engine", "edge"))
    self.tts_auto_read_var.set(tts_cfg.get("auto_read", False))

    engine = tts_cfg.get("engine", "edge")
    engine_cfg = tts_cfg.get(engine, {})
    self.tts_voice_var.set(engine_cfg.get("voice", "zh-CN-XiaoxiaoNeural"))
    self.tts_speed_var.set(engine_cfg.get("rate", 1.0))
    self.tts_volume_var.set(engine_cfg.get("volume", 0.8))

    self._on_tts_enable_changed()
```

**保存配置**:
```python
def _save_config(self):
    # ... 现有代码 ...

    # 保存 TTS 配置
    engine = self.tts_engine_var.get()
    self.config["tts"] = {
        "enabled": self.tts_enable_var.get(),
        "engine": engine,
        "auto_read": self.tts_auto_read_var.get(),
        engine: {
            "voice": self.tts_voice_var.get(),
            "rate": self.tts_speed_var.get(),
            "volume": self.tts_volume_var.get(),
        }
    }
```

### 3. 主窗口 - 添加语音阅读按钮

**文件**: `src/ui/main_window.py`

**位置**: 在红色框标记的位置（"反向翻译"和"悬浮窗"按钮之间）

**代码示例**:
```python
# 在主窗口初始化中添加 TTS 管理器
from src.tts.manager import TTSManager

class MainWindow:
    def __init__(self, config):
        # ... 现有代码 ...

        # 初始化 TTS 管理器
        tts_cfg = config.get("tts", {})
        self.tts_manager = None
        if tts_cfg.get("enabled", False):
            try:
                self.tts_manager = TTSManager(
                    engine_name=tts_cfg.get("engine", "edge"),
                    cache_enabled=True,
                )
                self.tts_manager.start()
            except Exception as exc:
                logger.error("Failed to initialize TTS: %s", exc)

        # TTS 按钮（在反向翻译和悬浮窗之间）
        self.tts_button = ctk.CTkButton(
            self.top_bar,
            text=tr("tts_read_button"),
            command=self._on_tts_button_click,
            width=50,
            height=32,
        )
        # 根据 TTS 是否启用来显示/隐藏按钮
        if self.tts_manager is not None:
            self.tts_button.pack(side="left", padx=5)
```

**TTS 按钮点击处理**:
```python
def _on_tts_button_click(self):
    """语音阅读按钮点击"""
    if self.tts_manager is None:
        return

    # 获取当前输出格式
    output_format = self.config.get("translation", {}).get("output_format", "translated_with_original")

    # 获取要朗读的文本
    if output_format == "original_only":
        # 仅原文模式：朗读原文
        text_to_read = self.source_text.get("1.0", "end-1c").strip()
        if not text_to_read:
            return
    else:
        # 其他模式：朗读译文
        text_to_read = self.last_translation_result
        if not text_to_read:
            return

    # 获取 TTS 配置
    tts_cfg = self.config.get("tts", {})
    engine = tts_cfg.get("engine", "edge")
    engine_cfg = tts_cfg.get(engine, {})
    voice = engine_cfg.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = engine_cfg.get("rate", 1.0)
    volume = engine_cfg.get("volume", 0.8)

    # 朗读
    self.tts_button.configure(text=tr("tts_reading"))
    self.tts_manager.speak(
        text_to_read,
        voice,
        rate,
        volume,
        callback=self._on_tts_complete,
    )

def _on_tts_complete(self, success: bool, message: str):
    """TTS 完成回调"""
    self.tts_button.configure(text=tr("tts_read_button"))
    if not success:
        logger.error("TTS failed: %s", message)
```

**自动朗读集成**:
```python
def _on_translation_complete(self, original_text: str, translated_text: str):
    """翻译完成回调"""
    # ... 现有代码 ...

    # 自动朗读
    tts_cfg = self.config.get("tts", {})
    if tts_cfg.get("enabled", False) and tts_cfg.get("auto_read", False):
        if self.tts_manager is not None:
            output_format = self.config.get("translation", {}).get("output_format", "translated_with_original")
            text_to_read = original_text if output_format == "original_only" else translated_text

            engine = tts_cfg.get("engine", "edge")
            engine_cfg = tts_cfg.get(engine, {})
            voice = engine_cfg.get("voice", "zh-CN-XiaoxiaoNeural")
            rate = engine_cfg.get("rate", 1.0)
            volume = engine_cfg.get("volume", 0.8)

            self.tts_manager.speak(text_to_read, voice, rate, volume)
```

**清理资源**:
```python
def destroy(self):
    """窗口关闭时清理资源"""
    # ... 现有代码 ...

    # 停止 TTS 管理器
    if self.tts_manager is not None:
        self.tts_manager.stop()
```

### 4. 配置管理器 - 添加 TTS 配置验证

**文件**: `src/utils/config_manager.py`

**添加函数**:
```python
def _ensure_tts_config(config: dict, loaded: dict | None = None) -> bool:
    """确保 TTS 配置存在且有效"""
    changed = False
    tts_cfg = config.get("tts", {})
    if not isinstance(tts_cfg, dict):
        tts_cfg = {}
        config["tts"] = tts_cfg
        changed = True

    # 默认值
    defaults = {
        "enabled": False,
        "engine": "edge",
        "auto_read": False,
    }

    for key, value in defaults.items():
        if key not in tts_cfg:
            tts_cfg[key] = value
            changed = True

    # 确保各引擎配置存在
    engine_defaults = {
        "edge": {
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": 1.0,
            "volume": 0.8,
        },
        "gtts": {
            "voice": "zh-CN",
            "rate": 1.0,
            "volume": 0.8,
        },
        "pyttsx3": {
            "voice": None,
            "rate": 150,
            "volume": 1.0,
        },
    }

    for engine, engine_cfg in engine_defaults.items():
        if engine not in tts_cfg:
            tts_cfg[engine] = engine_cfg
            changed = True
        elif not isinstance(tts_cfg[engine], dict):
            tts_cfg[engine] = engine_cfg
            changed = True

    return changed
```

**在 load_config 中调用**:
```python
def load_config() -> dict:
    # ... 现有代码 ...

    if _ensure_tts_config(merged, loaded):
        config_changed = True

    # ... 现有代码 ...
```

## 测试步骤

1. **安装依赖**:
   ```bash
   pip install edge-tts gTTS pyttsx3 pydub
   ```

2. **测试 TTS 引擎**:
   ```python
   from src.tts.gtts_engine import GoogleTTS
   tts = GoogleTTS()
   print(f"Available: {tts.is_available()}")
   ```

3. **测试 UI**:
   - 打开设置窗口，确认 Avatar 同步折叠栏已删除
   - 确认 TTS 折叠栏显示正常
   - 测试各个 TTS 控件
   - 点击测试按钮，确认能播放声音

4. **测试主窗口**:
   - 确认 🔊 按钮显示在正确位置
   - 输入文本并翻译
   - 点击 🔊 按钮，确认能朗读
   - 切换输出格式为"仅原文"，确认朗读原文
   - 切换回其他格式，确认朗读译文

## 注意事项

1. **音色列表动态加载**: `_on_tts_engine_changed` 方法需要根据选择的引擎动态加载可用音色
2. **错误处理**: 添加适当的错误处理和用户提示
3. **线程安全**: TTS 管理器已经是线程安全的，但 UI 更新需要在主线程
4. **资源清理**: 确保窗口关闭时停止 TTS 管理器

## 完成后的功能

✅ 三种 TTS 引擎（Edge TTS, Google TTS, pyttsx3）
✅ 设置窗口中的 TTS 配置
✅ 主窗口中的朗读按钮
✅ 根据输出格式自动切换朗读内容
✅ 自动朗读选项
✅ 语速和音量控制
✅ 音色选择

---

**创建日期**: 2026-05-02
**状态**: 核心模块完成，UI 集成指南已提供
