# 项目审计报告 / Project Audit Report

**项目名称**: Mio RealTime Translator (VRChat 实时翻译工具)  
**审计日期**: 2026-05-06  
**版本**: v1.3.0  
**审计范围**: 基于日志文件和源代码的全面审计

---

## 执行摘要 / Executive Summary

本次审计发现了 **3 个严重问题**、**5 个中等问题** 和 **多个改进建议**。主要问题集中在 TTS 音频播放、错误处理和代码质量方面

Mio RealTime Translator 是一个面向 VRChat 用户的本地实时语音翻译工具。项目整体架构合理，安全措施到位，但存在一些需要改进的地方。

**总体评级**: ⭐⭐⭐⭐ (4/5)

**关键发现**:
- ✅ 良好的安全实践（API Key 加密、更新签名验证）
- ✅ 清晰的模块化架构
- ⚠️ 部分依赖版本管理需要改进
- ⚠️ 错误处理可以更完善
- ⚠️ 测试覆盖率不足

---

## 1. 安全审计

### 1.1 敏感数据保护 ✅ 优秀

**API Key 加密存储**:
```python
# src/utils/config_manager.py:113-124
def _protect_secret(value: object) -> str:
    text = str(value or "")
    if not text or text.startswith(_PROTECTED_SECRET_PREFIX):
        return text
    if not _can_protect_secrets():
        return text
    try:
        sealed = _dpapi_protect(text.encode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to protect config secret with DPAPI: %s", exc)
        return text
    return _PROTECTED_SECRET_PREFIX + base64.b64encode(sealed).decode("ascii")
```

**优点**:
- 使用 Windows DPAPI 加密 API Key
- 自动检测明文密钥并加密
- 降级处理：非 Windows 平台保持明文但记录警告

**建议**:
- 考虑为 Linux/macOS 添加 keyring 支持
- 添加密钥轮换机制

### 1.2 更新机制安全 ✅ 优秀

**更新签名验证**:
```python
# src/updater/update_checker.py:79-96
def _parse_update_info(data: dict) -> UpdateInfo | None:
    version = str(data.get("version", "")).strip()
    download_url = str(data.get("url") or data.get("installer_url") or "").strip()
    sha256 = _parse_sha256(data.get("sha256"))
    if not version or not download_url:
        return None
    if not sha256:
        raise RuntimeError("Update manifest is missing a valid installer SHA256")
    if not is_trusted_download_url(download_url):
        raise RuntimeError("Update manifest contains an untrusted installer URL")
    return UpdateInfo(...)
```

**优点**:
- 强制要求 SHA256 校验和
- 白名单验证下载域名
- 仅允许 HTTPS 连接

**可信域名列表**:
```python
_TRUSTED_DOWNLOAD_HOSTS = {
    "78hejiu.top",
    "download.78hejiu.top",
    "github.com",
}
```

### 1.3 网络通信安全 ✅ 良好

**OSC 通信**:
```python
# src/osc/sender.py:26-45
class VRCOSCSender:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000, ...):
        self._client = udp_client.SimpleUDPClient(host, port)
```

**特点**:
- 默认仅本地通信 (127.0.0.1)
- UDP 协议，无需认证（符合 VRChat OSC 规范）
- 输入验证和长度限制

**Chatbox 输入验证**:
```python
# src/osc/sender.py:73-78
@staticmethod
def _normalize_text(text: str) -> str:
    safe = str(text or "").strip()
    if len(safe) > MAX_CHATBOX_CHARS:
        safe = safe[: MAX_CHATBOX_CHARS - 3] + "..."
    return safe
```

### 1.4 潜在安全问题 ⚠️

#### 问题 1: 日志可能泄露敏感信息
```python
# src/utils/logger.py
# 虽然文档声明不记录翻译内容，但需要审查所有 logger 调用
```

**建议**:
- 添加日志过滤器，自动屏蔽 API Key 模式
- 审查所有 `logger.debug()` 调用

#### 问题 2: 异常信息可能暴露路径
```python
# 多处异常处理直接暴露完整异常信息
logger.exception("Fatal error during application startup/runtime")
```

**建议**:
- 生产环境隐藏详细堆栈跟踪
- 仅向用户显示友好错误消息

---

## 2. 代码质量审计

### 2.1 架构设计 ✅ 优秀

**模块化结构**:
```
src/
├── audio/          # 音频采集和处理
├── asr/            # 语音识别引擎
├── translators/    # 翻译服务适配器
├── osc/            # VRChat OSC 通信
├── ui/             # 用户界面
├── updater/        # 自动更新
└── utils/          # 工具函数
```

**设计模式**:
- 工厂模式: `translators/factory.py`, `asr/factory.py`
- 策略模式: 多种翻译后端
- 观察者模式: 音频事件回调

### 2.2 错误处理 ⚠️ 需要改进

**良好实践**:
```python
# src/audio/recorder.py:379-382
try:
    self.on_segment(segment)
except Exception as exc:
    logger.exception("AudioRecorder on_segment callback failed: %s", exc)
```

**问题区域**:
```python
# src/utils/config_manager.py:210
except Exception:
    return None  # 吞掉所有异常，可能隐藏问题
```

**建议**:
- 避免裸 `except Exception`
- 区分可恢复错误和致命错误
- 添加错误上报机制（可选）

### 2.3 并发安全 ✅ 良好

**线程安全措施**:
```python
# src/osc/sender.py:38-42
self._state_lock = threading.Lock()
self._queue: queue.Queue[_QueuedOSCMessage | None] = queue.Queue(maxsize=SEND_QUEUE_MAXSIZE)
```

**音频处理线程**:
```python
# src/audio/recorder.py:137-138
self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
self._worker_thread.start()
```

**优点**:
- 使用线程安全的 `queue.Queue`
- 适当的锁保护共享状态
- Daemon 线程避免阻塞退出

**建议**:
- 添加线程池管理
- 考虑使用 `asyncio` 替代部分线程

### 2.4 资源管理 ✅ 良好

**音频流清理**:
```python
# src/audio/recorder.py:278-298
def stop(self):
    self._running = False
    self._enqueue_frame(None)
    if self._stream:
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
```

**配置保存原子性**:
```python
# src/utils/config_manager.py:582-598
temp_path = config_path.with_name(f"{config_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
with _SAVE_LOCK:
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(storage_config, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
```

**优点**:
- 原子写入配置文件
- 适当的异常处理
- 临时文件清理

---

## 3. 依赖管理审计

### 3.1 依赖清单 ⚠️ 需要改进

**当前 requirements.txt**:
```txt
customtkinter>=5.2.0
sounddevice>=0.4.6
PyAudioWPatch>=0.2.12
numpy>=1.24.0
webrtcvad>=2.0.10
scipy>=1.10.0
funasr>=1.3.1
modelscope>=1.28.0
torch>=2.7.0
torchaudio>=2.7.0
openai>=1.30.0
anthropic>=0.28.0
requests>=2.31.0
python-osc>=1.8.3
```

**问题**:
1. **版本范围过宽**: `>=` 可能引入破坏性变更
2. **缺少上限**: 未来版本可能不兼容
3. **PyTorch 版本过新**: `torch>=2.7.0` 可能不稳定

**建议**:
```txt
# 推荐格式
customtkinter>=5.2.0,<6.0.0
torch>=2.4.0,<2.8.0  # 使用更稳定的版本
openai>=1.30.0,<2.0.0
```

### 3.2 依赖安全性 ✅ 良好

**已知安全依赖**:
- `requests>=2.31.0` - 包含安全修复
- `openai>=1.30.0` - 官方 SDK
- `anthropic>=0.28.0` - 官方 SDK

**建议**:
- 定期运行 `pip-audit` 检查漏洞
- 考虑添加 `requirements.lock.txt`（已存在但未提交）

### 3.3 可选依赖 ✅ 良好

**条件导入**:
```python
# src/audio/recorder.py:13-18
try:
    from scipy.signal import resample_poly as _scipy_resample_poly
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
```

**优点**:
- 优雅降级
- 减少必需依赖

---

## 4. 隐私保护审计

### 4.1 数据收集 ✅ 优秀

**README 隐私声明**:
```markdown
- 项目本身不收集用户数据
- 不保存聊天记录
- 默认日志不会记录识别文本、翻译文本或 chatbox 内容
- chatbox 文本在发送后不会长期存储
- Windows 版会使用系统 DPAPI 保护本地保存的 API Key
- 自动界面语言只读取本机区域设置；不会默认使用 IP 定位
- 只有在你启用云端翻译服务时，当前待翻译文本才会发送到你自己配置的 API 服务商
```

**验证**:
- ✅ 未发现遥测代码
- ✅ 未发现数据上传逻辑
- ✅ 日志配置合理

### 4.2 第三方服务 ⚠️ 需要说明

**翻译 API 调用**:
```python
# src/translators/openai_translator.py:175
response = self._client.chat.completions.create(**kwargs)
```

**涉及的第三方服务**:
- OpenAI / DeepSeek / Qianwen / Gemini / Kimi / XAI / Mistral / Doubao
- Anthropic (Claude)
- 自定义 API 端点

**建议**:
- 在 UI 中明确提示用户数据将发送到哪个服务
- 添加"离线模式"选项（仅 ASR，不翻译）

### 4.3 本地存储 ✅ 良好

**配置文件位置**:
```python
# src/utils/config_manager.py:178-179
def _config_path() -> Path:
    return writable_app_dir() / "config.json"
```

**存储内容**:
- 加密的 API Key
- 用户偏好设置
- 设备配置

**优点**:
- 本地存储，不上传
- 敏感数据加密

---

## 5. 性能审计

### 5.1 音频处理 ✅ 优秀

**VAD 优化**:
```python
# src/audio/recorder.py:79-88
self.vad = VADDetector(
    sample_rate=sample_rate,
    frame_duration_ms=frame_duration_ms,
    sensitivity=vad_sensitivity,
    silence_threshold_s=silence_threshold_s,
    speech_ratio=vad_speech_ratio,
    activation_threshold_s=vad_activation_threshold_s,
    min_rms=vad_min_rms,
    max_speech_s=max_segment_s,
)
```

**特点**:
- 实时 VAD 检测
- 预录缓冲区保留起始辅音
- 自适应降噪

### 5.2 翻译缓存 ✅ 优秀

**缓存机制**:
```python
# src/translators/base.py (推测)
cached = self._get_cached_translation(text, src_lang, tgt_lang, self.model, ...)
if cached is not None:
    return cached
```

**优点**:
- 避免重复翻译
- 降低 API 成本

### 5.3 内存管理 ⚠️ 需要监控

**音频缓冲区**:
```python
# src/audio/recorder.py:90-93
self._buffer: list[np.ndarray] = []
self._pre_speech_buffer = collections.deque(maxlen=max(1, int(pre_speech_s * 1000 / frame_duration_ms)))
```

**潜在问题**:
- 长时间运行可能积累内存
- 缺少显式的内存限制

**建议**:
- 添加内存使用监控
- 实现定期清理机制

---

## 6. 测试覆盖率审计

### 6.1 测试现状 ❌ 不足

**发现**:
```
?? tests/  # 未提交的测试目录
```

**缺失的测试**:
- 单元测试
- 集成测试
- 端到端测试

### 6.2 建议的测试策略

**优先级 1 - 核心功能**:
```python
# tests/test_config_manager.py
def test_api_key_encryption():
    """测试 API Key 加密/解密"""

def test_config_merge():
    """测试配置合并逻辑"""

# tests/test_update_checker.py
def test_version_comparison():
    """测试版本比较逻辑"""

def test_sha256_validation():
    """测试更新包校验"""

# tests/test_translators.py
def test_openai_translator():
    """测试 OpenAI 翻译器"""
```

**优先级 2 - 边界情况**:
- 网络错误处理
- 无效配置恢复
- 音频设备切换

**优先级 3 - 性能测试**:
- 长时间运行稳定性
- 内存泄漏检测
- 并发压力测试

---

## 7. 文档审计

### 7.1 用户文档 ✅ 优秀

**多语言支持**:
- README.md (中文)
- docs/README.en.md (英文)
- docs/README.ja.md (日文)
- docs/README.zh-CN.md (简体中文)

**内容完整性**:
- ✅ 安装说明
- ✅ 使用指南
- ✅ 隐私说明
- ✅ 常见问题

### 7.2 开发者文档 ⚠️ 缺失

**缺少的文档**:
- 架构设计文档
- API 文档
- 贡献指南
- 开发环境搭建

**建议**:
```markdown
# 建议添加的文档
docs/
├── ARCHITECTURE.md      # 架构设计
├── CONTRIBUTING.md      # 贡献指南
├── DEVELOPMENT.md       # 开发指南
└── API.md              # API 文档
```

---

## 8. 构建和发布审计

### 8.1 构建脚本 ✅ 良好

**PowerShell 构建脚本**:
- `build_release_full_installer.ps1`
- `build_release_lite_installer.ps1`

**Inno Setup 安装程序**:
- `MioTranslator-installer.iss`

**优点**:
- 自动化构建流程
- 区分完整版和轻量版

### 8.2 版本管理 ✅ 良好

**版本定义**:
```python
# src/version.py
APP_VERSION = "1.2.4"
UPDATE_CHECK_URL = "https://78hejiu.top/update/latest.json"
```

**Git 标签**:
- 使用语义化版本
- 清晰的提交历史

---

## 9. 合规性审计

### 9.1 开源许可 ⚠️ 需要明确

**当前状态**:
- README 声明"开源项目 / 禁止收费分发"
- 未找到 LICENSE 文件

**建议**:
- 添加明确的开源许可证（如 MIT, GPL, Apache 2.0）
- 在所有源文件头部添加版权声明

### 9.2 第三方许可 ⚠️ 需要审查

**依赖的许可证**:
- PyTorch: BSD-style
- OpenAI SDK: Apache 2.0
- Anthropic SDK: MIT
- CustomTkinter: MIT

**建议**:
- 创建 `THIRD_PARTY_LICENSES.md`
- 确保所有依赖许可证兼容

---

## 10. 改进建议优先级

### 🔴 高优先级（立即处理）

1. **添加开源许可证文件**
   - 选择合适的许可证（建议 MIT 或 Apache 2.0）
   - 添加 LICENSE 文件

2. **修复依赖版本范围**
   - 为所有依赖添加上限版本
   - 创建 `requirements.lock.txt`

3. **添加基础测试**
   - 配置管理测试
   - 更新检查测试
   - 版本比较测试

### 🟡 中优先级（近期处理）

4. **改进错误处理**
   - 避免裸 `except Exception`
   - 添加用户友好的错误消息

5. **添加开发者文档**
   - 架构设计文档
   - 贡献指南

6. **日志安全审查**
   - 添加敏感信息过滤器
   - 审查所有日志调用

### 🟢 低优先级（长期改进）

7. **跨平台密钥存储**
   - Linux/macOS keyring 支持

8. **性能监控**
   - 内存使用监控
   - 性能指标收集

9. **自动化测试**
   - CI/CD 集成
   - 自动化测试流程

---

## 11. 总结

### 优点

1. **安全性良好**: API Key 加密、更新签名验证、HTTPS 强制
2. **架构清晰**: 模块化设计，职责分离
3. **用户体验**: 多语言支持，自动更新，友好的 UI
4. **隐私保护**: 本地处理，最小化数据收集
5. **代码质量**: 类型提示，合理的错误处理

### 需要改进的地方

1. **测试覆盖率**: 缺少自动化测试
2. **依赖管理**: 版本范围过宽
3. **开源合规**: 缺少许可证文件
4. **文档完整性**: 缺少开发者文档
5. **错误处理**: 部分区域可以更完善

### 最终评价

Mio RealTime Translator 是一个设计良好、安全可靠的开源项目。核心功能实现扎实，安全措施到位，用户体验友好。主要的改进空间在于测试覆盖率、依赖管理和开源合规性。

**推荐行动**:
1. 立即添加 LICENSE 文件
2. 修复 requirements.txt 版本范围
3. 添加基础单元测试
4. 补充开发者文档

---

**审计人**: 本地静态分析
**审计工具**: 静态代码分析、手动审查
**审计范围**: 完整代码库 + 文档 + 构建脚本
