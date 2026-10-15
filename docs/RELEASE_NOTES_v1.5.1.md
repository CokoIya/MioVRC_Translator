# Mio RealTime Translator v1.5.1

发布日期：2026-10-15。

## 本次包含的现有修复

- SteamVR 退出时统一清理头显字幕及相关覆盖层，避免运行时关闭后继续访问已释放的接口。
- 改善 SteamVR 未运行时的状态识别，减少重复提示与日志。
- 修正 Style-Bert-VITS2 的 JP-Extra 模型判断，以及多语言模型的中文、日文和英文文本语言选择。
- 保留完整 Mio VR手势、取景翻译、手腕书签、字幕及原有翻译、ASR、TTS 服务。

本次打包使用已有工作区修复，不另外修改功能逻辑；旧版 dist 产物保留。

## 安装与源码

使用 MioTranslator-Setup-v1.5.1.exe 安装或升级，安装前请退出 Mio。VR 功能需要 SteamVR 和正常追踪的头显及手柄。

官方安装包包含完整 VR 功能。公开仓库及 GitHub 自动生成的 Source code 压缩包提供可独立运行的非 VR 桌面版；VR 实现继续在私有仓库维护，既有公开历史不改写。

本次安装包提供 Ed25519 更新清单与校验和签名。Windows Authenticode 签名状态以最终构建报告为准。
