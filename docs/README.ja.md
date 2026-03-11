# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-2ea44f?style=for-the-badge)](./README.zh-CN.md)
[![ja](https://img.shields.io/badge/README-%E6%97%A5%E6%9C%AC%E8%AA%9E-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> VRChat 向けのローカルリアルタイム音声翻訳ツール  
> 作者: `ここ_Mio` / オープンソースプロジェクト / 有料での再配布禁止

## バージョン

- デスクトップ版: `v1.2.1`
- GitHub Releases 軽量版インストーラー: `v1.2.1_release`

## 概要

**Mio RealTime Translator** は、VRChat 向けのローカルリアルタイム音声翻訳ツールです。現在の主な構成は以下の通りです。

- ローカル音声認識: `SenseVoice Small`
- 翻訳サービス: `GPT` / `DeepSeek` / `GLM` / `Qwen` / `Claude`
- VRChat 通信: `OSC`

## 主な機能

- マイク入力をローカルで認識し、結果を VRChat chatbox に送信
- 手動入力して VRC に送れる翻訳パネルを内蔵
- `訳文（原文）`、`訳文のみ`、`原文のみ`、`原文（訳文）` など複数の出力形式に対応
- 各翻訳サービスにプリセットモデル一覧を用意し、速度・品質・プラグイン適性を表示
- ノイズ除去強度、VAD 無音しきい値、ストリーミング認識パラメータを調整可能
- メイン画面と設定画面をよりコンパクトなデスクトップ UI に最適化し、ポップアップの位置とアイコンも統一
- UI 言語切り替えに対応

## ダウンロード

### GitHub Releases

[Releases](https://github.com/CokoIya/MioVRC_Translator/releases) から最新の Windows 版をダウンロードできます。

軽量版リリースには **SenseVoice Small モデルは同梱されません**。初回起動時にモデルが見つからない場合は、自動でダウンロードが始まり、下部ステータス欄に進捗が表示されます。

### 完全オフラインモデルパック

- QQ 1 群: `1077205718`
- QQ 2 群: `756274989`
- 百度网盘: `https://pan.baidu.com/s/1HIdfd7tV3o1t845FKpu40g?pwd=0601`

## クイックスタート

```bash
pip install -r requirements.txt
python main.py
```

先にモデルをダウンロードしたい場合:

```bash
python download_models.py
```

SenseVoice Small は初回起動時にも自動ダウンロードされます。モデルキャッシュ先を変更したい場合は次を設定してください。

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 設定手順

1. `config.example.json` を `config.json` にコピー
2. アプリ起動後に `設定` を開く
3. 翻訳サービスを選び、対応する `API Key` を入力
4. 必要に応じてマイク、ノイズ除去、VAD、対象言語、出力形式を調整
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

- 本プロジェクトはユーザーデータを収集しません
- チャットログを保存しません
- chatbox メッセージは送信後すぐに破棄されます
- プロジェクト独自のクラウドサービスはありません
- クラウド翻訳サービスを有効にした場合のみ、現在の翻訳対象テキストが自分で設定した API サービス先へ送信されます

## 技術スタック

| モジュール | 技術 |
| --- | --- |
| UI | CustomTkinter |
| 音声入力 | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| 翻訳 | OpenAI SDK / Anthropic SDK / OpenAI Compatible API |
| VRChat 通信 | python-osc |

## 既知の制限

- VRChat のネイティブ OSC では他プレイヤーのチャット本文や生音声を取得できないため、現行版は自分のマイク入力と手動テキスト入力のみを扱います
- VRChat chatbox の 1 メッセージ上限はおよそ 144 文字です
- 騒がしい環境では誤反応が起きる場合があるため、ノイズ除去と VAD の手動調整が必要になることがあります
