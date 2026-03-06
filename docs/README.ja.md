# VRC Realtime Translator

[![zh-CN](https://img.shields.io/badge/README-zh--CN-2ea44f?style=for-the-badge)](../README.md)
[![ja](https://img.shields.io/badge/README-ja-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-en-0366d6?style=for-the-badge)](./README.en.md)

## 概要

VRChat 向けのローカルリアルタイム翻訳ツールです。ローカル ASR（SenseVoice）+ 切り替え可能な AI 翻訳バックエンド + OSC 通信で構成されています。

## 主な機能

- FunASR / SenseVoice Small を使ったローカル音声認識。
- 複数の翻訳バックエンド：OpenAI、DeepSeek、Qianwen、Anthropic、カスタム OpenAI 互換 API。
- OSC 経由で翻訳結果を VRChat チャットボックスへ送信。
- 受信したチャットボックス文を中国語へ逆翻訳し、フローティングウィンドウに表示。
- 手動翻訳パネル（入力してそのまま VRC へ送信可能）。

## クイックスタート

```bash
pip install -r requirements.txt
python main.py
```

初回起動時に SenseVoice Small（約 500MB）をダウンロードします。必要に応じてモデルキャッシュ先を指定できます。

```powershell
$env:MODELSCOPE_CACHE = "./models"
```

## 設定

1. `config.example.json` を `config.json` にコピーします。
2. 起動後 `Settings` で翻訳バックエンド（API Key / Base URL / Model）を設定します。
3. 必要に応じてマイク、VAD 無音しきい値、翻訳先言語、出力形式を調整します。

## プライバシー

> 本プロジェクトはデータ収集を一切行わず、チャット内容を保存しません。各メッセージは送信完了後に自動的に破棄されます。
>
> 本プロジェクトはローカルプロジェクトであり、独自のクラウドサーバーはありません。ユーザーの API キーがアップロードされ悪用されることはありません。
>
> アプリ自体に統計・トラッキング・ローカルチャットログ保存はありません。クラウド翻訳バックエンドを有効にした場合のみ、翻訳のために現在のメッセージが設定した API プロバイダへ送信されます。

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

- VRChat は他ユーザーの音声ストリームを公開していないため、逆翻訳はチャットボックス文字列ベースです。
- VRChat チャットボックスは 1 メッセージ約 144 文字上限で、自動切り詰めされます。
- 騒音環境では VAD の誤検出が発生する場合があります。`Settings` でしきい値を調整してください。
