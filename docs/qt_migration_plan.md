# Mio RealTime Translator UI 技术栈迁移计划

## 1. 迁移目标

将当前正式版 `Tk / CustomTkinter` UI 迁移到 `PySide6 / Qt`，迁移目标是**完整复刻当前正式版 UI**，不是重做或简化 UI。

具体要求：

1. **完整复刻当前正式版 UI** — 布局结构、视觉风格、控件位置、窗口尺寸、间距、颜色、字体层级、交互流程全部一致。
2. **保持现有功能行为** — 语音识别、翻译、TTS、OSC、热键、桌面音频监听、悬浮窗、模型下载、更新检查、赞助窗口等功能不因 UI 迁移而失效。
3. **并行迁移，逐步验证** — 保留当前 `src/ui/` Tk 正式 UI，新建或继续完善 `src/ui_qt/` Qt UI。默认启动仍使用 Tk UI，直到 Qt UI 经用户确认后再切换。
4. **禁止回滚项目** — 不执行任何项目级回滚，不恢复旧版本覆盖当前文件，所有修复都通过正向补丁完成。

---

## 2. 硬性约束

### 2.1 禁止回滚

迁移过程中**绝对禁止**执行以下任何操作：

- `git reset --hard` / `git reset`
- `git checkout -- <file>`
- `git restore <file>`
- `git clean -fd`
- 删除 `src/ui/` 任何文件
- 用旧版本覆盖当前文件
- 批量还原整个目录

如果某个修改出错，只允许：阅读当前文件 → 定位错误 → 写正向修复 patch → 运行测试 → 用户确认。

### 2.2 UI 保真约束

当前 `src/ui/` 是正式版 UI 的唯一来源。迁移时必须遵守：

- **不重新设计 UI**，不简化，不删功能，不改布局意图。
- **不替换成 skeleton / demo UI**。
- 正确做法：`CTkFrame → QFrame`，`CTkButton → QPushButton`，`Tk after → QTimer`，`Tk Toplevel → QDialog/QWidget`。

### 2.3 数据路径约束

必须保留项目已有路径策略：

> 所有应用生成的数据、缓存、临时文件、背景图片必须写入安装/项目目录。默认不使用 AppData、system temp 或用户目录。如需覆盖默认路径，只能通过 `MIO_TRANSLATOR_HOME` 环境变量显式指定。

Qt 迁移**禁止**引入：`QStandardPaths` 默认 AppData 路径、系统临时目录作为默认缓存。

### 2.4 默认启动约束

迁移期间默认启动为 Tk 正式 UI。`MIO_TRANSLATOR_UI` 环境变量控制后端：

| 值 | 行为 |
|---|---|
| `tk`（默认） | 当前正式 Tk UI |
| `qt` | Qt 迁移版 UI（开发验证用） |

---

## 3. 目标文件结构

```
src/
  ui/                                  # 当前正式 Tk UI，迁移完成前保留
    main_window.py
    settings_window.py
    floating_window.py
    text_input_window.py
    sponsor_window.py
    update_window.py
    model_download_dialog.py

  ui_qt/                               # Qt 迁移版 UI
    app.py                             # Qt 启动入口
    main_window.py                     # Qt 主窗口，完整复刻 Tk 版
    settings_window.py                  # Qt 设置窗口
    floating_window.py                 # Qt 悬浮窗
    text_input_window.py               # Qt 手动输入窗口
    sponsor_window.py                  # Qt 赞助窗口
    update_window.py                   # Qt 更新窗口
    model_download_dialog.py           # Qt 模型下载窗口
    styles.qss                         # 统一样式表（如需要）

  core/                                # 可选，无 UI 依赖的服务层
    runtime_controller.py
    config_service.py
    translation_pipeline.py
    speech_pipeline.py
    tts_service.py
    osc_service.py
    hotkey_service.py
```

**约束**：`src/ui/` 不删除；`src/ui_qt/` 不得简化正式 UI；`core/` 不得反向依赖 Qt UI。

---

## 4. 分阶段迁移计划

### Phase 0：冻结迁移基准

**目标**：确认当前项目状态为正式 UI 基准。

**操作**：
1. 记录正式 UI 文件清单（`src/ui/` 下所有 .py 文件）。
2. 只读检查各文件窗口结构、控件、回调。
3. 建立 UI 对照清单：

| 区块 | 对应 Tk 文件 | 对应 Qt 文件 |
|---|---|---|
| 主窗口 header | main_window.py `_build()` | main_window.py |
| 主窗口翻译面板 | main_window.py `_build_translate_panel()` | main_window.py |
| 主窗口右侧快捷控制 | main_window.py `_build()` | main_window.py |
| 主窗口 footer | main_window.py `_build()` | main_window.py |
| 设置窗口 | settings_window.py | settings_window.py |
| 悬浮窗 | floating_window.py | floating_window.py |
| 手动输入窗口 | text_input_window.py | text_input_window.py |
| 赞助窗口 | sponsor_window.py | sponsor_window.py |
| 更新窗口 | update_window.py | update_window.py |
| 模型下载 | model_download_dialog.py | model_download_dialog.py |

**验收标准**：不修改现有正式 UI，不回滚任何文件。

---

### Phase 1：Qt 启动壳与安全入口

**目标**：建立 Qt 应用入口，不影响默认 Tk UI。

**文件**：`main.py`、`src/ui_qt/app.py`

**设计**：
- `main.py` 保持 `MIO_TRANSLATOR_UI` 环境变量判断，默认走 Tk。
- `src/ui_qt/app.py` 负责创建 `QApplication`、设置应用名/图标、加载样式、创建 `MainWindow`、进入 `app.exec()`。
- `src/ui_qt/app.py` 中的 `run_qt_app()` 不得包含任何 Tk 导入。

**禁止事项**：不把默认入口改成 Qt；不删除 Tk 入口；不让 Qt 启动失败影响 Tk。

**验收标准**：默认启动仍是正式 Tk UI；`MIO_TRANSLATOR_UI=qt` 可单独启动 Qt 开发窗口。

---

### Phase 2：主窗口逐区块复刻

**目标**：完整迁移 `src/ui/main_window.py` 的 UI 和交互。

#### 2.1 基础结构映射

| Tk Widget | Qt Widget |
|---|---|
| `ctk.CTk` | `QMainWindow` |
| `ctk.CTkFrame` | `QFrame` / `QWidget` |
| `ctk.CTkLabel` | `QLabel` |
| `ctk.CTkButton` | `QPushButton` |
| `ctk.CTkOptionMenu` | `QComboBox` |
| `ctk.CTkTextbox` | `QTextEdit` / `QPlainTextEdit` |
| `ctk.CTkProgressBar` | `QProgressBar` |
| `ctk.CTkScrollableFrame` | `QScrollArea` + `QWidget` |

#### 2.2 Header 区域

迁移内容：应用图标、标题、副标题、状态显示、更新 badge、UI 语言选择、主题切换按钮、设置按钮、指南按钮。

**必须验证**：语言切换后文案刷新；主题按钮行为一致；更新 badge 显示逻辑；设置按钮打开设置窗口；指南按钮打开 OSC guide。

#### 2.3 翻译主面板

迁移内容：源/目标语言选择、语言互换按钮、原文文本区、译文文本区、字符计数、手动输入按钮、翻译按钮、清空按钮、复制原文、复制译文、发送到 VRChat。

**必须验证**：手动输入窗口可打开；翻译/清空/复制/发送行为一致；错误译文颜色一致；所有按钮连接到正确功能。

#### 2.4 右侧快捷控制区

迁移内容：开始/停止监听、翻译/同传模式切换、麦克风设备选择、麦克风静音、反向翻译/桌面音频监听、悬浮窗显示开关。

**必须验证**：开始监听按钮状态正确；模式切换保存配置；设备选择弹窗可用；桌面音频监听状态同步设置窗口；悬浮窗显示/隐藏一致。

#### 2.5 Footer 区域

迁移内容：底部状态文本、底部进度条、赞助按钮、GitHub / QQ / LINE 链接按钮。

**必须验证**：进度条显示/隐藏不造成布局跳动；外部链接正确打开；赞助窗口正确打开。

#### 2.6 主题切换

当前 Tk 版本已有完整主题系统（含暗色/亮色切换动画）。Qt 版本必须复刻：
- 主题色板切换逻辑。
- 主题配置持久化到 config。
- 主题切换动画（当前为截图展开动画）。
- 主题同步所有子窗口。

#### 2.7 状态刷新方法

| Tk 方法 | Qt 等效 |
|---|---|
| `self.after()` | `QTimer.singleShot()` / `QTimer` |
| `StringVar.trace_add()` | `signal / slot` 连接 |
| `widget.configure(text=...)` | `widget.setText()` / `setStyleSheet()` |
| `ctk.set_appearance_mode()` | 手动同步全局色板 |

**验收标准**：Qt 主窗口视觉上与 Tk 正式 UI 基本一致；所有按钮可点击且连接到正确功能；语言切换、主题切换不崩；设置/悬浮/手动输入窗口可打开；启动/停止监听不因 UI 迁移断开。

---

### Phase 3：设置窗口完整迁移

**目标**：完整迁移 `src/ui/settings_window.py`。

**窗口结构**：左侧导航 + 右侧页面 host + Header + 底部按钮。

**必须迁移的页面**：

| 页面 ID | 页面名称 | 关键控件 |
|---|---|---|
| common | 通用/翻译设置 | ASR Provider、Backend 切换、Model 选择、翻译锁定 |
| voice | 语音设置 | 麦克风、采样率、回声消除、降噪 |
| vrc_listen | VRC Listen 设置 | 启用开关、发送到聊天框、悬浮窗、桌面音频 |
| tts | TTS 设置 | 引擎选择、声音选择、语速、音量、输出目标、测试按钮 |
| roleplay | 角色扮演/社交 | BERT Prompt、赞助信息等 |

**Tk -> Qt 映射**：

| Tk 模式 | Qt 等效 |
|---|---|
| `CTkToplevel` | `QDialog` |
| `CTkScrollableFrame` | `QScrollArea` + `QWidget` |
| `CTkSwitch` | `QCheckBox` 或自定义 switch |
| `CTkSlider` | `QSlider` |
| `StringVar` + `trace_add` | widget state + `signal` |
| `after_idle` | `QTimer.singleShot(0, ...)` |
| section card 折叠动画 | 直接展开（Qt 不需要折叠） |

**关键行为必须保留**：
- ASR 引擎切换动态更新字段。
- 翻译后端切换动态更新 Model 字段。
- 模型信息卡片刷新（badge 复用逻辑）。
- VRC Listen 状态实时回调到主窗口。
- TTS 启用/禁用联动所有控件状态。
- BERT Prompt 保存。
- 设置保存到 config。

**验收标准**：设置窗口视觉结构与正式版一致；每个页面都存在且可交互；所有控件初始值来自 config；修改控件后 config 更新逻辑一致；与主窗口联动一致。

---

### Phase 4：辅助窗口迁移

按以下顺序逐个迁移。每个窗口必须验证可打开、控件可用、与主窗口数据交换正确。

| # | 窗口 | Tk 源文件 | Qt 目标文件 | 关键点 |
|---|---|---|---|---|
| 1 | 手动输入窗口 | text_input_window.py | text_input_window.py | Shift+Return 快捷键；主窗口回填 |
| 2 | 悬浮窗 | floating_window.py | floating_window.py | 历史气泡；重新发送；翻译显示 |
| 3 | 赞助窗口 | sponsor_window.py | sponsor_window.py | 赞助列表；QR 弹窗；主题色同步 |
| 4 | 更新窗口 | update_window.py | update_window.py | 下载进度；后台线程；Qt 信号桥 |
| 5 | 模型下载窗口 | model_download_dialog.py | model_download_dialog.py | 进度条；跨线程更新；setup 模式 |

**跨线程处理原则**：下载/网络操作在后台线程执行；通过 Qt 信号桥（`Signal`）将进度传回主线程；禁止直接在工作线程中调用 `widget.setValue()` 等非线程安全方法。

---

### Phase 5：集成与切换

**目标**：Qt UI 经用户确认后，将默认入口从 Tk 切换到 Qt。

**前置条件**：
1. Phase 2 主窗口全部区块通过验收。
2. Phase 3 设置窗口全部页面通过验收。
3. Phase 4 所有辅助窗口通过验收。
4. `MIO_TRANSLATOR_UI=qt` 启动的 Qt UI 通过完整功能测试。
5. 用户明确确认 Qt UI 等同于正式版。

**切换步骤**：
1. 修改 `main.py`，将 `MIO_TRANSLATOR_UI` 默认值从 `tk` 改为 `qt`。
2. 确保 `src/ui_qt/app.py` 中无残留 `RuntimeError`。
3. 验证启动入口。
4. 可选：删除 `src/ui/` 并清理 PyInstaller spec 中的 Tk 隐式导入（Phase 8）。

---

### Phase 6：清理与收尾

**操作**：
1. 删除 `src/ui/` 目录（确认 Qt UI 稳定运行后）。
2. 更新 `MioTranslator.spec`：
   - 移除 Tk/CustomTkinter 相关隐藏导入。
   - 保留 PySide6 相关配置。
   - 保留 `src/ui_qt.*` 隐藏导入。
3. 更新 `requirements.txt`：移除 `customtkinter`（确认不需要后）。
4. 更新测试套件：
   - 将 Tk UI 测试文件重命名或改为 Qt UI 测试。
   - 确认 `pytest-qt` 测试全部通过。
5. 运行完整测试套件，确认无回归。

---

## 5. Tk -> Qt 关键差异备忘

### 布局

- Tk `grid` / `pack` → Qt `QGridLayout` / `QVBoxLayout` / `QHBoxLayout`
- Tk `pack(fill="both", expand=True)` → Qt `QLayout.setSizeConstraint(QLayout.SetNoConstraint)` + widget size policy

### 事件

| Tk | Qt |
|---|---|
| `widget.bind("<Button-1>", handler)` | `widget.mousePressEvent` 或 `installEventFilter` |
| `StringVar.trace_add("write", callback)` | `QLineEdit.textChanged.connect(slot)` |
| `self.after(ms, func)` | `QTimer.singleShot(ms, func)` |
| `self.after_idle(func)` | `QTimer.singleShot(0, func)` |

### 样式

- Tk `widget.configure(fg_color="...")` → Qt `widget.setStyleSheet()` 或 `QPalette`
- CTk 暗色主题色板 → 手动映射到 Qt 样式表
- `ctk.CTkFont(size=12, weight="bold")` → `QFont(family, size, weight)`

### 窗口

| Tk | Qt |
|---|---|
| `ctk.CTkToplevel(parent)` | `QDialog(parent)` 或 `QWidget(parent)` |
| `Toplevel` geometry | `setGeometry()` 或 `setFixedSize()` |
| `transient(parent)` | `setWindowModality(Qt.WindowModal)` |
| `protocol("WM_DELETE_WINDOW", ...)` | `setAttribute(Qt.WA_DeleteOnClose)` + `closeEvent` |

### 图标

- `tkinter.PhotoImage` → `QIcon` / `QPixmap`
- 图标路径查找 → 复用 `app_paths.resource_base_dirs()`

### 线程安全

- Qt UI 只在主线程更新。
- 后台线程通过 `Qt.Signals` + `QObject` 子类跨线程通知。
- 禁止在工作线程直接调用 `widget.update()` 等 UI 方法。

---

## 6. 验收检查清单

### 通用

- [ ] 默认启动为 Tk UI（`MIO_TRANSLATOR_UI` 默认 `tk`）
- [ ] `MIO_TRANSLATOR_UI=qt` 可启动 Qt UI
- [ ] `python -m py_compile` 所有 `src/ui_qt/*.py` 无语法错误
- [ ] 完整测试套件通过（不含 Tk 隐式依赖的测试）
- [ ] PyInstaller spec 包含 PySide6 插件和 Qt 隐藏导入

### 主窗口

- [ ] 窗口尺寸、背景色与 Tk 版一致
- [ ] Header 所有控件存在且文案正确
- [ ] 翻译面板所有按钮存在且功能正确
- [ ] 右侧快捷控制区所有控件存在且功能正确
- [ ] Footer 所有链接按钮存在且正确打开外部 URL
- [ ] 语言切换刷新所有控件文案
- [ ] 主题切换更新所有颜色且持久化到 config
- [ ] 设置窗口可打开
- [ ] 悬浮窗可打开
- [ ] 手动输入窗口可打开

### 设置窗口

- [ ] 左侧导航所有页面存在
- [ ] 各页面控件与 Tk 版结构一致
- [ ] 控件初始值来自 config
- [ ] 修改控件后 config 正确保存
- [ ] VRC Listen 状态实时同步主窗口
- [ ] TTS 测试按钮可执行

### 辅助窗口

- [ ] 手动输入窗口 Shift+Return 快捷键正确
- [ ] 悬浮窗历史气泡正确显示
- [ ] 赞助窗口列表和 QR 正确显示
- [ ] 更新窗口进度条正确更新
- [ ] 模型下载进度正确更新

---

## 7. 当前已知存量资产

以下文件已存在于 `src/ui_qt/`，是之前迁移周期的中间产物，**不得作为正式 UI 蓝本**，需要对照 Tk 正式版重新审查和替换：

```
src/ui_qt/app.py              # 已有启动壳（需移除 RuntimeError）
src/ui_qt/main_window.py      # 简化版，不可用，需重新按 Phase 2 迁移
src/ui_qt/settings_window.py  # 简化版，不可用，需重新按 Phase 3 迁移
src/ui_qt/text_input_window.py    # 简化版，需重新按 Phase 4 迁移
src/ui_qt/floating_window.py      # 简化版，需重新按 Phase 4 迁移
src/ui_qt/sponsor_window.py        # 简化版，需重新按 Phase 4 迁移
src/ui_qt/update_window.py        # 已有正确逻辑，需审查样式
src/ui_qt/model_download_dialog.py # 已有正确跨线程桥，需审查样式
```

以下 `core/` 文件是之前迁移周期的中间产物，可按需复用或重写：

```
src/core/config_service.py
src/core/runtime_controller.py
src/core/osc_service.py
src/core/hotkey_service.py
src/core/translation_pipeline.py
src/core/speech_pipeline.py
src/core/tts_service.py
```

**原则**：`core/` 服务层不得反向依赖 `src/ui_qt/`。如果某 service 需要 Qt 信号，优先改为普通回调接口。
