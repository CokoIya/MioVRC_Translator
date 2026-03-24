# Mio RealTime Translator | VRChat / VRC 实时翻译工具

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./docs/README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./docs/README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)
[![稳定版 / Beta 下载](https://img.shields.io/badge/稳定版%20%2F%20Beta%20下载-78hejiu.top-ff6b35?style=for-the-badge)](https://78hejiu.top)

## 官网下载

### [https://78hejiu.top/](https://78hejiu.top/)

> 面向 VRChat / VRC 用户的本地实时语音翻译工具、VRChat Chatbox 翻译助手  
> 作者：`ここ_Mio` / 开源项目 / 禁止收费分发  
> 下载官网：[https://78hejiu.top/](https://78hejiu.top/)

## 简介

**Mio RealTime Translator** 是一款面向 VRChat / VRC 的桌面实时翻译工具。玩家也常用 `VRC 翻译插件`、`VRChat 翻译插件`、`VRChat Chatbox 翻译`、`VRChat 语音翻译` 等关键词来搜索这类工具。

它重点解决两类场景：

- 自己说的话，快速翻译后发到 `VRChat Chatbox`
- 听到别人的语音后，快速做反向翻译，方便理解和确认内容

当前主要链路：

- 麦克风翻译：`麦克风 -> ASR -> 翻译 -> VRChat Chatbox`
- 反向翻译：`VRChat 音频 -> ASR -> 翻译 -> 聊天框 / 悬浮显示`

## 相关关键词

以下关键词用于帮助玩家在 GitHub 搜索中更容易找到本项目：

- 中文：VRC翻译插件、VRChat翻译插件、VRChat实时翻译、VRChat语音翻译、VRChat Chatbox翻译、VRChat聊天框翻译、VRChat反向翻译、VRChat字幕翻译、VRChat本地翻译工具、VRChat语音转文字翻译
- English: VRChat translator, VRC translator, VRChat real-time translator, VRChat voice translator, VRChat speech translation, VRChat Chatbox translator, VRChat subtitle overlay, VRChat local translation tool
- 日本語：VRChat 翻訳ツール、VRC 翻訳、VRChat リアルタイム翻訳、VRChat 音声翻訳、VRChat Chatbox 翻訳、VRChat 字幕表示、VRChat ローカル翻訳ツール
- 한국어: VRChat 번역기, VRC 번역기, VRChat 실시간 번역, VRChat 음성 번역, VRChat 채팅박스 번역, VRChat 자막 번역

## 下载

- 官方下载站：[https://78hejiu.top/](https://78hejiu.top/)
- 稳定版、beta 测试版，以及未来所有更新，统一在官网提供下载
- 本仓库不再提供 GitHub Releases 特供版，也不再同步二进制安装包
- GitHub 仓库主要用于源代码、问题反馈和开发记录

## 功能亮点

- 本地语音识别与实时翻译
- 翻译结果可发送到 `VRChat Chatbox`
- 支持反向翻译，监听 VRChat 播放音频并翻译
- 内置手动文本翻译面板
- 支持多种聊天框输出格式
- 支持多语言界面
- 支持 ASR 词典、自声抑制、降噪、VAD 与识别参数调节
- 支持 `Avatar / OSC` 参数同步
- 支持可选悬浮窗显示

## 运行环境

- 推荐系统：`Windows 10 / 11`
- 反向翻译依赖 `Windows WASAPI Loopback`
- 若本地没有模型，首次启动时会自动下载 `SenseVoice Small`

## 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

如需提前下载模型：

```bash
python download_models.py
```

如需自定义模型缓存目录：

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 首次使用

1. 将 `config.example.json` 复制为 `config.json`
2. 启动程序，打开 `设置`
3. 选择翻译服务并填写对应的 `API Key`
4. 根据需要设置目标语言、输出格式、麦克风和反向翻译选项
5. 在 VRChat 中启用 `OSC`
   `Action Menu -> Options -> OSC -> Enable`

## 隐私说明

- 项目本身不收集用户数据
- 不保存聊天记录
- chatbox 文本在发送后不会长期存储
- 只有在你启用云端翻译服务时，当前待翻译文本才会发送到你自己配置的 API 服务商

## 补充说明

- VRChat 原生 OSC 不提供其他玩家的原始聊天文本
- 反向翻译依赖本机播放设备回环采集，体验会受系统音频链路影响
- VRChat Chatbox 有发送频率和单条长度限制
