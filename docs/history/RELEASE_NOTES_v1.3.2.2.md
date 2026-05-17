# Mio Translator v1.3.2.2

## 📦 下载 / Downloads / ダウンロード

- **Full 完整包** (推荐新用户 / For new users / 新規ユーザー向け): [MioTranslator-Setup-v1.3.2.2-full.exe](https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.3.2.2/MioTranslator-Setup-v1.3.2.2-full.exe) (1.03 GB)
- **Lite 更新包** (推荐老用户 / For existing users / 既存ユーザー向け): [MioTranslator-Setup-v1.3.2.2-lite.exe](https://github.com/CokoIya/MioVRC_Translator/releases/download/v1.3.2.2/MioTranslator-Setup-v1.3.2.2-lite.exe) (249.6 MB)

---

## 🇨🇳 中文更新说明

v1.3.2.2 是体验修正版本，主要优化了用户界面响应和系统稳定性。

### ✨ 主要改进

1. **设置页面滚动优化**
   - 调快滚动速度到更接近常见桌面应用的手感
   - 提升设置页面的操作流畅度

2. **AI 服务商切换布局修复**
   - 修复切换 AI 服务商时 Model 区域被挤到下方的问题
   - 设置页会在内容变化后重新测量布局，确保显示正常

3. **启动加载反馈改进**
   - 开始监听后立即显示启动/加载进度
   - 减少进入加载条前的静默等待时间
   - 防止重复点击开始按钮导致的状态混乱

4. **性能优化**
   - 复用麦克风和 VRC listen 的 ASR 实例
   - 缓存模型完整性校验结果，降低重复初始化开销
   - 优化音频设备检测逻辑，减少不必要的流创建

5. **TTS 播放稳定性提升**
   - 改进 TTS 音频播放的线程安全性
   - 修复停止播放时可能出现的竞态条件
   - 优化播放队列管理，防止队列阻塞

6. **OSC 发送可靠性增强**
   - 改进 OSC 消息队列管理
   - 当队列满时提供明确的错误提示
   - 增加端口和主机地址的输入验证

7. **角色扮演预设扩充**
   - 新增 6 个角色扮演预设：元气好友、温柔兄姐、傲娇吐槽、游戏队友、礼貌翻译员、猫娘咖啡厅女仆
   - 为不同社交场景提供更多翻译风格选择

### 🐛 Bug 修复

- 修复桌面音频设备检测时可能导致的崩溃
- 修复快捷键注册失败时的翻译函数调用错误
- 修复 TTS 停止时的资源清理问题
- 移除虚拟音频设备的自动选择逻辑（避免误选）

---

## 🇬🇧 English Update Notes

v1.3.2.2 is a usability and stability polish release focusing on UI responsiveness and system reliability.

### ✨ Key Improvements

1. **Settings Page Scroll Optimization**
   - Increased scroll speed to feel closer to common desktop apps
   - Improved settings page operation fluidity

2. **AI Provider Layout Fix**
   - Fixed the Model section being pushed downward when switching AI providers
   - Settings now remeasures layout after content changes to ensure proper display

3. **Startup Loading Feedback**
   - Shows startup/loading progress immediately after listening starts
   - Reduced silent wait time before the loading bar appears
   - Prevents state confusion from repeated start button clicks

4. **Performance Optimization**
   - Reuses ASR instances for microphone and VRC listen
   - Caches model integrity check results to reduce repeated initialization overhead
   - Optimized audio device detection logic to minimize unnecessary stream creation

5. **TTS Playback Stability**
   - Improved thread safety for TTS audio playback
   - Fixed potential race conditions when stopping playback
   - Optimized playback queue management to prevent queue blocking

6. **OSC Send Reliability**
   - Improved OSC message queue management
   - Provides clear error messages when queue is full
   - Added input validation for port and host address

7. **Roleplay Preset Expansion**
   - Added 6 new roleplay presets: Energetic Friend, Gentle Big Sibling, Teasing Tsundere, Gamer Teammate, Polite Interpreter, Catgirl Cafe Maid
   - Offers more translation style choices for different social scenarios

### 🐛 Bug Fixes

- Fixed potential crash during desktop audio device detection
- Fixed translation function call error when hotkey registration fails
- Fixed TTS resource cleanup issues when stopping
- Removed automatic virtual audio device selection logic (to avoid misselection)

---

## 🇯🇵 日本語更新情報

v1.3.2.2 は操作感と安定性を整える修正版で、UI の応答性とシステムの信頼性に焦点を当てています。

### ✨ 主な改善点

1. **設定画面のスクロール最適化**
   - 一般的なデスクトップアプリに近い操作感へスクロール速度を調整
   - 設定画面の操作の流暢性を向上

2. **AI プロバイダー切り替え時のレイアウト修正**
   - AI サービスを切り替えたときに Model 欄が下へ押し出される問題を修正
   - 内容変更後に設定画面のレイアウトを再計測し、正常な表示を保証

3. **起動時の読み込み表示改善**
   - リスニング開始直後から起動/読み込み進捗を表示
   - 読み込みバーが出るまでの無音の待ち時間を減少
   - 開始ボタンの連続クリックによる状態の混乱を防止

4. **パフォーマンス最適化**
   - マイクと VRC listen で ASR インスタンスを再利用
   - モデル整合性チェック結果をキャッシュして、重複初期化の負荷を削減
   - オーディオデバイス検出ロジックを最適化し、不要なストリーム作成を最小化

5. **TTS 再生の安定性向上**
   - TTS オーディオ再生のスレッドセーフティを改善
   - 再生停止時の潜在的な競合状態を修正
   - 再生キュー管理を最適化し、キューのブロックを防止

6. **OSC 送信の信頼性強化**
   - OSC メッセージキュー管理を改善
   - キューが満杯の場合に明確なエラーメッセージを提供
   - ポートとホストアドレスの入力検証を追加

7. **ロールプレイプリセットの拡充**
   - 6 つの新しいロールプレイプリセットを追加：元気な友達、優しい兄姉、ツンデレ風、ゲーム仲間、丁寧な通訳、猫耳カフェメイド
   - 異なる社交シーンに対応する翻訳スタイルの選択肢を提供

### 🐛 バグ修正

- デスクトップオーディオデバイス検出時のクラッシュの可能性を修正
- ホットキー登録失敗時の翻訳関数呼び出しエラーを修正
- TTS 停止時のリソースクリーンアップ問題を修正
- 仮想オーディオデバイスの自動選択ロジックを削除（誤選択を回避）

---

## 📝 Technical Details

- **Version**: 1.3.2.2
- **Release Date**: 2026-05-07
- **Installer Size**: 
  - Full: 1106518485 bytes (1.03 GB)
  - Lite: 261761551 bytes (249.6 MB)
- **SHA256 Checksums**:
  - Full: `c2b9d0ecbda7b31a22cf3f837c46d90bf62fe1c5e2a10819921f27e197b58daa`
  - Lite: `d13f4857c19d2f380c6d427eeecd3376d51def6a62b48282a04f3dd1d08060af`

---

## 🔄 Upgrade Notes / 升级说明 / アップグレード注意事項

- **新用户 / New Users / 新規ユーザー**: 请下载 Full 完整包，已内置 SenseVoice 模型
- **老用户 / Existing Users / 既存ユーザー**: 下载 Lite 更新包即可，安装器会检测已有模型
- 支持从 v1.3.x 系列任意版本直接升级

---

## 🙏 致谢 / Acknowledgments / 謝辞

感谢所有用户的反馈和支持！

Thank you for all the feedback and support!

フィードバックとサポートをありがとうございます！
