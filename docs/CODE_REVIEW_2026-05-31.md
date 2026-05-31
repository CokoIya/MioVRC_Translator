# 代码审查报告

**项目**: VRC Translator
**分支**: main (working tree uncommitted changes)
**日期**: 2026-05-31
**审查范围**: ~16K 行变更，64 个文件
**审查投入**: 7 个分析角度 × 6 候选 + 2 验证代理 × 10 候选

---

## 摘要

共发现 **10 个问题**，按严重程度排序：

| # | 文件 | 行 | 类别 | 验证结果 |
|---|------|-----|------|----------|
| 1 | style_bert_vits2_engine.py | 699 | 正确性 | CONFIRMED |
| 2 | tts_service.py | 69 | 正确性 | PLAUSIBLE |
| 3 | manager.py | 830 | 正确性 | CONFIRMED |
| 4 | model_manager.py | 43 | 正确性 | CONFIRMED |
| 5 | manager.py | 256 | 正确性 | PLAUSIBLE |
| 6 | manager.py | 57 | 重复代码 | 确认 |
| 7 | config_manager.py | 1182 | 重复代码 | 确认 |
| 8 | api_tts_engines.py | 229 | 重复代码 | 确认 |
| 9 | manager.py | 1040 | 死代码 | CONFIRMED |
| 10 | api_tts_config.py | 273 | 模式风险 | PLAUSIBLE |

---

## 1. 每个音色独立的语言区域检测被移除

**文件**: `src/tts/style_bert_vits2_engine.py:699`
**验证**: CONFIRMED
**严重程度**: 高

**问题描述**:
旧的 `get_available_voices()` 对每个音色调用 `style_bert_preset_language(model.name, speaker)` 来解析其语言区域。新代码直接用全局 `self._bert_language` 作为所有音色的语言区域，`_voice_bert_language()` 方法（line 804-805）也恒返回全局设置。

**失败场景**:
用户配置了一个已知音色（如 Hololive 模型）其原生语言与全局 `_bert_language` 不同。TTS 输出使用错误语言区域，导致发音/口音不正确。

**修复建议**:
恢复每个音色独立的语言区域检测逻辑。如果 `style_bert_preset_language()` 仍在其他地方被使用，应保留；若已移除，考虑在 `_STYLE_BERT_VOICE_LOCALES` 中按音色名称索引而不是全局设置。

---

## 2. TtsService.speak() 使用原始引擎名称查配置，未经过 normalize

**文件**: `src/core/tts_service.py:69`
**验证**: PLAUSIBLE
**严重程度**: 高

**问题描述**:
`config_manager._ensure_tts_config` 会 normalize 引擎名称（如 'Edge' → 'edge'），但 `TtsService.speak()` 在 line 69 使用 `tts_cfg.get(tts_cfg.get("engine", "edge"))` 直接查配置。工厂侧（`factory.py:42`）内部会再做一次 normalize，但 engine_cfg 查找（第 47 行）已经用了未 normalize 的名称。

**失败场景**:
配置文件存的是 'Edge' 或含空格的 ' edge '，而 `tts_cfg` 中的 key 已经过 normalize 为 'edge'。`tts_cfg.get('Edge')` 查不到配置，返回空 dict，最终用默认值替代用户配置的引擎/音色/语速。

**修复建议**:
在 `TtsService.speak()` 中对 `tts_cfg.get("engine")` 调用与 config_manager 相同的 normalize 逻辑后再查找配置。或者确保 config_manager 的 normalize 对所有 TTS 配置路径都生效。

---

## 3. 音频播放热路径中无条件阻塞 50ms sleep

**文件**: `src/tts/manager.py:830`
**验证**: CONFIRMED
**严重程度**: 中

**问题描述**:
`speak()` 方法在 line 830 执行 `time.sleep(0.05)`，注释说明是为了等待音频设备完全释放以避免重叠/重复播放。这是一个固定阻塞，发生在每次 speak 的热路径上。

**失败场景**:
高频连续翻译场景（如快速连续对话翻译），每段 TTS 输出后固定等 50ms，累积延迟明显。例如 10 段连续翻译等 500ms，在实时口译场景中难以接受。

**修复建议**:
用 Event 等待前一 stream 的 `finished_callback`（已在 line 287 接入 `finished_callback=mark_finished`），设置超时 50ms 替代无条件等待。这样快速场景下不等，慢速场景下最多等 50ms。

---

## 4. 删除了 MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR 环境变量覆盖

**文件**: `src/asr/model_manager.py:43`
**验证**: CONFIRMED
**严重程度**: 低

**问题描述**:
旧代码允许 `MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR` 环境变量重定向模型缓存目录。diff 删除了这个逻辑。

**失败场景**:
开发者和高级用户通过该环境变量将 SenseVoice 模型缓存定向到快速 SSD（如 `D:\model-cache`），现在被静默忽略。缓存始终写到 `writable_app_dir()` 路径，可能落在系统盘或慢速 HDD 上导致下载缓慢。

**修复建议**:
保留环境变量覆盖逻辑，或者如果确定不再需要，至少在 release notes 中说明。

---

## 5. stop() 守卫条件存在边缘情况可能导致 NPE

**文件**: `src/tts/manager.py:256`
**验证**: PLAUSIBLE
**严重程度**: 中

**问题描述**:
守卫条件为 `if not self._running and self._worker_thread is None`。若 `start()` 在设置 `_running=True` 后、创建 `_worker_thread` 前失败（引擎初始化失败），此时 `stop()` 命中 else 分支调用 `_signal_worker_stop()`，该方法对 None 线程操作可能 NPE。

**失败场景**:
ASR/TTS 引擎初始化中途失败（如 GPU 无内存），`start()` 部分完成（`_running=True`，`_worker_thread=None`）。此时调用 `stop()` 触发 NPE。

**修复建议**:
将条件改为 `if self._worker_thread is None`，简化逻辑并避免上述边缘情况：

```python
def stop(self) -> None:
    if self._worker_thread is None:
        self.stop_playback()
        return
    # ... 正常停止逻辑
```

---

## 6. _style_bert_cuda_available() 在两处重复定义

**文件**: `src/tts/manager.py:57` 和 `src/tts/style_bert_vits2_engine.py:280`
**验证**: 确认（两处完全相同）
**严重程度**: 低

**问题描述**:
两处实现完全相同的 `torch.cuda.is_available()` 检查。

**修复建议**:
在 `style_bert_vits2_engine.py` 中定义一个公共函数（或使用已有函数），`manager.py` 直接 import 调用。消除重复，避免维护风险。

---

## 7. _normalize_style_bert_bert_language() 与引擎模块中的函数完全重复

**文件**: `src/utils/config_manager.py:1182-1211` 和 `src/tts/style_bert_vits2_engine.py:576-611`
**验证**: 确认（两处完全相同）
**严重程度**: 中

**问题描述**:
30+ 语言别名的规范化表在两个文件中各有一份副本。当前 `config_manager` 的副本仅被 `normalize_output_format_2` 消费（line 1305），而引擎模块的副本在整个文件中被广泛使用。

**修复建议**:
删除 `config_manager.py` 中的私有副本，直接 import `style_bert_vits2_engine.normalize_style_bert_bert_language`。消除约 30 行重复代码。

---

## 8. _float_value() / _int_value() 与工厂模块中的函数重复

**文件**: `src/tts/api_tts_engines.py:229-242` 和 `src/translators/factory.py:556-567`
**验证**: 确认（逻辑相同）
**严重程度**: 低

**问题描述**:
工厂版本的 `_float_setting()` 和 `_int_setting()` 有显式 min/max 范围支持，更健壮。TTS 引擎侧定义了简化版 `_float_value()` 和 `_int_value()`，逻辑完全相同但功能较弱。

**修复建议**:
`api_tts_engines.py` 中删除本地实现，直接 import 并调用 `translators.factory` 中的公共函数。

---

## 9. _play_audio_with_fallback 是未被调用的死代码

**文件**: `src/tts/manager.py:1040-1104`
**验证**: CONFIRMED（grep 全代码库仅文档文件有引用）
**严重程度**: 低

**问题描述**:
该方法定义后从未被任何代码路径调用。约 65 行代码，由一个完整的独立 fallback player 组成。当前所有 `PortAudioError` 处理都内联在 `try/except` 中（lines 870-898），不再依赖此方法。

**修复建议**:
删除该方法。遗留文档引用（`docs/history/FIX_SUMMARY.md`）也应对应清理。

---

## 10. resolve_tts_api_config 对浅拷贝 dict 做 update，后续调用可能污染

**文件**: `src/tts/api_tts_config.py:273`
**验证**: PLAUSIBLE
**严重程度**: 低

**问题描述**:
`get_tts_api_default_config()` 返回 `dict(defaults)`（浅拷贝），`resolve_tts_api_config()` 在此浅拷贝上执行 `defaults.update(config)`。由于 `TTS_API_DEFAULT_CONFIGS` 只含原始类型（字符串、浮点数、整数、布尔），当前无嵌套结构共享风险。但模式本身脆弱——若未来添加嵌套 dict/list，浅拷贝特性会导致跨调用污染。

**失败场景**:
若未来有人在 `TTS_API_DEFAULT_CONFIGS` 中添加嵌套值（如默认 headers list），浅拷贝会导致 update 污染共享引用。

**修复建议**:
使用 `copy.deepcopy(defaults)` 替代 `dict(defaults)`，确保完全隔离。或重构为每次返回新 dict 而非依赖拷贝保护。

---

## 审查方法论

| 角度 | 覆盖范围 | 候选数 |
|------|---------|--------|
| A: 行对行 diff 扫描 | 所有变更 hunks + 相邻上下文 | 4 |
| B: 删除行为审计 | 所有删除/替换行 | 6 |
| C: 跨文件追踪 | 变更函数的调用方/被调用方 | 6 |
| D: 代码复用 | 重复模式检测 | 3 |
| E: 简化审查 | 不必要复杂度 | 6 |
| F: 效率审查 | 浪费/阻塞 | 6 |
| G: 架构深度 | 抽象层次 | 0 |

验证阶段：对 10 个候选运行逐个源码核查，确认/可信/驳回。驳回 3 项（目标目录覆盖是有意行为；ASR 默认函数是别名非错误调用；缓存 bypass 长度匹配无错）。