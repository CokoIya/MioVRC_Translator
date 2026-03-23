# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> 面向 VRChat 用户的本地实时语音翻译工具  
> 作者：`ここ_Mio` / 开源项目 / 禁止收费分发  
> 官网：`https://78hejiu.top`

## 简介

**Mio RealTime Translator** 是一款面向 VRChat 的桌面实时翻译工具，重点解决两类场景：

- 自己说的话，快速翻译后发到 `VRChat Chatbox`
- 听到别人的语音后，快速做反向翻译，方便理解和确认内容

当前主要链路：

- 麦克风翻译：`麦克风 -> ASR -> 翻译 -> VRChat Chatbox`
- 反向翻译：`VRChat 音频 -> ASR -> 翻译 -> 聊天框 / 悬浮显示`

## 下载

- 官方下载站：`https://78hejiu.top`
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