# Settings Window 修改说明

由于 settings_window.py 文件较大（2745行），这里提供详细的修改指南。

## 需要删除的代码

### 1. 删除 Avatar 相关的 UI 文本定义（约第543-893行）

搜索并删除以下键的所有语言版本：
- `avatar_section`
- `avatar_subtitle`
- `avatar_sync_enabled`
- `avatar_sync_hint`
- `avatar_param_translating`
- `avatar_param_speaking`
- `avatar_param_error`
- `avatar_param_target_language`

### 2. 删除 Avatar 配置加载代码（约第1381行）

```python
# 删除这行
avatar_cfg = osc_cfg.get("avatar_sync", {}) if isinstance(osc_cfg.get("avatar_sync", {}), dict) else {}
```

### 3. 删除 Avatar UI 构建代码（约第1910-1960行）

删除从 `self._build_collapsible_card` 开始到所有 Avatar 参数变量定义的代码块。

### 4. 删除 Avatar 配置保存代码（约第2733-2739行）

```python
# 删除这些行
avatar_cfg = osc_cfg.setdefault("avatar_sync", {})
avatar_cfg["enabled"] = self._avatar_sync_enabled_var.get() == "1"
avatar_params = avatar_cfg.setdefault("params", {})
avatar_params["translating"] = self._avatar_translating_var.get().strip()
avatar_params["speaking"] = self._avatar_speaking_var.get().strip()
avatar_params["error"] = self._avatar_error_var.get().strip()
avatar_params["target_language"] = self._avatar_target_language_var.get().strip()
```

## 需要添加的代码

### 1. 在 UI 文本定义中添加 TTS 相关文本

已经在 `src/utils/i18n.py` 中添加，settings_window.py 使用 `tr()` 函数即可。

### 2. 在 `__init__` 方法开始处添加 TTS 管理器导入

```python
from src.tts.manager import TTSManager
from src.tts.factory import create_tts_engine
```

### 3. 在配置加载部分添加 TTS 配置加载（替换 Avatar 配置的位置）

```python
# 加载 TTS 配置
tts_cfg = cfg.get("tts", {})
if not isinstance(tts_cfg, dict):
    tts_cfg = {}
```

### 4. 在 UI 构建部分添加 TTS 折叠卡片（替换 Avatar 卡片的位置）

```python
# TTS 折叠卡片
tts_card = self._build_collapsible_card(
    self._scrollable_frame,
    tr("tts_section"),
    "",
    collapsed=True,
)

# TTS 启用开关
self._tts_enabled_var = ctk.StringVar(
    value="1" if bool(tts_cfg.get("enabled", False)) else "0"
)
self._build_switch_entry(
    tts_card,
    tr("tts_enable"),
    self._tts_enabled_var,
)

# TTS 引擎选择
engine = tts_cfg.get("engine", "edge")
self._tts_engine_var = ctk.StringVar(value=engine)
self._build_option_menu(
    tts_card,
    tr("tts_engine"),
    self._tts_engine_var,
    ["edge", "gtts", "pyttsx3"],
    command=self._on_tts_engine_changed,
    **pad,
)

# TTS 音色选择
engine_cfg = tts_cfg.get(engine, {})
self._tts_voice_var = ctk.StringVar(
    value=str(engine_cfg.get("voice", "zh-CN-XiaoxiaoNeural"))
)
self._tts_voice_menu = self._build_option_menu(
    tts_card,
    tr("tts_voice"),
    self._tts_voice_var,
    ["zh-CN-XiaoxiaoNeural"],  # 初始值，会在 _on_tts_engine_changed 中更新
    **pad,
)

# TTS 语速
self._tts_rate_var = ctk.DoubleVar(value=float(engine_cfg.get("rate", 1.0)))
rate_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
rate_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=4)
ctk.CTkLabel(
    rate_frame,
    text=tr("tts_speed") + ":",
    text_color=TEXT_PRI,
).pack(side="left", padx=(0, 10))
self._tts_rate_slider = ctk.CTkSlider(
    rate_frame,
    from_=0.5,
    to=2.0,
    variable=self._tts_rate_var,
    command=self._update_tts_rate_label,
)
self._tts_rate_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
self._tts_rate_label = ctk.CTkLabel(rate_frame, text="1.0x", width=50)
self._tts_rate_label.pack(side="left")

# TTS 音量
self._tts_volume_var = ctk.DoubleVar(value=float(engine_cfg.get("volume", 0.8)))
volume_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
volume_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=4)
ctk.CTkLabel(
    volume_frame,
    text=tr("tts_volume") + ":",
    text_color=TEXT_PRI,
).pack(side="left", padx=(0, 10))
self._tts_volume_slider = ctk.CTkSlider(
    volume_frame,
    from_=0.0,
    to=1.0,
    variable=self._tts_volume_var,
    command=self._update_tts_volume_label,
)
self._tts_volume_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
self._tts_volume_label = ctk.CTkLabel(volume_frame, text="80%", width=50)
self._tts_volume_label.pack(side="left")

# TTS 自动朗读
self._tts_auto_read_var = ctk.StringVar(
    value="1" if bool(tts_cfg.get("auto_read", False)) else "0"
)
self._build_switch_entry(
    tts_card,
    tr("tts_auto_read"),
    self._tts_auto_read_var,
)

# TTS 测试按钮
test_btn = ctk.CTkButton(
    tts_card,
    text=tr("tts_test"),
    command=self._test_tts,
    width=100,
    height=32,
)
test_btn.pack(anchor="w", padx=SETTINGS_FIELD_PADX, pady=8)

# TTS 提示
self._build_hint_box(tts_card, tr("tts_hint"))

# 初始化时加载音色列表
self._on_tts_engine_changed(engine)
```

### 5. 添加 TTS 相关的方法

```python
def _on_tts_engine_changed(self, engine: str):
    """TTS 引擎改变时更新音色列表"""
    try:
        from src.tts.factory import create_tts_engine
        tts_engine = create_tts_engine(engine)
        if tts_engine and tts_engine.is_available():
            voices = tts_engine.get_available_voices()
            voice_ids = [v.id for v in voices[:50]]  # 限制数量避免UI卡顿
            if voice_ids:
                self._tts_voice_menu.configure(values=voice_ids)
                # 如果当前音色不在列表中，设置为第一个
                if self._tts_voice_var.get() not in voice_ids:
                    self._tts_voice_var.set(voice_ids[0])
    except Exception as exc:
        logger.error("Failed to load TTS voices: %s", exc)

def _update_tts_rate_label(self, value):
    """更新语速标签"""
    self._tts_rate_label.configure(text=f"{float(value):.1f}x")

def _update_tts_volume_label(self, value):
    """更新音量标签"""
    self._tts_volume_label.configure(text=f"{int(float(value) * 100)}%")

def _test_tts(self):
    """测试 TTS"""
    try:
        from src.tts.manager import TTSManager

        test_texts = {
            "zh-CN": "你好，这是语音测试。",
            "en": "Hello, this is a voice test.",
            "ja": "こんにちは、これは音声テストです。",
        }
        test_text = test_texts.get(self._ui_language, "Hello, this is a voice test.")

        engine = self._tts_engine_var.get()
        voice = self._tts_voice_var.get()
        rate = self._tts_rate_var.get()
        volume = self._tts_volume_var.get()

        # 创建临时 TTS 管理器
        manager = TTSManager(engine_name=engine, cache_enabled=False)
        manager.start()
        manager.speak(test_text, voice, rate, volume)

        # 等待播放完成后清理
        def cleanup():
            time.sleep(3)
            manager.stop()
        threading.Thread(target=cleanup, daemon=True).start()

    except Exception as exc:
        logger.error("TTS test failed: %s", exc)
        messagebox.showerror(tr("error_title"), f"TTS test failed: {exc}")
```

### 6. 在配置保存部分添加 TTS 配置保存（替换 Avatar 保存的位置）

```python
# 保存 TTS 配置
engine = self._tts_engine_var.get()
tts_cfg = cfg.setdefault("tts", {})
tts_cfg["enabled"] = self._tts_enabled_var.get() == "1"
tts_cfg["engine"] = engine
tts_cfg["auto_read"] = self._tts_auto_read_var.get() == "1"

engine_cfg = tts_cfg.setdefault(engine, {})
engine_cfg["voice"] = self._tts_voice_var.get()
engine_cfg["rate"] = self._tts_rate_var.get()
engine_cfg["volume"] = self._tts_volume_var.get()
```

## 实施步骤

1. 备份 `src/ui/settings_window.py`
2. 删除所有 Avatar 相关代码
3. 添加 TTS 相关代码
4. 测试设置窗口是否正常显示
5. 测试 TTS 功能是否正常工作

## 注意事项

- 确保导入 `logger` 用于日志记录
- `_build_option_menu` 方法返回的是菜单对象，需要保存以便后续更新
- TTS 测试功能使用临时管理器，避免影响主程序
- 音色列表限制为50个，避免UI卡顿

---

由于文件较大且修改较多，建议使用文本编辑器手动修改，或者我可以为你生成完整的修改后的文件。
