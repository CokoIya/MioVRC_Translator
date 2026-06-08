# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)
[![安定版 / Beta ダウンロード](https://img.shields.io/badge/安定版%20%2F%20Beta%20ダウンロード-78hejiu.top-ff6b35?style=for-the-badge)](https://78hejiu.top)

**公式ダウンロード: <https://78hejiu.top>**

> VRChat 向けのローカルリアルタイム翻訳ツール
> 作者: `ここ_Mio` / 公式ビルドは無料 / ソースコードは GPLv3-or-later
> ダウンロード先: <https://78hejiu.top>

## 概要

**Mio RealTime Translator** は VRChat 向けのデスクトップ翻訳ツールです。主に次の 2 つの場面を想定しています。

- 自分の発言を翻訳して `VRChat Chatbox` に送る
- VRChat で聞こえる音声を逆翻訳して、内容を理解しやすくする

現在の主な処理パイプライン:

- マイク翻訳: `Microphone -> ASR -> Translation -> VRChat Chatbox`
- 逆翻訳: `VRChat audio -> ASR -> Translation -> Chatbox / Floating display`

## ダウンロード

- 公式ダウンロード先: <https://78hejiu.top>
- 安定版、beta 版、今後のすべての更新は公式サイトで配布します
- GitHub Releases でも `full` インストーラーと `lite` 更新用インストーラーを配布します
- 初回ユーザーは `full`、既存ユーザーとオンライン更新は `lite` を利用してください
- GitHub は主にソースコード、Issue、開発履歴の公開に使います

## 主な機能

- ローカル音声認識とリアルタイム翻訳
- 翻訳結果を `VRChat Chatbox` に送信
- VRChat の再生音声を聞いて逆翻訳
- 手動テキスト翻訳パネルを内蔵
- 複数の chatbox 出力形式に対応
- 多言語 UI
- ASR 辞書、自声抑制、ノイズ除去、VAD、認識パラメータ調整
- `Avatar / OSC` パラメータ同期
- 任意で使えるフローティング表示

## 動作環境

- 推奨 OS: `Windows 10 / 11`
- 逆翻訳は `Windows WASAPI Loopback` に依存します
- モデルがローカルにない場合、初回起動時に `SenseVoice Small` を自動ダウンロードします
- `full` インストーラーには `SenseVoice Small` モデルが含まれます。`lite` インストーラーは既にモデルがある既存環境の更新向けです。

## ソースから起動

```bash
pip install -r requirements.txt
python main.py
```

先にモデルをダウンロードしたい場合:

```bash
python download_models.py
```

モデルキャッシュの保存先を変更したい場合:

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 初回設定

1. `config.example.json` を `config.json` にコピー
2. アプリを起動して `設定` を開く
3. 翻訳サービスを選び、対応する `API Key` を入力
4. 必要に応じて対象言語、出力形式、マイク、逆翻訳設定を調整
5. VRChat で `OSC` を有効化
   `Action Menu -> Options -> OSC -> Enable`

## プライバシー

- このプロジェクトはユーザーデータを収集しません
- チャット履歴を保存しません
- 既定のログには、認識テキスト、翻訳テキスト、chatbox の内容を記録しません
- chatbox に送るテキストは送信後に保持しません
- Windows 版では、保存された API Key をシステムの DPAPI で保護します
- 自動 UI 言語判定はローカルのシステム言語のみを読み取り、IP 位置情報は既定で使用しません
- クラウド翻訳を有効にした場合のみ、現在のテキストがあなた自身の設定した API サービスへ送信されます

## 補足

- VRChat の標準 OSC では他プレイヤーの生チャット本文は取得できません
- 逆翻訳はローカル再生デバイスのループバック収録に依存するため、PC 側の音声経路の影響を受けます
- VRChat Chatbox には送信頻度と 1 件あたりの文字数制限があります

## ライセンスとブランド

現在のバージョン以降、このプロジェクトのソースコードは [GNU GPLv3-or-later](../LICENSE) で公開されます。変更版やバイナリを配布する場合は GPLv3-or-later に従い、対応するソースコードを提供する必要があります。

`Mio RealTime Translator`、`Mio Translator`、ロゴ、アプリアイコン、公式サイト素材、リリース素材は GPL では許諾されません。非公式ビルドは別の名前と素材を使い、公式リリースではないことを明確に表示してください。詳しくは [BRANDING.md](../BRANDING.md) を参照してください。
