# 同类 VRChat 翻译项目参考功能落地方案

> 基于对当前 Mio / vrc-translator 项目，以及 Kikitan、VRCT、VRCLS 三个同类项目的对比审计整理。本文档用于指导后续功能规划、架构重构和分阶段实现。

## 1. 背景与目标

当前项目已经具备较完整的 VRChat 实时翻译能力：

- 麦克风采集、VAD 分段、ASR 识别。
- 桌面音频 / VRChat 听译链路。
- 多翻译后端、fallback、错误友好提示。
- Chatbox OSC 输出与 Avatar 参数输出。
- TTS、虚拟输出设备、同传模式。
- Qt 主界面、设置页、浮窗、模型下载与更新能力。

对比参考项目后，建议优先补齐以下方向：

1. 将当前集中在 `MainWindow` 的运行逻辑逐步服务化。
2. 增强 VRChat OSC 双向同步，而不仅是单向发送。
3. 增加 SteamVR / OpenVR 字幕 Overlay，减少对 Chatbox 刷屏的依赖。
4. 增加 VAD 校准和音频诊断，降低用户调参成本。
5. 增加按玩家选择的游戏 / 程序进程采集音频的 Process Loopback，减少误采集和回声；VRChat 只是默认 preset，不作为唯一目标。
6. 增加首次启动 / 模式向导，降低复杂功能的入门门槛。
7. 重组设置页，让识别、翻译、TTS、VR 集成、更新等功能分区更清晰。
8. 增加语音控制、OCR / 拍照翻译、热更新体系等长期增强能力。

本文档的实现建议遵循两个项目约束：

- **保持当前正式 UI 风格，不进行无关 redesign 或简化。** 新功能应融入现有 Qt UI 语言、布局密度和视觉层级。
- **应用数据、缓存、模型、临时文件默认继续保留在安装目录 / 项目可写目录下。** 不默认迁移到 AppData，除非用户主动配置。

---

## 2. 参考项目可借鉴点

### 2.1 Kikitan

参考仓库：<https://github.com/YusufOzmen01/kikitan-translator>

可借鉴点：

- **OSC 双向状态同步**
  - 发送 `/chatbox/input`、`/chatbox/typing`。
  - 使用 Avatar 参数控制麦克风、桌面听译、Chatbox、Overlay 开关。
  - 监听 VRChat 的 `MuteSelf` 参数并同步到应用状态。
- **OpenVR Overlay**
  - 独立 C# Overlay 项目提供 VR 内字幕显示方向。
- **流式 STT 状态接口**
  - partial / final 结果分离。
  - 识别连接状态可展示给 UI。
  - 手动输入复用翻译回调。
- **Quickstart / Changelog / 模式说明**
  - 适合作为 Mio 首次启动向导和功能模式卡片参考。

### 2.2 VRCT

参考仓库：<https://github.com/misyaguziya/VRCT>

可借鉴点：

- **Python 后端 command / event 架构**
  - UI 通过 endpoint 发命令。
  - 后端用队列、多 worker、按功能锁处理耗时任务。
  - 后端主动上报初始化、下载、设备错误、运行状态等事件。
- **麦克风与扬声器转写作为一等功能**
  - 明确区分“我说话给别人看”和“别人说话给我看”。
- **CPU / CUDA 构建分流**
  - 有助于降低用户对 GPU 环境的困惑。
- **匿名遥测说明方式**
  - 如果未来加入遥测或崩溃上报，应提供明确开关和透明说明。

### 2.3 VRCLS

参考仓库：<https://github.com/VoiceLinkVR/VRCLS>

可借鉴点：

- **Core / Services / UI 三层架构**
  - Core 放接口、模型、配置。
  - Services 按 Audio、Recognition、Translation、TTS、OSC、VR、Update、Command、Photo 拆分。
  - UI 使用 ViewModel 和 Settings Tabs 组织显示逻辑。
- **Process Loopback Capture**
  - 只采集玩家选择的目标游戏 / 程序进程音频，减少系统音、音乐、TTS 回声误采集。
  - VRChat 可作为默认 preset，但架构不绑定单一游戏。
- **VAD Calibration / Confidence Meter**
  - 可视化显示噪声、音量、speech confidence，帮助用户调参。
- **SteamVR Overlay Service**
  - VR 内显示字幕和翻译结果，减少 Chatbox 依赖。
- **Voice Control / CommandService**
  - VR 内用语音控制应用开关、模式和语言。
- **OCR / Photo Translation**
  - 翻译 VRChat 世界中的公告、菜单、海报、活动图。
- **应用更新 / 模型更新 / 翻译配置热更新**
  - 将不同更新域拆开，便于稳定发布。

---

## 3. 当前项目架构现状

当前关键路径：

```text
main.py
  └─ src.ui_qt.app.run_qt_app(config)
      └─ src.ui_qt.main_window.MainWindow
          ├─ 创建 ASR pair
          ├─ 创建 OSC sender
          ├─ 启动 partial / final worker queue
          ├─ 启动麦克风采集
          ├─ 可选启动桌面听译
          ├─ 执行 ASR → 翻译 → UI / OSC / TTS 输出
          └─ 管理设置、热键、更新、错误退避
```

主要模块：

| 模块 | 当前职责 | 备注 |
|---|---|---|
| `main.py` | 启动、环境检查、CUDA 安装 / 验证、配置加载、Qt 启动 | 职责较合理 |
| `src/ui_qt/main_window.py` | UI + 实时运行管线 + 音频 + ASR + 翻译 + OSC + TTS + 设置联动 | 最大耦合热点 |
| `src/audio/recorder.py` | 麦克风采集、VAD、分段、partial chunk | 功能完整，可服务化 |
| `src/audio/desktop_recorder.py` | 桌面环回采集、设备枚举、SoundCard / PyAudio fallback | 可扩展 Process Loopback |
| `src/asr/factory.py` | ASR 后端创建、fallback | 边界清晰 |
| `src/translators/factory.py` | 翻译后端创建、fallback | 边界清晰 |
| `src/osc/sender.py` | Chatbox / Avatar 参数发送、队列、限流 | 建议扩展 listener |
| `src/tts/manager.py` | TTS 队列、缓存、输出设备、失败暂停 | 可封装为 TTS service |
| `src/core/*` | runtime / pipeline 雏形 | 尚未承接主要实时链路 |

核心问题：

- `MainWindow` 同时是 UI、Controller、Service Coordinator、Pipeline 和状态容器。
- 增加 Overlay、OSC listener、Process Loopback、VAD 校准时，如果继续直接堆到 `MainWindow`，维护成本会明显上升。
- 当前 core 层已有雏形，但未成为主架构边界。

---

## 4. 建议目标架构

建议逐步演进，不一次性大重构。

```text
src/
  core/
    app_events.py              # 运行事件、UI 事件、错误事件定义
    app_commands.py            # Start/Stop/Translate/Send/SetConfig 等命令定义
    runtime_state.py           # 运行态快照
    pipeline_coordinator.py    # 统一编排 mic/listen/manual/tts/osc

  services/
    audio_capture_service.py   # 麦克风采集封装
    desktop_capture_service.py # 桌面 / 进程音频采集封装
    vad_calibration_service.py # VAD 校准、音频诊断
    asr_service.py             # ASR load/transcribe/fallback 包装
    translation_service.py     # 翻译、fallback、错误退避
    osc_service.py             # OSC sender + listener + VRChat 状态同步
    overlay_service.py         # SteamVR/OpenVR overlay 抽象
    tts_service.py             # TTS manager 包装
    update_service.py          # app/model/catalog/dictionary 更新入口
    voice_command_service.py   # 语音命令解析和执行
    ocr_translation_service.py # 截图/OCR/翻译

  ui_qt/
    main_window.py             # 保留正式 UI，逐步瘦身为显示和用户交互
    settings_window.py
    view_models/
      runtime_view_model.py
      settings_view_model.py
```

### 4.1 命令 / 事件流

建议参考 VRCT 的 endpoint 思路，但在 Python Qt 内部可以先用对象队列，不必做子进程通信。

```text
UI action
  └─ AppCommand
      └─ PipelineCoordinator
          ├─ Service call
          ├─ RuntimeState update
          └─ AppEvent
              └─ UI callback / Qt signal
```

示例命令：

| 命令 | 用途 |
|---|---|
| `StartMicPipeline` | 启动麦克风实时翻译 |
| `StopMicPipeline` | 停止麦克风实时翻译 |
| `StartListenPipeline` | 启动桌面 / VRChat 听译 |
| `StopListenPipeline` | 停止听译 |
| `TranslateManualText` | 手动文本翻译 |
| `SendChatboxText` | 发送 Chatbox |
| `SetMicMuted` | 设置麦克风静音 |
| `SetOutputFormat` | 设置输出格式 |
| `StartOverlay` | 启动 VR Overlay |
| `RunVadCalibration` | 开始 VAD 校准 |
| `DownloadModel` | 下载 / 更新模型 |

示例事件：

| 事件 | 用途 |
|---|---|
| `RuntimeStateChanged` | idle / starting / running / muted / error |
| `PartialTranscriptReady` | partial ASR 文本 |
| `FinalTranscriptReady` | final ASR 文本 |
| `TranslationReady` | 翻译结果 |
| `ChatboxSendQueued` | Chatbox 已排队 |
| `OscAvatarStateChanged` | VRChat avatar 参数变更 |
| `OverlayStatusChanged` | Overlay 连接 / 显示状态 |
| `VadMeterUpdated` | RMS / confidence / speech state |
| `ModelDownloadProgress` | 模型下载进度 |
| `FriendlyErrorRaised` | 友好错误提示 |

---

## 5. 功能落地方案

## 5.1 OSC 双向同步

### 目标

将当前 OSC 单向发送增强为双向状态同步：

- 向 VRChat 发送 Chatbox 文本。
- 向 VRChat 发送 Avatar 参数。
- 从 VRChat 接收 Avatar 参数变化。
- 同步 VRChat mute/self 状态到应用麦克风状态。
- 允许 Avatar 菜单控制 Mio 功能开关。

### 建议 OSC 地址

发送：

| 地址 | 参数 | 用途 |
|---|---|---|
| `/chatbox/input` | `(text, true, false)` | 发送 Chatbox |
| `/chatbox/typing` | `(bool)` | 可选：显示正在输入 |
| `/avatar/parameters/MioMicActive` | `bool` | 麦克风翻译状态 |
| `/avatar/parameters/MioListenActive` | `bool` | 听译状态 |
| `/avatar/parameters/MioTtsActive` | `bool` | TTS 状态 |
| `/avatar/parameters/MioOverlayActive` | `bool` | Overlay 状态 |
| `/avatar/parameters/MioError` | `bool` | 错误提示状态 |

接收：

| 地址 | 参数 | 用途 |
|---|---|---|
| `/avatar/parameters/MuteSelf` | `bool` | 同步 VRChat 自身静音 |
| `/avatar/parameters/MioToggleMic` | `bool` | Avatar 菜单控制麦克风翻译 |
| `/avatar/parameters/MioToggleListen` | `bool` | Avatar 菜单控制听译 |
| `/avatar/parameters/MioToggleTts` | `bool` | Avatar 菜单控制 TTS |
| `/avatar/parameters/MioToggleOverlay` | `bool` | Avatar 菜单控制 Overlay |

### 实现建议

1. 在 `src/osc/` 新增 `listener.py`。
2. 新增 `OSCService` 包装现有 `VRCOSCSender` 和 listener。
3. listener 独立线程绑定接收端口，收到消息后转为 `AppEvent`。
4. UI 设置页新增：
   - 接收端口。
   - 是否同步 VRChat mute。
   - 是否允许 Avatar 参数控制应用。
   - 参数名前缀，默认 `Mio`。
5. 对接 `MainWindow` 时先保持最小侵入：
   - 收到 `MuteSelf=true` 调用现有静音逻辑。
   - 收到 toggle 参数后调用现有 start/stop 方法。

### 验收标准

- VRChat OSC 开启后，应用可收到 `MuteSelf` 状态变化。
- Avatar 参数切换可控制 Mio 麦克风翻译 / 听译 / TTS / Overlay。
- OSC listener 停止后端口释放，无后台线程残留。
- 网络异常、端口占用、非法参数不会导致应用崩溃。

---

## 5.2 SteamVR / OpenVR Overlay

### 目标

提供 VR 内字幕显示能力，让用户不必只依赖 Chatbox：

- 显示听译字幕。
- 显示我方语音的原文 / 译文。
- 显示手动输入翻译结果。
- 可选显示状态：ASR、翻译中、错误、静音。

### 显示模式

| 模式 | 用途 |
|---|---|
| `listen_only` | 只显示听译，即“别人说的话” |
| `mic_only` | 只显示我方语音识别 / 翻译 |
| `all` | 显示全部实时文本 |
| `errors_only` | 只显示状态和错误 |

### 推荐方案

分两阶段：

#### 阶段 A：桌面透明浮窗增强

- 复用当前 Qt 浮窗能力。
- 增加更适合 VR 桌面捕获的“高对比字幕模式”。
- 支持固定宽度、自动换行、显示最近 N 条。
- 低风险，可快速验证字幕展示体验。

#### 阶段 B：OpenVR Overlay Service

实现方式候选：

1. Python 调用 OpenVR / pyopenvr。
2. 独立 C# / Rust helper 进程负责 Overlay，Python 通过本地 HTTP / stdin JSON 通信。
3. 若后续计划 Tauri / Rust 迁移，可用 Rust OpenVR wrapper。

建议优先 **独立 helper 进程**：

- 避免 OpenVR SDK 与 PyInstaller / PySide 主进程耦合。
- Overlay 崩溃不影响主应用。
- 后续可替换实现。

### 数据流

```text
ASR / Translation result
  └─ OverlayService.update_text(event)
      └─ helper process / local HTTP
          └─ SteamVR Overlay panel
```

### 配置项

- 是否启用 Overlay。
- Overlay 显示模式。
- 字体大小。
- 面板宽度 / 高度。
- 背景透明度。
- 显示位置 preset。
- 最近消息数量。
- 是否显示原文。
- 是否显示翻译。

### 验收标准

- SteamVR 已运行时可创建 Overlay。
- VR 内能看到实时字幕更新。
- SteamVR 未运行时应用不崩溃，显示友好提示。
- Overlay helper 退出后可自动重启或提示用户。

---

## 5.3 VAD 校准与音频诊断

### 目标

降低用户调参成本，解决常见问题：

- 说话不触发。
- 环境噪声误触发。
- 切句太早 / 太晚。
- 桌面听译一直识别 TTS 回声。
- 虚拟声卡设备选错。

### 功能设计

新增“音频诊断 / VAD 校准”窗口：

```text
[麦克风] [桌面听译]

输入设备：...
当前 RMS：||||||||----
峰值 RMS：||||||||||||
VAD 状态：Speech / Silence
Speech Ratio：0.72
建议 vad_min_rms：0.010
建议 silence：0.65s

[开始环境噪声采样] [开始说话测试] [应用推荐参数]
```

### 实现步骤

1. 复用 `AudioRecorder.diagnostics_snapshot()` 或扩展 recorder 回调。
2. 增加 `VadCalibrationService`：
   - 采集 5 秒环境噪声。
   - 采集 5 秒用户正常说话。
   - 计算 noise floor、speech RMS、推荐阈值。
3. 对桌面听译单独校准，避免与麦克风混用参数。
4. 设置页提供“恢复默认 / 使用推荐 / 高级参数”。

### 推荐参数计算

初版可使用简单规则：

```text
noise_floor = percentile(noise_rms, 90)
speech_floor = percentile(speech_rms, 30)
recommended_min_rms = clamp((noise_floor * 1.8 + speech_floor * 0.4) / 2, 0.003, 0.04)
```

后续再接入 Silero confidence meter。

### 验收标准

- 用户可以看到实时音量和 VAD 状态。
- 校准后能写入当前配置。
- 配置变更不要求重启整个应用，最多重启对应采集链路。
- 对麦克风和桌面听译分别生效。

---

## 5.4 Process Loopback / 目标游戏进程音频采集

### 目标

只采集玩家选择的目标游戏 / 程序进程音频，减少其他应用声音、系统提示音、TTS 回声进入听译。VRChat 只是默认推荐项之一，Mio 应服务所有游戏玩家。

### 现状

当前桌面采集主要按输出设备环回采集，适合通用场景，但容易采到：

- 浏览器音乐。
- 系统提示音。
- 自己的 TTS 输出。
- Discord / 其他语音软件。

### 推荐实现路线

#### 方案 A：Windows 进程环回 API

Windows 10/11 支持按进程 loopback capture。可用方向：

- C++ / C# helper 实现进程 loopback。
- Python 主进程通过 pipe / local socket 接收 PCM。
- helper 专注音频捕获，主进程继续负责 VAD / ASR。

#### 方案 B：保留设备环回 + 进程检测辅助

短期可先做：

- 枚举正在运行的游戏 / 程序进程，让玩家选择需要监听的目标。
- 提供 VRChat preset，但允许输入任意进程名，例如 `VRChat.exe`、`Game.exe`、`UnityPlayer.exe`。
- 自动选择更可能承载目标进程声音的输出设备。
- 提示用户把目标游戏输出切到 MixLine / 虚拟设备。

### 建议阶段

1. P1：增加目标进程列表 / 运行状态检测和提示，不改变采集实现。
2. P2：新增 helper proof-of-concept，只输出 PCM 到主进程。
3. P3：整合到 `DesktopCaptureService`，支持采集模式：
   - `output_device_loopback`
   - `process_loopback_target`
   - `manual_process_loopback`

### 配置项

- 采集模式。
- 目标进程名列表，默认可预填 `VRChat.exe`，但玩家可选择或输入任意游戏 / 程序进程。
- 进程 preset，例如 VRChat / Steam 游戏 / 自定义。
- 最近选择的进程列表。
- 若目标进程不存在时是否回退到设备环回。
- 是否自动抑制 TTS 回声。

### 验收标准

- 只播放浏览器音乐时，process loopback 不触发听译。
- VRChat 有声音时，听译正常触发。
- VRChat 重启后可重新绑定。
- helper 异常退出不会导致主应用崩溃。

---

## 5.5 首次启动 / 模式向导

### 目标

把复杂功能转换为用户能理解的使用模式。

### 推荐模式卡片

| 模式 | 用户目标 | 默认开启 |
|---|---|---|
| Chatbox 翻译 | 我说话，翻译后发给别人 | 麦克风 ASR + 翻译 + Chatbox |
| 听别人说话 | 别人说话，翻译给我看 | 桌面听译 + 浮窗 |
| 同传 / TTS | 翻译后用 TTS 输出到虚拟麦克风 | 麦克风 ASR + 翻译 + TTS |
| 手动输入 | 手动输入文本，翻译后发 Chatbox | 文本输入窗口 |
| VR 字幕 | 在 VR 内看字幕 | Overlay + 听译 |

### 向导步骤

1. 选择使用模式。
2. 检查 OSC 是否开启。
3. 选择麦克风。
4. 选择桌面 / VRChat 音频来源。
5. 选择 ASR 引擎。
6. 选择翻译后端和目标语言。
7. 可选配置 TTS / 虚拟设备。
8. 运行测试：说一句话 → 识别 → 翻译 → 输出预览。

### UI 约束

- 不改变现有正式主界面风格。
- 向导作为独立 dialog / welcome window。
- 用户可跳过，也可从设置页重新打开。

### 验收标准

- 首次用户 5 分钟内能完成基础 Chatbox 翻译配置。
- OSC 未开启时能给出明确图示和步骤。
- 测试失败能定位到麦克风、ASR、翻译、OSC 中的具体环节。

---

## 5.6 设置页重组

### 目标

降低设置页复杂度，按功能域分组，而不是按历史堆叠排列。

### 建议分类

| 设置页 | 内容 |
|---|---|
| 外观 | 主题、语言、字体、浮窗、Overlay 外观 |
| 识别 | ASR 引擎、麦克风、VAD、模型、partial、fallback |
| 听译 | 桌面 / 进程采集、听译语言、听译输出、回声抑制 |
| 翻译 | 后端、API Key、模型、fallback、输出格式、多目标语言、术语表 |
| TTS / 同传 | TTS 引擎、音色、输出设备、缓存、同传策略 |
| VR 集成 | OSC、Avatar 参数、Chatbox、Overlay、VRChat 状态同步 |
| 热键 / 语音控制 | 全局热键、语音命令、命令动作 |
| 更新 / 模型 | 应用更新、ASR 模型、TTS 模型、catalog、字典 |
| 高级 | 日志、性能模式、调试、导入导出配置 |

### 实现建议

- 保留当前 `SettingsWindow` 的视觉风格。
- 先增加导航分组，不大幅重写控件。
- 将设置读写统一交给 `SettingsViewModel` 或 `ConfigService`。
- 每页只负责 UI，不直接管理实时运行线程。

### 验收标准

- 原有配置项不丢失。
- 配置迁移保持兼容。
- 常用设置可以在 2 次点击内找到。
- 运行中修改设备 / 语言 / 输出格式有明确应用策略。

---

## 5.7 语音控制

### 目标

允许用户在 VR 内通过语音控制 Mio，减少摘头显或切窗口操作。

### 示例命令

| 命令 | 动作 |
|---|---|
| “Mio 开始翻译” | 启动麦克风翻译 |
| “Mio 停止翻译” | 停止麦克风翻译 |
| “Mio 开始听译” | 启动桌面 / VRChat 听译 |
| “Mio 静音” | 静音麦克风翻译 |
| “Mio 切换日语” | 目标语言切到日语 |
| “Mio 打开字幕” | 启动 Overlay / 浮窗 |
| “Mio 关闭 TTS” | 停止 TTS 输出 |

### 实现建议

1. 从现有 ASR final 文本中检测命令，不新增识别链路。
2. 命令必须有唤醒词，默认 `Mio`，避免误触发。
3. 命令识别后不发送到 Chatbox。
4. 配置页支持自定义命令短语。
5. 后续可支持 Avatar 参数或热键触发命令模式。

### 风险控制

- 默认关闭。
- 唤醒词必需。
- 高风险动作需要二次确认或只允许本地动作，例如不执行任意脚本。
- 如果未来支持脚本执行，应使用白名单和明确授权。

### 验收标准

- 开启后能稳定识别 5 个基础命令。
- 普通聊天中不明显误触发。
- 命令执行有 UI / Overlay 状态反馈。

---

## 5.8 OCR / 拍照翻译

### 目标

翻译 VRChat 世界内的文字，例如：

- 世界菜单。
- 公告牌。
- 活动海报。
- 图片说明。
- 截图中的文本。

### 实现阶段

#### 阶段 A：本地截图 OCR

- 用户选择图片文件。
- OCR 提取文字。
- 调用现有翻译后端。
- 在 UI 中显示原文 / 译文。

#### 阶段 B：屏幕区域 OCR

- 用户框选屏幕区域。
- 自动截图。
- OCR + 翻译。

#### 阶段 C：VRChat 截图目录监听

- 监听 VRChat 截图目录。
- 新截图出现后自动 OCR / 翻译。
- 可选发送到 Overlay 或浮窗。

### OCR 引擎候选

| 引擎 | 优点 | 风险 |
|---|---|---|
| PaddleOCR | 中文 / 日文等多语言较强 | 依赖较大 |
| EasyOCR | 上手简单 | 性能和准确率需测试 |
| Windows OCR API | 系统集成轻量 | 语言包和 API 兼容性 |
| 云 OCR | 准确率高 | 隐私、网络、费用 |

### 验收标准

- 能翻译本地图片中的中 / 英 / 日常见文本。
- 截图失败、OCR 无文本、翻译失败都有友好提示。
- 默认不上传图片到远端，除非用户明确选择云 OCR。

---

## 5.9 更新体系增强

### 目标

把不同更新域分开管理，避免“应用更新”和“模型 / catalog 更新”混在一起。

### 建议更新域

| 更新域 | 内容 | 触发 |
|---|---|---|
| 应用更新 | exe / 安装包 / release manifest | 启动延迟检查、手动检查 |
| ASR 模型更新 | SenseVoice、Whisper、Qwen 配置 | 设置页、首次使用 |
| TTS 模型 / 音色更新 | Style-Bert-VITS2、Voice list、API voice catalog | 设置页、TTS 页 |
| 翻译 catalog 热更新 | 后端列表、模型列表、base_url、区域 | 启动异步、手动刷新 |
| 字典 / 术语表更新 | ASR correction、翻译 glossary | 手动刷新、版本提示 |
| Overlay helper 更新 | OpenVR helper binary | Overlay 首次使用 / 应用更新 |

### 实现建议

- 新增 `UpdateService` 统一对外，但内部按 domain 拆分。
- manifest 增加：
  - version
  - sha256
  - size
  - required_app_version
  - release_notes_url
  - rollback information
- 下载路径继续走安装目录 / writable app dir。
- 下载完成后先校验，再替换。
- 模型更新不强制影响应用更新。

### 验收标准

- 应用更新失败不会破坏模型文件。
- 模型下载失败不会影响主程序启动。
- catalog 更新失败使用缓存。
- 用户能在设置页看到每个更新域的状态。

---

## 6. 分阶段路线图

### 当前实现状态（2026-06-07）

本节用于快速定位下次继续开发时的剩余工作。

已完成 / 基本完成：

- **P0 架构准备与低风险体验增强**
  - 已有 `AppEvent` / `AppCommand` 基础定义。
  - 已抽出 `OSCService`，封装 OSC sender / listener。
  - 已有音频诊断窗口与 VAD 校准服务 / 窗口。
  - 已有首次启动 / 模式向导原型，并能从设置页重新打开。
  - 设置页已按功能域完成导航分组。
- **P1 OSC 双向同步 + VAD 校准**
  - 已实现 OSC listener、`MuteSelf` 同步、Avatar 参数控制麦克风 / 听译 / TTS / Overlay 开关。
  - 已实现麦克风与听译 VAD 校准，支持写回配置并重启对应采集链路。
- **P2 服务化试点**
  - 已抽出 `ManualTranslationController`，手动翻译不再完全堆在 `MainWindow`。
  - 已抽出 `OutputDispatcher`，统一 Chatbox 格式化、模板、多目标语言输出和发送入口。
  - 手动翻译、麦克风实时翻译、听译 Chatbox 输出已逐步接入 `OutputDispatcher`。
  - 已补充启动失败 / 启动取消资源清理、停止 / 重启后旧翻译结果不再误发 Chatbox 的保护。
  - 已恢复主题切换淡入淡出过渡，并避免旧设置保存失败污染共享配置。

仍需继续：

- **P2 最新推进**
  - 已新增 `OverlayService` 抽象，并将现有 Qt 浮窗接为 desktop overlay backend。
  - `OutputDispatcher` 已支持 `OutputMessage` 和可注册 sink，麦克风、听译、手动翻译结果已接入 overlay sink。
  - Overlay 开关状态已同步到 Avatar 参数 `MioOverlayActive`，并补充默认配置。
  - 已新增 `MicPipeline` / `ListenPipeline` 最小切片，接管 final 文本后的翻译计划、翻译结果和 `OutputMessage` 构建。
  - 主界面显示与 TTS 已注册为 `OutputDispatcher` sink，后续可继续将 Chatbox / OpenVR helper 统一接入。
  - P3 Process Loopback 方向已改为玩家可选择任意游戏 / 程序进程，VRChat 仅作为 preset。
- **P2 剩余**
  1. 抽出 `MicPipeline` 和 `ListenPipeline`，继续减少 `MainWindow` 直接处理 ASR / 翻译 / 输出细节。
  2. 继续扩展 `OutputDispatcher` sink：UI、TTS、独立 OpenVR helper 统一事件入口。
  3. 实现独立 OpenVR helper，并接入 `OverlayService` 的第二 backend。
  4. 设置页新增真正的 VR Overlay 配置，而不是仅复用听译浮窗开关。
- **P3 剩余**
  1. Process Loopback helper PoC 尚未实现，目标应是任意玩家选择的进程，而不是 VRChat 专用。
  2. `DesktopCaptureService` / 运行态 snapshot 尚未完成。
  3. 设置页虽然已分组，但尚未完全迁移到 `SettingsViewModel` / `ConfigService`。
  4. 需要设置页进程选择器 / 最近进程列表 / preset 管理。
- **P4 剩余**
  1. 语音控制、OCR / 拍照翻译、更新体系按 domain 拆分尚未实现。

### P0：架构准备与低风险体验增强

周期建议：1-2 个迭代。

任务：

1. 新增 `AppEvent` / `AppCommand` 基础定义。
2. 抽出 `OSCService` wrapper，但仍复用现有 `VRCOSCSender`。
3. 增加音频诊断数据面板最小版本。
4. 增加首次启动 / 模式向导文案和 UI 原型。
5. 整理设置页分组方案，不立即迁移所有控件。

验收：

- 不改变现有主功能行为。
- 新增服务可以单元测试。
- UI 风格与现有正式界面一致。

### P1：OSC 双向同步 + VAD 校准

周期建议：2-4 个迭代。

任务：

1. 实现 OSC listener。
2. 支持 VRChat `MuteSelf` 同步。
3. 支持 Avatar 参数控制麦克风 / 听译 / TTS。
4. 实现 VAD 校准窗口。
5. 设置页新增 OSC 接收、Avatar 参数和 VAD 校准入口。

验收：

- VRChat 内可控制 Mio 基础开关。
- 用户可通过校准改善误触发 / 不触发问题。

### P2：管线服务化 + Overlay MVP

周期建议：3-6 个迭代。

任务：

1. 抽出 `MicPipeline` 和 `ListenPipeline`。
2. 抽出 `ManualTranslationController`。
3. 抽出 `OutputDispatcher`，统一 UI / Chatbox / TTS / Overlay 输出。
4. 实现 Overlay helper MVP。
5. 设置页新增 VR Overlay 设置。

验收：

- `MainWindow` 不再直接处理全部 ASR / 翻译 / 输出细节。
- VR 内能看到字幕。
- Overlay 崩溃不影响主程序。

### P3：Process Loopback + 设置页重组

周期建议：4-8 个迭代。

任务：

1. 增加目标游戏 / 程序进程枚举、选择和运行状态检测。
2. 实现 Process Loopback helper PoC，支持指定进程名 / PID。
3. 整合到 `DesktopCaptureService`。
4. 设置页按功能域重组，并加入进程选择器、preset、最近进程。
5. 增加运行态 snapshot，减少 UI 直接读写服务状态。

验收：

- 能只捕获玩家选择的目标游戏 / 程序声音。
- 只播放浏览器音乐时，如果浏览器不是目标进程，不触发听译。
- 目标游戏重启后可重新绑定。
- 设置项迁移完整，无旧配置丢失。

### P4：语音控制 + OCR / 拍照翻译 + 更新体系完善

周期建议：长期。

任务：

1. 实现唤醒词语音命令。
2. 支持自定义命令短语。
3. 实现本地图片 OCR 翻译。
4. 实现屏幕区域 / VRChat 截图目录 OCR。
5. 拆分 app/model/catalog/dictionary 更新域。

验收：

- VR 内可用语音控制常见动作。
- 图片 / 截图翻译可稳定使用。
- 更新状态清晰、失败可回滚或降级。

---

## 7. 测试建议

### 单元测试

- OSC 消息解析与参数校验。
- Chatbox 截断和限流。
- VAD 推荐参数计算。
- 命令短语匹配。
- 更新 manifest 校验。
- OCR 空文本 / 多语言文本处理。

### 集成测试

- 麦克风 ASR → 翻译 → Chatbox。
- 桌面听译 → 翻译 → 浮窗 / Overlay。
- VRChat mute → Mio 静音。
- Avatar 参数 → Mio start/stop。
- TTS 输出 → 回声抑制。
- 模型下载失败 → fallback / 提示。

### 手动验收场景

1. VRChat OSC 未开启。
2. VRChat OSC 已开启但端口被占用。
3. 麦克风设备不存在。
4. 默认输出设备变化。
5. VRChat 重启。
6. SteamVR 未启动。
7. SteamVR 已启动但 Overlay helper 崩溃。
8. 翻译后端限额 / 网络错误。
9. TTS 虚拟设备不存在。
10. 用户从旧配置升级。

---

## 8. 风险与注意事项

### 架构重构风险

- 避免一次性重写 `MainWindow`。
- 优先抽边界清晰的 OSC、TTS、manual translation。
- 每次抽取后保留回归测试。

### Overlay 风险

- OpenVR 依赖、SteamVR 状态、PyInstaller 打包都可能带来不稳定。
- 建议 helper 进程隔离，避免主应用崩溃。

### Process Loopback 风险

- Windows 版本差异和音频 API 复杂。
- 建议先 PoC，再产品化。
- 保留设备环回 fallback。

### OSC 双向控制风险

- Avatar 参数可能被误触发。
- 默认只同步 `MuteSelf`，其他控制项默认关闭。
- 参数名允许用户自定义前缀，避免与其他 Avatar 参数冲突。

### 语音控制风险

- 必须默认关闭。
- 必须要求唤醒词。
- 不执行任意外部命令，除非未来有明确白名单和安全确认。

### OCR 隐私风险

- 默认本地 OCR。
- 云 OCR 必须明确提示图片会上传。
- 不自动上传 VRChat 截图。

---

## 9. 推荐优先级总结

| 优先级 | 功能 | 理由 |
|---|---|---|
| 高 | OSC 双向同步 | VRChat 集成体验提升明显，现有 sender 基础好 |
| 高 | VAD 校准 / 音频诊断 | 直接解决用户配置痛点 |
| 高 | 架构服务化第一阶段 | 为后续功能降低维护成本 |
| 中高 | 模式向导 | 降低新用户上手成本 |
| 中高 | Overlay MVP | VR 内字幕价值高，但实现风险稍高 |
| 中 | 设置页重组 | 功能复杂后必须做，但要谨慎迁移 |
| 中 | Process Loopback | 价值高，但 Windows 音频实现复杂 |
| 中低 | 更新体系增强 | 已有基础，可逐步完善 |
| 长期 | 语音控制 | VR 体验增强，但需控制误触发 |
| 长期 | OCR / 拍照翻译 | 差异化功能，适合后续扩展 |

---

## 10. 建议下一步

建议下一步继续推进 **P2 后半段**，不要回头重复 P0 / P1：

1. 抽出 `MicPipeline`，先迁移麦克风 final ASR → 翻译 → 输出分发路径。
2. 抽出 `ListenPipeline`，迁移桌面听译 ASR → 翻译 → 浮窗 / Chatbox 输出路径。
3. 将 `OutputDispatcher` 扩展为多 sink 分发：主界面、浮窗、Chatbox、TTS、未来 Overlay。
4. 新增 `OverlayService` 抽象，先接桌面/浮窗式 Overlay MVP，再考虑独立 OpenVR helper。
5. 完成 Process Loopback 前置准备：`DesktopCaptureService`、运行态 snapshot、目标进程选择 / 检测状态 UI。

这样可以沿着已完成的 `ManualTranslationController` / `OutputDispatcher` 服务化试点继续瘦身 `MainWindow`，同时为真正的 VR Overlay 和 Process Loopback 降低后续接入风险。
