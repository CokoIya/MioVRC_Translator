# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> VRChat プレイヤー向けのローカルリアルタイム音声翻訳ツール
> 作者: `みお_Mio` / オープンソース / 有料再配布禁止

## バージョン

- デスクトップ版: `v1.2.0`
- GitHub Releases 軽量版インストーラー: `v1.2.0_release`

## 概要

**Mio RealTime Translator** は、VRChat 向けのローカルリアルタイム音声翻訳ツールです。コア構成は次のとおりです。

- ローカル音声認識: `SenseVoice Small`
- 翻訳バックエンド: `OpenAI` / `DeepSeek` / `Qianwen` / `Anthropic`
- VRChat 通信: `OSC`
- 逆翻訳表示: 受信した chatbox テキストをフローティングウィンドウに表示

## 主な機能

- マイク入力をローカルで認識し、その結果を VRChat chatbox に送信
- 手動入力して VRC へ送信できる翻訳パネルを搭載
- `訳文（原文）`、`訳文のみ`、`原文のみ`、`原文（訳文）` など複数の出力形式を選択可能
- 受信した chatbox メッセージを逆翻訳してフローティングウィンドウに表示
- UI 言語の切り替えに対応
- ストリーミング認識パラメータを調整可能

## ダウンロード

### GitHub Releases

[Releases](https://github.com/CokoIya/MioVRC_Translator/releases) から最新の Windows 版をダウンロードできます。

軽量版リリースには **SenseVoice Small モデルは同梱されません**。初回起動時にモデルが存在しない場合は、自動でダウンロードが始まり、進捗はアプリ下部に表示されます。

### 完全オフライン版

- QQ 1 群: `1077205718`
- QQ 2 群: `756274989`
- 百度网盘: `https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601`

## クイックスタート

```bash
pip install -r requirements.txt
python main.py
```

事前にモデルをダウンロードしたい場合:

```bash
python download_models.py
```

SenseVoice Small は初回起動時にも自動ダウンロードされます。モデルキャッシュ先を変更したい場合は次を設定してください。

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 設定手順

1. `config.example.json` を `config.json` にコピー
2. アプリ起動後に `Settings` を開く
3. 翻訳バックエンドを選び、対応する `API Key` を入力
4. 必要に応じてマイク、VAD の静音しきい値、対象言語、出力形式を調整
5. VRChat 側で OSC を有効化: `Action Menu -> Options -> OSC -> Enable`

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

- このプロジェクトは利用者データを収集しません
- チャットログを保存しません
- chatbox メッセージは送信後すぐに破棄されます
- プロジェクト独自のクラウドサービスはありません
- クラウド翻訳バックエンドを有効にした場合のみ、現在の翻訳対象テキストが自分で設定した API 提供元へ送信されます

## 技術スタック

| モジュール | 技術 |
| --- | --- |
| UI | CustomTkinter |
| 音声入力 | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| 翻訳 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc |

## 既知の制限

- VRChat は他プレイヤーの生音声ストリームを公開していないため、逆翻訳は chatbox テキストのみが対象です
- VRChat chatbox の 1 メッセージ上限は約 144 文字です
- 騒がしい環境では VAD が誤反応する場合があり、手動調整が必要です
