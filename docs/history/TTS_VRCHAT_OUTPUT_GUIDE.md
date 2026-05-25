# TTS 输出到 VRChat 功能指南

## 功能概述

此功能允许将 AI 语音（TTS）输出到虚拟音频设备，使 VRChat 中的其他玩家能够听到 AI 朗读的内容。

## 实现细节

### 核心修改

1. **TTSManager (src/tts/manager.py)**
   - 添加 `output_device` 参数支持自定义输出设备
   - 修改 `_play_audio()` 方法使用非阻塞回调模式（兼容 Windows WDM-KS 驱动）
   - 添加 `list_output_devices()` 函数列出所有可用音频输出设备
   - 添加 `find_virtual_audio_device()` 函数自动检测虚拟音频设备

2. **设置窗口 (src/ui/settings_window.py)**
   - 添加"输出到 VRChat"开关控件
   - 实现虚拟设备自动检测和警告提示
   - 添加 VoiceMeeter Banana 安装指南对话框
   - 提供直接下载链接

3. **配置文件 (config.example.json)**
   - 在 `tts` 配置中添加 `output_device` 字段（默认 null）
   - 添加 `output_to_vrchat` 布尔字段控制功能开关

### 技术要点

- **音频播放模式**: 从阻塞式 `sd.OutputStream.write()` 改为回调模式，解决 "Blocking API not supported yet" 错误
- **设备检测**: 自动搜索包含 "vb-cable", "voicemeeter", "virtual", "loopback", "vaio" 等关键词的设备
- **编码问题**: 使用字符串拼接而非单行长字符串，避免 Windows GBK 编码问题

## 使用方法

### 前置条件

需要安装虚拟音频混音器。推荐使用 **VoiceMeeter Banana**（免费）:
- 自带虚拟音频设备（VAIO、VAIO3、VoiceMeeter Output）
- 可混合真实麦克风和 AI 语音
- 让你在说话的同时播放 AI 朗读

下载地址: https://download.vb-audio.com/Download_CABLE/VoicemeeterProSetup.exe

### 配置步骤

1. **安装 VoiceMeeter Banana**
   - 下载并安装 VoiceMeeter Banana
   - 重启应用以自动检测虚拟设备

2. **配置 VoiceMeeter**
   - 打开 VoiceMeeter Banana
   - 在 Hardware Input 1 中选择你的真实麦克风
   - 确保 A1 输出已启用

3. **配置 VRChat**
   - 在 VRChat 设置中
   - 将麦克风设备选择为 "VoiceMeeter Output"

4. **配置本应用**
   - 打开设置窗口
   - 在 TTS 设置中开启"输出到 VRChat"开关
   - 点击"测试 TTS"验证功能

### 音频流向

```
真实麦克风 ──→ VoiceMeeter Input 1
                    ↓
TTS 输出 ────→ VoiceMeeter VAIO (虚拟输入)
                    ↓
              VoiceMeeter 混音
                    ↓
           VoiceMeeter Output ──→ VRChat
```

## 故障排除

### 问题: 未检测到虚拟音频设备

**解决方案**:
1. 确认已安装 VoiceMeeter Banana
2. 重启应用
3. 在设置窗口中点击"安装虚拟音频设备"查看安装指南

### 问题: VRChat 听不到 TTS 声音

**检查清单**:
1. VRChat 麦克风设置是否选择了 "VoiceMeeter Output"
2. VoiceMeeter 中 A1 输出是否已启用
3. 本应用中"输出到 VRChat"开关是否已开启
4. 使用"测试 TTS"功能验证音频输出

### 问题: 自己的声音也听不到了

**解决方案**:
- 在 VoiceMeeter 中确认 Hardware Input 1 已选择你的麦克风
- 确认 A1 输出已启用

## 设计原则

- **独立性**: TTS 输出设备与麦克风输入设备完全独立
- **简洁性**: 自动检测虚拟设备，无需用户手动选择复杂的设备列表
- **兼容性**: 支持 VB-Cable、VoiceMeeter 等常见虚拟音频设备
- **用户友好**: 提供安装指南和直接下载链接

## 相关文件

- `src/tts/manager.py` - TTS 管理器核心实现
- `src/ui/settings_window.py` - 设置界面和用户交互
- `src/ui/main_window.py` - 主窗口 TTS 初始化
- `config.example.json` - 配置文件示例

## 已知限制

1. 仅支持 Windows 平台（依赖 sounddevice 库）
2. 需要用户手动安装虚拟音频设备
3. 虚拟设备检测基于关键词匹配，可能无法识别所有虚拟设备

## 未来改进

- [ ] 支持更多虚拟音频设备的自动检测
- [ ] 添加音频混音音量控制
- [ ] 提供设备选择的高级选项（可选）
- [ ] 添加音频输出测试工具
