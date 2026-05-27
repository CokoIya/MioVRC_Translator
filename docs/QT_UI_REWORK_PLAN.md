# Qt UI 重做计划

## 背景

当前 Qt UI 是从 Tk UI 迁移过来的，但不是一次完整的界面重构，结果变成了多个局部模块拼接：

- 主界面右侧快捷控制区和左侧翻译区视觉割裂，像独立外挂区域。
- 主题切换时部分区域不同步，尤其滚动区、viewport、设置页内容区会出现白块。
- 设置页打开慢，导航拆得太细，内容区域和外层主题不统一。
- OSC 引导被简化成纯文本弹窗，丢失了原本的引导体验。
- 主界面整体缺少设计层级，阴影、边框、圆角、间距不统一。

目标不是继续小修小补，而是在保持现有窗口尺寸和功能入口的前提下，重做 Qt UI 的视觉结构。

## 硬性限制

- 主窗口尺寸保持当前尺寸：`860 x 430`。
- 设置窗口尺寸保持当前尺寸：`860 x 430`。
- 不允许用户拖拽改变窗口尺寸。
- 不改业务逻辑，不重写翻译、ASR、TTS、OSC、配置保存逻辑。
- 不碰 Tk UI，所有改动集中在 `src/ui_qt`。
- 保留现有主要入口：
  - 开始监听
  - 翻译 / 同传模式切换
  - 麦克风设备选择
  - 静音
  - 反向翻译
  - 悬浮窗
  - 设置
  - OSC 引导
  - Sponsors / 社交链接

## 主界面重做目标

### 1. 固定窗口尺寸

主窗口使用固定尺寸：

```python
self.setFixedSize(860, 430)
```

不再保留最小尺寸和可拉伸布局，否则小控件会在不同尺寸下产生新的错位。

### 2. 统一整体结构

主界面分为三层：

```text
Window
└─ Shell
   ├─ Header
   ├─ Main Content
   │  ├─ Translation Workspace
   │  └─ Quick Controls
   └─ Footer
```

关键点：

- `Translation Workspace` 和 `Quick Controls` 必须属于同一个主内容容器。
- 右侧快捷控制区不能再是独立白底/独立阴影卡片。
- 右侧区域只用一条轻分隔线和左侧区分。
- 主内容容器自身负责边框和背景。

### 3. 重做视觉层级

统一使用一套 Qt palette：

- Window/Shell 背景
- Header/Footer 背景
- Main Content 背景
- Inner Panel 背景
- Field 背景
- Border
- Muted Text
- Accent

禁止出现：

- 设置页白色内容块残留在暗色主题中。
- 右侧快捷区单独使用浅色背景。
- 外围莫名深色粗边框。
- 多层卡片套卡片。
- 大阴影造成的拼接感。

### 4. 控件尺寸约束

主界面控件要适配 860 x 430，不追求响应式：

- Header 固定高度约 62px。
- Footer 固定高度约 46px。
- Quick Controls 固定宽度约 210-214px。
- 翻译区占剩余宽度。
- 输入/输出文本框固定视觉高度，不随文字或按钮挤压跳动。
- 所有按钮高度统一，模式切换按钮放在同一个 segmented control 中。

### 5. 主题切换

主题切换只做状态切换和样式重刷：

- 移除截图 reveal 动画。
- 移除会导致整窗闪烁的 `grab/processEvents/overlay` 流程。
- 切换后立即同步：
  - 主窗口
  - Header/Footer
  - 翻译区
  - 右侧快捷区
  - 文本框
  - ComboBox popup
  - ScrollArea viewport

## 设置页重做目标

### 1. 固定窗口尺寸

设置窗口同样固定：

```python
self.setFixedSize(860, 430)
```

### 2. 设置页结构

保留左侧导航，但只保留大类：

```text
General / Translation
Voice Settings
VRC Listen
TTS / Interpretation
Roleplay / Social
Avatar / OSC
```

不再把以下内容拆成独立导航项：

- Hotkeys
- Model
- Dictionary

这些内容并入对应页面：

- Text Input Hotkey -> General / Translation
- Mic Mute Hotkey -> Voice Settings
- Model status -> Voice Settings
- Dictionary -> Voice Settings

### 3. 设置页性能

设置页打开时只构建第一个页面。

其他页面点击时懒加载：

```text
打开设置窗 -> 只构建 General
点击 Voice -> 构建 Voice
点击 TTS -> 构建 TTS，并在此时加载 TTS voices
点击 Dictionary 所在区域 -> 此时刷新 dictionary status
```

避免打开设置页时一次性做：

- TTS engine 初始化
- voices 枚举
- 字典状态扫描
- 模型状态检查

### 4. 主题同步

设置页必须统一 QSS：

- `QDialog`
- `QScrollArea`
- `QAbstractScrollArea::viewport`
- 页面 content widget
- `QStackedWidget`
- `QComboBox QAbstractItemView`
- `QTextEdit`
- `QLineEdit`
- `QCheckBox`

暗色主题下不允许再出现白色页面背景。

## OSC 引导

恢复为三步引导，不再只是一个大文本框：

```text
1. 打开 Action Menu
   Action Menu > Options

2. 进入 OSC 菜单
   Options > OSC

3. 打开 Enable
   OSC > Enabled
```

要求：

- 首次启动 Qt UI 时自动显示一次。
- 自动显示不能阻塞主窗口事件循环。
- 点击 Header 的 OSC 按钮可再次打开。
- 引导窗口使用当前主题样式。

## 代码实施范围

主要文件：

- `src/ui_qt/main_window.py`
- `src/ui_qt/settings_window.py`
- `tests/test_qt_main_window.py`
- `tests/test_qt_settings_window.py`

可选文件：

- `src/ui_qt/styles.qss`

如果 `styles.qss` 与窗口内动态 QSS 冲突，应二选一：

- 要么删除无效全局 QSS；
- 要么把公共 Qt 样式抽到 `styles.qss`，窗口内只注入主题变量。

不要同时维护两套互相覆盖的 QSS。

## 分阶段执行

### 阶段 1：尺寸和主题基础

- 固定主窗口和设置窗口尺寸。
- 移除主题切换动画。
- 建立统一 palette。
- 修复 ScrollArea / viewport 白块。

验收：

- 暗色主题下主界面和设置页没有白色残留。
- 切换主题不闪屏。
- 窗口尺寸不能拖动。

### 阶段 2：主界面重排

- 重做 Shell/Header/Main/Footer 布局。
- 翻译区和快捷区放入同一主内容容器。
- 右侧快捷区改为同底色分栏。
- 统一按钮、输入框、面板圆角和间距。

验收：

- 右侧红框区域不再像独立外挂。
- 主界面没有多层卡片拼接感。
- 860 x 430 下所有文字和按钮不重叠。

### 阶段 3：设置页重排

- 导航缩回 6 个大类。
- 合并 Hotkey / Model / Dictionary。
- 页面懒加载。
- 设置页 QSS 统一。

验收：

- 设置页打开明显变快。
- 暗色主题设置页内容区不再白块。
- 导航项数量恢复合理。

### 阶段 4：OSC 引导恢复

- 实现三步引导。
- 首次启动自动显示一次。
- 手动 OSC 按钮可重复打开。
- 非阻塞显示。

验收：

- OSC 引导体验不被省略。
- 不影响主界面操作。

### 阶段 5：验证

运行：

```powershell
.\.venv\Scripts\python.exe -m py_compile src\ui_qt\main_window.py src\ui_qt\settings_window.py
.\.venv\Scripts\python.exe -m pytest tests\test_qt_main_window.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_qt_settings_window.py -q
```

手动验证：

```powershell
$env:MIO_TRANSLATOR_NO_VENV_RELAUNCH='1'
$env:MIO_TRANSLATOR_UI='qt'
.\.venv\Scripts\python.exe main.py
```

检查点：

- 主窗口尺寸固定。
- 设置窗口尺寸固定。
- 暗色/浅色主题切换正常。
- UI 语言切换后 Header、主区、右侧区、设置页同步。
- 设置页打开速度可接受。
- OSC 引导存在。
- 右侧快捷区和主界面视觉统一。

## 暂不处理

以下问题不放进本轮 UI 重做，避免范围失控：

- Tk UI 的视觉调整。
- 翻译质量。
- ASR/TTS 后端行为。
- 乱码文案的全量修复。
- 安装包构建。
- 图标资源重制。

乱码文案问题应该单独做一轮，因为它涉及编码来源、i18n 表和迁移文件，不应该混在布局重做里。
