# VoiceMeeter Banana 快速配置指南

## 目标
让 VRChat 中的其他玩家能同时听到：
- ✅ 你的真实声音（麦克风）
- ✅ AI 翻译语音（TTS）

## 配置步骤

### 1️⃣ VoiceMeeter Banana 基础配置

#### 输入配置（左侧）

**Stereo Input 1**（第一个输入通道）：
```
1. 点击 "Select Input Device"
2. 选择你的真实麦克风（例如：Realtek Audio、USB 麦克风）
3. 确保 A1 按钮是绿色亮起 ✅
4. 音量推子拉到 0dB 左右
```

**Stereo Input 2 和 3**：
- 不需要配置，保持默认即可

**Virtual Inputs**（右侧虚拟输入）：
- **Voicemeeter Input**：应用的 TTS 会自动输出到这里
- **Voicemeeter AUX I**：暂时不用
- 确保这两个通道的 **A1 按钮也是绿色** ✅

#### 输出配置（右上角）

**HARDWARE OUT - A1**：
```
1. 点击 A1 下方的设备名称
2. 选择你的真实扬声器/耳机（例如：Realtek Audio、USB 耳机）
3. 这样你才能听到混音后的声音
```

**A2, A3, B1, B2**：
- 不需要配置

### 2️⃣ Windows 声音设置

#### 播放设备（第一张截图）

**需要启用的设备**：
- ✅ **Voicemeeter Input (VB-Audio Voicemeeter VAIO)** - 应用 TTS 输出到这里
- ✅ 你的真实扬声器/耳机

**可以禁用的设备**（右键 → 禁用）：
- ❌ Voicemeeter Out A2, A3, A4, A5
- ❌ Voicemeeter Out B1, B2
- ❌ Voicemeeter AUX Input（如果不用）

#### 录制设备（第二张截图）

**需要启用的设备**：
- ✅ **Voicemeeter Output (VB-Audio Voicemeeter VAIO)** - VRChat 麦克风用这个
- ✅ 你的真实麦克风

**可以禁用的设备**（右键 → 禁用）：
- ❌ Voicemeeter In 2, 3, 4, 5
- ❌ Voicemeeter AUX Input

### 3️⃣ 应用配置

**在翻译应用中**：
```
1. 打开设置窗口（点击托盘图标 → 设置）
2. 找到 TTS 设置区域
3. 开启"输出到 VRChat"开关 ✅
4. 点击"测试 TTS"验证功能
```

**语音识别设置**：
- 麦克风设备：选择你的**真实麦克风**（不要选 VoiceMeeter）
- 这样不会影响正常的语音转文字功能

### 4️⃣ VRChat 配置

```
1. 打开 VRChat 设置
2. Audio 选项卡
3. Microphone：选择 "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)"
4. 保存设置
```

## 音频流向图

```
┌─────────────┐
│ 你的真实麦克风 │
└──────┬──────┘
       │
       ↓
┌─────────────────────┐
│ VoiceMeeter Input 1 │
└──────┬──────────────┘
       │
       ├─→ A1 输出 ─→ 你的耳机（监听自己）
       │
       ↓
┌──────────────────────┐
│ VoiceMeeter Output   │ ←─── VRChat 从这里读取
│ (虚拟麦克风)          │
└──────┬───────────────┘
       ↑
       │
┌──────────────────────┐
│ 应用 TTS 输出         │
│ → Voicemeeter Input  │
│   (虚拟输入)          │
└──────────────────────┘
```

## 测试步骤

### ✅ 测试 1：麦克风输入
1. 在 VoiceMeeter 中对着麦克风说话
2. 观察 **Stereo Input 1** 的音量条是否跳动（绿色）
3. 你应该能从耳机听到自己的声音

### ✅ 测试 2：TTS 输出
1. 在应用设置中点击"测试 TTS"
2. 观察 VoiceMeeter 中 **Voicemeeter Input** 的音量条是否跳动
3. 你应该能听到测试语音

### ✅ 测试 3：VRChat 集成
1. 进入 VRChat
2. 对着麦克风说话，确认其他玩家能听到
3. 触发 AI 翻译，确认其他玩家能听到 TTS 语音

## 常见问题

### ❓ 听不到自己的声音
**原因**：A1 输出未配置或未启用
**解决**：
1. 检查 Input 1 的 A1 按钮是否绿色
2. 检查右上角 A1 输出设备是否选择了你的耳机

### ❓ VRChat 听不到 TTS
**原因**：虚拟输入未路由到 A1 输出
**解决**：
1. 检查 **Voicemeeter Input** 通道的 A1 按钮是否绿色
2. 在应用中重新测试 TTS

### ❓ VRChat 听不到我的麦克风
**原因**：VRChat 麦克风设备选择错误
**解决**：
1. VRChat 设置中选择 "VoiceMeeter Output"
2. 不要选择你的真实麦克风

### ❓ 应用检测不到虚拟设备
**原因**：VoiceMeeter 未正确安装或未启动
**解决**：
1. 确认 VoiceMeeter Banana 正在运行
2. 重启应用
3. 在 Windows 声音设置中确认虚拟设备存在

### ❓ 声音有延迟
**原因**：音频缓冲区设置
**解决**：
1. 在 VoiceMeeter 菜单中选择 Menu → System Settings
2. 调整 Buffer Size（推荐 512 或 1024）
3. 重启 VoiceMeeter

## 设备清理建议

为了简化 Windows 声音设置，可以禁用不需要的虚拟设备：

### 播放设备（可禁用）
- Voicemeeter Out A2, A3, A4, A5
- Voicemeeter Out B1, B2

### 录制设备（可禁用）
- Voicemeeter In 2, 3, 4, 5

**禁用方法**：
1. 右键点击设备
2. 选择"禁用"
3. 不会影响功能，只是让列表更清爽

## 推荐设置

### VoiceMeeter 音量设置
- **Input 1（麦克风）**：0dB（根据实际情况调整）
- **Voicemeeter Input（TTS）**：0dB
- **A1 Master**：0dB

### 应用设置
- **TTS 音量**：在应用设置中调整（0.0-1.0）
- **输出到 VRChat**：开启 ✅

### VRChat 设置
- **Microphone**：VoiceMeeter Output
- **Voice Activation**：根据个人喜好
- **Mic Gain**：根据实际情况调整

## 总结

只需要记住三个关键点：
1. **真实麦克风** → VoiceMeeter Input 1 → A1 输出
2. **应用 TTS** → Voicemeeter Input（虚拟）→ A1 输出
3. **VRChat 麦克风** → 选择 VoiceMeeter Output

所有声音都通过 A1 输出混合，最终传递给 VRChat！
