# 风险和 Bug 评估报告

## 高风险问题 🔴

### 1. 设备回退可能导致音频输出到错误设备
**文件：** `src/tts/manager.py:315-370`

**问题描述：**
```python
# 当所有 MIXLINE 替代设备都失败时，使用系统默认设备
if playback is None:
    logger.warning("No alternative device found, using default output device")
    playback = sd.OutputStream(device=None, ...)  # ⚠️ 可能是扬声器
```

**风险：**
- 如果 WASAPI/DirectSound MIXLINE 设备也失败，会回退到系统默认设备
- 用户可能听到 TTS 从扬声器播放，而不是发送到 VRChat
- **没有通知用户设备已切换**
- 用户可能不知道为什么 VRChat 中听不到声音

**影响范围：** 所有 TTS 引擎

**建议修复：**
```python
# 1. 在 UI 显示警告通知
if final_device != original_device:
    self._show_device_change_notification(original_device, final_device)

# 2. 记录到日志并更新配置
logger.warning("TTS output device changed: %s -> %s",
               original_device, final_device)
self._output_device = final_device
self._save_device_config()

# 3. 如果回退到默认设备，显示错误而非静默失败
if final_device is None:
    raise RuntimeError("MIXLINE device not available, please check audio settings")
```

**优先级：** P0 - 立即修复

---

### 2. 设备回退时可能出现无限循环
**文件：** `src/tts/manager.py:338-355`

**问题描述：**
```python
for alternative_device in alternative_devices:
    try:
        playback = sd.OutputStream(device=alternative_device, ...)
        break
    except sd.PortAudioError as alt_exc:
        logger.warning("Alternative MIXLINE device %s failed", alternative_device)
        # ⚠️ 如果 alternative_device 也触发回退逻辑，可能递归
```

**风险：**
- `_create_output_stream` 在创建流失败时会调用 `_find_alternative_devices`
- 如果替代设备也失败，可能再次触发回退逻辑
- 虽然当前实现跳过了 `failed_device`，但如果设备列表变化，可能出现问题

**建议修复：**
```python
# 添加递归深度限制
def _create_output_stream(self, ..., _retry_depth=0):
    if _retry_depth > 2:
        raise RuntimeError("Device fallback exceeded maximum retries")

    try:
        playback = sd.OutputStream(device=playback_device, ...)
    except sd.PortAudioError as exc:
        if _retry_depth < 2:
            # 尝试回退
            playback = self._create_output_stream(..., _retry_depth=_retry_depth+1)
```

**优先级：** P0 - 立即修复

---

### 3. 多线程环境下设备列表可能变化
**文件：** `src/tts/manager.py:382-410`

**问题描述：**
```python
def _find_alternative_devices(self, failed_device: int) -> list[int]:
    devices = sd.query_devices()  # ⚠️ 设备列表可能在运行时变化
    for i, dev in enumerate(devices):
        if i == failed_device:  # ⚠️ 设备 ID 可能已失效
            continue
```

**风险：**
- 用户插拔音频设备时，设备 ID 可能变化
- 缓存的设备 ID 可能指向错误的设备
- `sd.query_devices()` 可能抛出异常（设备正在被移除）
- 设备 ID 可能超出范围

**建议修复：**
```python
def _find_alternative_devices(self, failed_device: int) -> list[int]:
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.error("Failed to query devices: %s", exc)
        return []

    # 验证设备 ID 有效性
    if failed_device >= len(devices) or failed_device < 0:
        logger.warning("Device ID %s out of range (0-%s)",
                      failed_device, len(devices)-1)
        return []

    try:
        failed_info = devices[failed_device]
    except (IndexError, KeyError) as exc:
        logger.error("Failed to get device info: %s", exc)
        return []
```

**优先级：** P0 - 立即修复

---

## 中风险问题 🟡

### 4. FP16/FP32 转换 Monkey-patch 影响全局
**文件：** `src/tts/style_bert_vits2_engine.py:186-193`

**问题描述：**
```python
# 全局 monkey-patch safetensors 加载器
from style_bert_vits2.models.utils import safetensors as sbv2_safetensors
_original_load_safetensors = sbv2_safetensors.load_safetensors
sbv2_safetensors.load_safetensors = _patched_load_safetensors  # ⚠️ 全局修改
```

**风险：**
- 影响所有使用 `style_bert_vits2` 的代码
- 如果其他代码期望 FP16，会得到 FP32（可能导致精度或性能问题）
- 无法撤销 patch（除非重启进程）
- 如果 `style_bert_vits2` 库更新，patch 可能失效

**建议修复：**
```python
# 1. 只在 CPU 模式下 patch
if self._device == "cpu":
    self._apply_fp32_patch()
else:
    logger.info("GPU mode, skipping FP32 patch")

# 2. 提供 unpatch 方法
def __del__(self):
    self._restore_original_loader()

def _restore_original_loader(self):
    global _original_load_safetensors
    if _original_load_safetensors is not None:
        sbv2_safetensors.load_safetensors = _original_load_safetensors
        _original_load_safetensors = None
```

**优先级：** P1 - 近期修复

---

### 5. BERT 模型下载可能阻塞 UI
**文件：** `src/tts/style_bert_vits2_engine.py:260-270`

**问题描述：**
```python
def _ensure_japanese_bert_runtime(self):
    bert_model = get_bert_model(Languages.JP)  # ⚠️ 可能触发 1.5GB 下载
    if bert_model is not None:
        bert_model.float()
```

**风险：**
- 首次使用时会下载 1.5GB BERT 模型
- 下载过程可能阻塞 TTS 线程（2-10 分钟，取决于网速）
- 没有进度提示，用户可能认为程序卡死
- 网络中断时下载失败，没有重试机制

**建议修复：**
```python
# 1. 在设置界面添加预下载按钮
def download_bert_model_async(self, progress_callback):
    def download_thread():
        try:
            download_model(STYLE_BERT_JP_BERT_MODEL_ID,
                          progress_callback=progress_callback)
            progress_callback(100, "Download complete")
        except Exception as exc:
            progress_callback(-1, f"Download failed: {exc}")

    threading.Thread(target=download_thread, daemon=True).start()

# 2. 首次合成时显示提示
if not model_is_complete(STYLE_BERT_JP_BERT_MODEL_ID):
    logger.warning("BERT model not found, downloading (1.5GB)...")
    show_notification("正在下载 BERT 模型，请稍候...")
```

**优先级：** P1 - 近期修复

---

### 6. 音色包扫描没有错误处理
**文件：** `src/tts/style_bert_vits2_models.py`

**问题描述：**
```python
def list_imported_style_bert_models():
    for model_dir in model_root.iterdir():
        if not model_dir.is_dir():
            continue
        # ⚠️ 如果 config.json 损坏，会抛出异常
        config = json.loads(config_path.read_text(encoding="utf-8"))
```

**风险：**
- 损坏的音色包会导致整个扫描失败
- 用户无法使用任何音色包（包括正常的）
- 没有跳过无效音色包的机制
- JSON 解析错误、文件权限错误等都会导致崩溃

**建议修复：**
```python
def list_imported_style_bert_models():
    models = []
    for model_dir in model_root.iterdir():
        try:
            if not model_dir.is_dir():
                continue

            config_path = model_dir / "config.json"
            if not config_path.exists():
                logger.debug("Skipping %s: no config.json", model_dir.name)
                continue

            config = json.loads(config_path.read_text(encoding="utf-8"))
            # 验证必需字段
            if "model_name" not in config:
                logger.warning("Skipping %s: invalid config", model_dir.name)
                continue

            models.append(...)

        except Exception as exc:
            logger.warning("Skipping invalid model %s: %s",
                          model_dir.name, exc)
            continue

    return models
```

**优先级：** P1 - 近期修复

---

### 7. 配置文件中的设备 ID 可能过期
**文件：** `config.json`

**问题描述：**
```json
{
  "tts": {
    "output_device": 62,  // ⚠️ 设备 ID 在重启后可能变化
    "output_device_name": "Speakers (MIXLINE Wave Speaker)"
  }
}
```

**风险：**
- Windows 重启后设备 ID 可能变化
- 用户插拔设备后 ID 失效
- 程序会尝试使用错误的设备
- 虽然有设备回退，但每次启动都触发回退会增加延迟

**当前缓解措施：**
- ✅ 已有 `output_device_name` 字段用于设备名称匹配
- ✅ 设备回退机制可以处理 ID 失效

**建议改进：**
```python
def _resolve_playback_device(self):
    # 优先使用设备名称而非 ID
    if self._output_device_name:
        device_id = self._find_device_by_name(self._output_device_name)
        if device_id is not None:
            # 如果 ID 变化，更新配置
            if device_id != self._output_device:
                logger.info("Device ID changed: %s -> %s",
                           self._output_device, device_id)
                self._output_device = device_id
                self._save_config()
            return device_id

    # 回退到 ID
    return self._output_device
```

**优先级：** P1 - 近期修复

---

## 低风险问题 🟢

### 8. 设备回退日志可能泄露敏感信息
**文件：** `src/tts/manager.py:332-336`

**问题描述：**
```python
logger.warning(
    "Device %s failed (error %s: %s)",
    playback_device,
    error_code,
    exc.args[0] if exc.args else exc  # ⚠️ 可能包含系统路径
)
```

**风险：**
- 错误消息可能包含用户名或系统路径
- 日志文件可能被分享时泄露隐私
- 例如：`C:\Users\username\AppData\...`

**建议修复：**
```python
import os

# 清理敏感信息
error_msg = str(exc.args[0] if exc.args else exc)
error_msg = error_msg.replace(os.path.expanduser("~"), "~")
error_msg = error_msg.replace(os.environ.get("USERNAME", ""), "<user>")
logger.warning("Device %s failed: %s", playback_device, error_msg)
```

**优先级：** P2 - 长期改进

---

### 9. CPU 推理性能警告不足
**文件：** `src/ui/settings_window.py`

**问题描述：**
- Style-Bert-VITS2 CPU 推理需要 2-3 秒/句
- UI 中没有明确的性能警告
- 用户可能认为程序卡死或出错

**建议修复：**
```python
# 在设备选择下拉菜单旁添加警告标签
if selected_device == "cpu":
    warning_label.set_text("⚠️ CPU 推理较慢（2-3秒/句），建议使用 GPU")
    warning_label.set_visible(True)
else:
    warning_label.set_visible(False)
```

**优先级：** P2 - 长期改进

---

## 已知 Bug 🐛

### Bug #1: 设备回退后配置文件未更新
**严重程度：** 中

**复现步骤：**
1. 配置文件设置 `output_device: 62` (WDM-KS)
2. 启动程序，自动回退到设备 30 (WASAPI)
3. 重启程序
4. 再次尝试使用设备 62，再次回退

**预期行为：** 配置文件应更新为设备 30

**实际行为：** 配置文件保持设备 62，每次启动都触发回退

**影响：** 每次启动都有额外的设备探测延迟（约 100-200ms）

**修复方案：**
```python
# 在 _create_output_stream 成功回退后
if alternative_device is not None and alternative_device != playback_device:
    self._output_device = alternative_device
    self._save_config()
```

---

### Bug #2: 多个 MIXLINE 设备时选择不确定
**严重程度：** 低

**场景：** 用户安装了多个虚拟音频设备（MIXLINE、VB-Cable 等）

**问题：** `_find_alternative_devices` 只检查名称中是否包含 "mixline"

**影响：** 可能选择错误的虚拟设备

**修复方案：**
```python
# 更精确的设备名称匹配
failed_name_parts = set(failed_name.lower().split())
dev_name_parts = set(dev_name.lower().split())

# 计算相似度
similarity = len(failed_name_parts & dev_name_parts) / len(failed_name_parts)
if similarity > 0.5:  # 至少 50% 匹配
    alternatives.append((i, similarity))

# 按相似度排序
alternatives.sort(key=lambda x: x[1], reverse=True)
return [dev_id for dev_id, _ in alternatives]
```

---

### Bug #3: 设备回退时采样率探测重复
**严重程度：** 低

**问题：**
- `_prepare_audio_for_device` 会探测设备采样率
- 设备回退时会对每个替代设备重复探测
- 每个设备探测 8 个采样率 = 8 × 3 = 24 次探测

**影响：** 设备切换延迟增加（约 100-200ms）

**修复方案：**
```python
# 缓存设备采样率探测结果
_device_sample_rates_cache: dict[int, list[int]] = {}

def _probe_supported_sample_rates(self, device: OutputDeviceRef) -> list[int]:
    if device in _device_sample_rates_cache:
        return _device_sample_rates_cache[device]

    # 探测逻辑...
    _device_sample_rates_cache[device] = supported
    return supported
```

---

### Bug #4: BERT 模型转换为 FP32 后未验证
**严重程度：** 低

**问题：**
```python
bert_model = get_bert_model(Languages.JP)
if bert_model is not None:
    bert_model.float()  # ⚠️ 没有验证转换是否成功
```

**影响：** 如果转换失败，仍会使用 FP16 模型，导致后续错误

**修复方案：**
```python
bert_model.float()
# 验证转换
param_dtype = next(bert_model.parameters()).dtype
if param_dtype != torch.float32:
    raise RuntimeError(f"Failed to convert BERT model to FP32 (got {param_dtype})")
logger.info("Converted BERT model to FP32 for CPU inference")
```

---

## 潜在 Bug（未验证）⚠️

### 潜在 Bug #1: 设备回退时音频可能截断
**场景：** 正在播放音频时设备失败

**问题：** `stop_playback()` 会立即停止所有流

**影响：** 用户听到的音频可能不完整

**需要验证：** 设备失败时是否会触发 `stop_playback()`

---

### 潜在 Bug #2: 并发调用 speak() 时队列可能溢出
**场景：** 快速连续调用 `speak()`

**问题：** 队列大小限制为 10

**影响：** 后续请求被丢弃，没有通知用户

**需要验证：** 队列满时的行为

---

### 潜在 Bug #3: 音色包热加载可能失败
**场景：** 程序运行时添加新音色包

**问题：** 音色包列表不会自动刷新

**影响：** 需要重启程序才能看到新音色包

**需要验证：** 是否需要热加载功能

---

## 测试覆盖缺口 📋

### 未测试的场景

1. **设备热插拔**
   - ❌ 播放过程中拔出 MIXLINE 设备
   - ❌ 播放过程中插入新设备
   - ❌ 设备 ID 变化时的行为

2. **网络异常**
   - ❌ BERT 模型下载中断
   - ❌ Edge TTS API 超时
   - ❌ 网络完全断开

3. **资源耗尽**
   - ❌ 内存不足时加载 BERT 模型
   - ❌ 磁盘空间不足时下载模型
   - ❌ 音频缓冲区溢出

4. **并发压力**
   - ❌ 多个线程同时调用 speak()
   - ❌ 快速切换 TTS 引擎
   - ❌ 同时播放多个音频流

5. **边界条件**
   - ❌ 空文本输入
   - ❌ 超长文本（>1000 字符）
   - ❌ 特殊字符（emoji、控制字符）
   - ❌ 无效的设备 ID（负数、超出范围）

### 建议的测试用例

```python
# 1. 设备回退测试
def test_device_fallback_wdmks_to_wasapi():
    """测试 WDM-KS 设备自动回退到 WASAPI"""
    manager = TTSManager(output_device=62)  # WDM-KS
    manager.speak("test")
    # 验证实际使用的是 WASAPI 设备 30

# 2. 设备失效测试
def test_device_becomes_invalid_during_playback():
    """测试播放过程中设备失效"""
    manager = TTSManager(output_device=30)
    manager.speak("long text...")
    # 模拟设备断开
    # 验证错误处理和资源清理

# 3. 并发测试
def test_concurrent_speak_calls():
    """测试并发调用 speak()"""
    manager = TTSManager()
    threads = [Thread(target=manager.speak, args=("test",))
               for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 验证队列处理和资源清理

# 4. 资源清理测试
def test_resource_cleanup_on_error():
    """测试错误时的资源清理"""
    manager = TTSManager(output_device=999)  # 无效设备
    with pytest.raises(RuntimeError):
        manager.speak("test")
    # 验证没有资源泄漏

# 5. 边界条件测试
def test_empty_text():
    """测试空文本输入"""
    manager = TTSManager()
    result = manager.speak("")
    assert result == False  # 应该拒绝空文本

def test_very_long_text():
    """测试超长文本"""
    manager = TTSManager()
    long_text = "test " * 1000
    result = manager.speak(long_text)
    # 验证处理或截断

def test_invalid_device_id():
    """测试无效设备 ID"""
    with pytest.raises(ValueError):
        manager = TTSManager(output_device=-1)
    with pytest.raises(ValueError):
        manager = TTSManager(output_device=9999)
```

---

## 安全审计补充 🔒

### 1. 输入验证
- ✅ TTS 文本已验证（`validate_tts_text`）
- ⚠️ **设备 ID 未验证范围**（可能导致数组越界）
- ⚠️ **音色包路径未验证**（可能目录遍历攻击）
- ⚠️ **配置文件值未验证**（可能注入恶意值）

**建议：**
```python
# 验证设备 ID
def _validate_device_id(device_id: int) -> bool:
    if device_id < 0:
        raise ValueError(f"Invalid device ID: {device_id}")
    devices = sd.query_devices()
    if device_id >= len(devices):
        raise ValueError(f"Device ID {device_id} out of range (0-{len(devices)-1})")
    return True

# 验证音色包路径
def _validate_model_path(model_path: Path) -> bool:
    model_root = Path("tts_models/style_bert_vits2")
    try:
        model_path.resolve().relative_to(model_root.resolve())
    except ValueError:
        raise ValueError(f"Model path outside allowed directory: {model_path}")
    return True
```

### 2. 权限检查
- ⚠️ **音色包目录可写**（用户可能注入恶意文件）
- ⚠️ **配置文件可写**（可能被篡改）
- ⚠️ **日志文件可写**（可能被填满磁盘）

**建议：**
- 验证音色包文件签名
- 配置文件使用只读模式打开
- 限制日志文件大小

### 3. 错误信息泄露
- ⚠️ **日志可能包含系统路径**（泄露用户名）
- ⚠️ **异常堆栈可能泄露内部结构**
- ⚠️ **设备名称可能包含敏感信息**

**建议：**
- 清理日志中的敏感路径
- 生产环境禁用详细堆栈
- 过滤设备名称中的敏感信息

---

## 性能瓶颈 ⚡

### 1. 设备探测
**问题：** 每次播放都探测采样率（8 次/设备）

**影响：** 增加 50-100ms 延迟

**建议：** 缓存探测结果，定期刷新

### 2. BERT 模型加载
**问题：** 首次加载需要 2-3 秒

**影响：** 首次 TTS 延迟高

**建议：** 预加载或延迟加载

### 3. FP16→FP32 转换
**问题：** 每次加载模型都转换

**影响：** 增加 500ms-1s 加载时间

**建议：** 缓存转换后的模型

### 4. 音色包扫描
**问题：** 每次启动都扫描所有音色包

**影响：** 启动延迟（50 个音色包约 500ms）

**建议：** 缓存音色包列表，监听文件变化

---

## 建议的优先级 📊

### P0 - 立即修复（本周）
1. ✅ 设备回退逻辑（已修复）
2. ⚠️ **设备回退后更新配置文件**
3. ⚠️ **添加设备切换通知**
4. ⚠️ **防止设备回退无限循环**
5. ⚠️ **设备列表查询异常处理**

### P1 - 近期修复（本月）
1. 音色包扫描错误处理
2. BERT 模型下载进度提示
3. 设备探测结果缓存
4. FP32 转换验证
5. 配置文件设备 ID 自动更新

### P2 - 长期改进（下季度）
1. 设备热插拔支持
2. 音色包热加载
3. 性能优化（缓存、预加载）
4. 安全加固（输入验证、权限检查）
5. 完善测试覆盖

---

## 总结

### 关键风险
1. 🔴 **设备回退可能输出到错误设备**（用户听不到 TTS）
2. 🔴 **设备回退可能无限循环**（程序卡死）
3. 🔴 **设备列表变化未处理**（崩溃或错误输出）

### 已知 Bug
1. 设备回退后配置文件未更新（每次启动都回退）
2. 多个虚拟设备时选择不确定
3. 设备回退时采样率探测重复（延迟增加）
4. BERT 模型转换未验证（可能失败）

### 测试缺口
- 设备热插拔
- 网络异常
- 资源耗尽
- 并发压力
- 边界条件

### 建议行动
1. **立即修复 P0 问题**（设备回退相关）
2. **补充单元测试**（设备回退、错误处理）
3. **添加集成测试**（完整播放流程）
4. **进行压力测试**（并发、资源耗尽）
5. **用户验收测试**（实际使用场景）
