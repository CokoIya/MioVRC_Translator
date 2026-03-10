# Mio RealTime Translator

[![ja](https://img.shields.io/badge/README-日本語-f39c12?style=for-the-badge)](./README.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./docs/README.en.md)

> VRChat 向けのローカルリアルタイム音声翻訳ツール  
> 作者: `みお_Mio` / オープンソース / 有料再配布禁止

## バージョン

- デスクトップ版: `v1.2.0`
- GitHub Releases 軽量版インストーラー: `v1.2.0`

## 概要

Mio RealTime Translator は、VRChat プレイヤー向けのローカルリアルタイム音声翻訳ツールです。

主な構成:

- ローカル音声認識: `SenseVoice Small`
- 翻訳バックエンド: `OpenAI` / `DeepSeek` / `Qianwen` / `Anthropic`
- VRChat 連携: `OSC`
- 受信チャットの逆翻訳表示: フローティングウィンドウ

## 主な機能

- マイク入力をリアルタイムで認識し、VRChat チャットボックスへ送信
- 手動テキスト入力と翻訳パネルを搭載
- 翻訳結果の出力形式を複数パターンから選択可能
- 受信チャットを逆翻訳してフローティングウィンドウに表示
- UI 言語を複数言語から切り替え可能

## ダウンロード

### GitHub Releases

[Releases](https://github.com/CokoIya/MioVRC_Translator/releases) から最新版をダウンロードできます。

Releases の軽量版は **SenseVoice Small モデルを同梱しません**。  
初回起動時にモデルが見つからない場合、自動でダウンロードを開始し、進捗はアプリ下部に表示されます。

### 完全オフライン用モデル付き配布

- QQ 1 群: `1077205718`
- QQ 2 群: `756274989`
- 百度网盘: `https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601`

## クイックスタート

```bash
pip install -r requirements.txt
python main.py
```

モデルを事前に取得したい場合:

```bash
python download_models.py
```

## 設定手順

1. `config.example.json` を `config.json` にコピー
2. アプリを起動して右上の `設定` を開く
3. 利用する翻訳バックエンドの `API Key` を入力
4. VRChat 側で `Action Menu -> Options -> OSC -> Enable` を有効化

## ビルド

### Releases 軽量版

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite.ps1
```

### Releases 軽量版インストーラー

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release_lite_installer.ps1
```

## プライバシー

- このプロジェクト自体は利用者データを収集しません
- チャットログを保存しません
- メッセージは送信完了後に即時破棄されます
- クラウド翻訳を利用する場合のみ、現在の翻訳対象テキストが自分で設定した API 提供元へ送信されます

## 技術スタック

| モジュール | 技術 |
| --- | --- |
| UI | CustomTkinter |
| 音声入力 | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| 翻訳 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc |

## 既知の注意点

- VRChat は他プレイヤーの生音声ストリームを公開していないため、逆翻訳はチャットボックス文字列ベースです
- チャットボックスの 1 メッセージ上限は約 144 文字です
- 一部環境では VRChat 側が不安定になる報告があり、引き続き調査中です

