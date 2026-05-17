# TTS语音阅读功能 - VRChat配置指南

## 功能说明

TTS（文字转语音）功能可以将你在文字输入窗口中输入的文字，通过AI声线朗读出来，并让VRChat中的其他玩家听到。

## 核心问题解决

### 问题：为什么其他玩家听不到我的TTS语音？

**原因**：TTS音频默认输出到你的扬声器/耳机，VRChat无法捕获这个音频。需要通过虚拟音频设备将TTS音频路由到VRChat的麦克风输入。

## 配置步骤

### 第一步：安装虚拟音频设备

推荐使用 **Voicemeeter Banana**（免费）：

1. 下载地址：https://vb-audio.com/Voicemeeter/banana.htm
2. 安装后重启电脑
3. 安装完成后，系统会新增以下虚拟音频设备：
   - **VoiceMeeter Input** (输出设备) - 用于TTS输出
   - **VoiceMeeter Output** (输入设备) - 用于VRChat麦克风输入

### 第二步：配置MioTranslator的TTS设置

1. 打开MioTranslator主窗口
2. 点击右上角的"设置"按钮
3. 在设置窗口中找到"TTS语音阅读"部分
4. 配置以下选项：
   - ✅ **启用TTS** - 打开开关
   - ✅ **自动朗读** - 打开开关（翻译后自动朗读）
   - ✅ **输出到VRChat** - 打开开关（关键！）
   - **输出设备** - 应该自动检测到 "VoiceMeeter Input"
   - **语音引擎** - 推荐使用 "Edge TTS"（免费，音质好）
   - **声线选择** - 根据需要选择AI声线（如：zh-CN-XiaoxiaoNeural）
   - **语速** - 调整到合适的速度（默认1.0）
   - **音量** - 调整到合适的音量（默认0.8）

5. 点击"测试"按钮，确认能听到TTS语音
6. 点击"保存"按钮

### 第三步：配置VRChat麦克风输入

1. 打开VRChat
2. 进入设置 → 音频设置
3. 将**麦克风输入设备**设置为：**VoiceMeeter Output**
4. 调整麦克风音量到合适的级别

### 第四步：配置Voicemeeter混音器

1. 打开Voicemeeter Banana应用
2. 在右侧"HARDWARE INPUT"部分：
   - 选择你的真实麦克风作为输入1
3. 在左侧"HARDWARE OUT"部分：
   - A1：选择你的真实扬声器/耳机
4. 调整音量平衡：
   - 确保"VoiceMeeter Input"通道的音量适中
   - 可以通过推子调整TTS和真实麦克风的音量比例

## 使用方法

### 方法1：通过文字输入窗口

1. 按下快捷键 `Ctrl+Alt+X` 打开文字输入窗口（可在设置中修改）
2. 输入你想说的文字
3. 点击"翻译并发送到VRC"按钮（或按 `Ctrl+Enter`）
4. 系统会：
   - 翻译你的文字（如果需要）
   - 通过TTS朗读译文（或原文）
   - 将文字发送到VRChat聊天框
   - VRChat中的其他玩家可以听到TTS语音

### 方法2：通过主窗口手动翻译

1. 在主窗口的"源文本"框中输入文字
2. 点击"翻译"按钮
3. 系统会自动朗读翻译结果
4. 点击"发送到VRC"按钮将文字发送到聊天框

## 输出格式选项

在主窗口可以选择发送到VRChat的文字格式：

- **译文(原文)** - 发送翻译后的文字，原文在括号中
- **原文(译文)** - 发送原文，翻译在括号中
- **仅译文** - 只发送翻译
- **仅原文** - 只发送原文（不翻译）

TTS会根据你选择的格式朗读相应的文字。

## 常见问题

### Q1: 其他玩家听不到我的TTS语音？

**检查清单**：
1. ✅ 确认已安装Voicemeeter并重启电脑
2. ✅ 确认MioTranslator设置中"输出到VRChat"已打开
3. ✅ 确认VRChat麦克风设置为"VoiceMeeter Output"
4. ✅ 确认Voicemeeter应用正在运行
5. ✅ 在MioTranslator中点击"测试"按钮，确认能听到TTS
6. ✅ 在VRChat中测试麦克风，确认其他玩家能听到你的声音

### Q2: TTS音量太小或太大？

**解决方法**：
1. 在MioTranslator设置中调整"音量"滑块
2. 在Voicemeeter中调整"VoiceMeeter Input"通道的推子
3. 在VRChat中调整麦克风增益

### Q3: TTS和真实麦克风同时输出，声音混乱？

**解决方法**：
- 在Voicemeeter中调整各通道的音量平衡
- 说话时暂停TTS，或者调低TTS音量
- 使用VRChat的"按键说话"模式，避免TTS和麦克风同时激活

### Q4: 没有检测到虚拟音频设备？

**解决方法**：
1. 确认Voicemeeter已正确安装
2. 重启电脑
3. 在Windows声音设置中确认能看到"VoiceMeeter Input"和"VoiceMeeter Output"
4. 重启MioTranslator应用

### Q5: TTS功能不工作？

**检查日志**：
1. 打开MioTranslator的日志文件（在应用目录的logs文件夹）
2. 查找包含"TTS"的日志行
3. 常见错误信息：
   - "TTS engine unavailable" - TTS引擎初始化失败
   - "No virtual audio device found" - 未检测到虚拟音频设备
   - "TTS playback failed" - 音频播放失败

## 技术原理

```
文字输入 → 翻译 → TTS合成 → VoiceMeeter Input → VoiceMeeter混音器 → VoiceMeeter Output → VRChat麦克风 → 其他玩家
```

## 推荐配置

- **TTS引擎**：Edge TTS（免费，音质好，支持多种语言）
- **中文声线**：zh-CN-XiaoxiaoNeural（女声）、zh-CN-YunxiNeural（男声）
- **日文声线**：ja-JP-NanamiNeural（女声）、ja-JP-KeitaNeural（男声）
- **英文声线**：en-US-JennyNeural（女声）、en-US-GuyNeural（男声）
- **语速**：1.0（正常速度）
- **音量**：0.8（80%音量）

## 更多帮助

如果遇到问题，请：
1. 查看应用日志文件
2. 访问项目GitHub页面提交Issue
3. 加入QQ群或LINE群寻求帮助

---

**注意**：首次使用TTS功能时，Edge TTS引擎需要联网下载语音数据，请确保网络连接正常。
