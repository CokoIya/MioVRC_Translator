# MixLine 虚拟麦克风配置指南

本项目的同声传译模式只使用 MixLine 作为虚拟麦克风代理。目标链路是：

```text
真实麦克风 + Mio 同传语音 -> MixLine 混音 -> MixLine 虚拟麦克风 -> VRChat
```

## 1. 安装 MixLine

前往 Logitech G 官方页面下载安装：

https://www.logitechg.com/en-us/software/mixline.html

安装完成后启动 MixLine，并确认 Windows 声音设备列表里能看到 MixLine 暴露的虚拟输入/输出设备。

## 2. 配置 Mio

1. 打开 Mio RealTime Translator。
2. 在主界面切换到“同传”模式。
3. 打开“设置 -> 同声传译”。
4. 启用“输出到 VRChat”。
5. 如果已安装 MixLine，程序会自动选择 MixLine 设备；没有检测到时，请先启动 MixLine 再打开设置。

## 3. 配置 VRChat

1. 打开 VRChat 设置。
2. 将 Microphone 选择为 MixLine 暴露的虚拟麦克风。
3. 回到 Mio，使用同声传译测试朗读或正常说话。
4. 如果 VRChat 麦克风音量条跳动，说明链路已连通。

## 4. 当前策略

同声传译模式只检测和绑定 MixLine 设备。没有检测到 MixLine 时，程序会提示安装或启动 MixLine。

## 常见问题

### 程序没有检测到 MixLine

- 确认 MixLine 已启动。
- 关闭并重新打开 Mio 的设置窗口。
- 在 Windows 声音设置中确认 MixLine 设备可见。
- 必要时重启 VRChat 和 Mio。

### VRChat 听不到同传语音

- 确认 Mio 设置里已打开“输出到 VRChat”。
- 确认 VRChat 的 Microphone 选择的是 MixLine 虚拟麦克风。
- 确认 MixLine 中 Mio/TTS 输出和真实麦克风被混到同一路虚拟麦克风。

### 自己的麦克风或 AI 声音太大

在 MixLine 里分别调低真实麦克风通道和 Mio/TTS 通道的音量，再观察 VRChat 麦克风音量条。
