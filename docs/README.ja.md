# Mio RealTime Translator

[![zh-CN](https://img.shields.io/badge/README-中文-2ea44f?style=for-the-badge)](../README.md)
[![ja](https://img.shields.io/badge/README-日本語-f39c12?style=for-the-badge)](./README.ja.md)
[![en](https://img.shields.io/badge/README-English-0366d6?style=for-the-badge)](./README.en.md)

> 上のバッジをクリックして言語を切り替え

---

## 概要

**Mio RealTime Translator** は、VRChat プレイヤー向けのローカルリアルタイム音声翻訳ツールです。VRC プレイヤー **酒寄 みお** が制作し、完全オープンソースで、いかなる有料配布も禁止しています。

コアアーキテクチャ：ローカル ASR（SenseVoice）＋ 切り替え可能な AI 翻訳バックエンド ＋ VRChat OSC 通信。

---

## 主な機能

- **ローカル音声認識** — FunASR / SenseVoice Small を使用。完全ローカル推論、低遅延、オフライン動作。
- **複数の翻訳バックエンド** — OpenAI、DeepSeek、Qianwen、Anthropic、およびカスタム OpenAI 互換 API をサポート。
- **VRChat 連携** — 公式 OSC インターフェースを通じて翻訳結果を VRChat チャットボックスへ直接送信。
- **逆翻訳** — 他ユーザーのチャットボックスを中国語に翻訳し、フローティングウィンドウに表示。
- **手動翻訳パネル** — Google 翻訳スタイルの左右 2 列レイアウト。テキスト入力後にワンクリックで VRC へ送信。
- **柔軟な出力形式** — `日語（中文）`・`日語のみ`・`中文のみ`・`中文（日語）` の 4 形式から選択可能。

---

## クイックスタート

```bash
pip install -r requirements.txt
python main.py
```

[Releases](https://github.com/CokoIya/MioVRC_Translator/releases) から Windows EXE 版をダウンロードすることもできます（Python 環境不要、ダウンロードしてすぐ使用可能）。

> 初回起動時に SenseVoice Small モデル（約 500 MB）が自動でダウンロードされます。
> モデルキャッシュ先を任意に指定できます：
> ```powershell
> $env:MODELSCOPE_CACHE = "./models"
> ```

---

## 設定手順

1. `config.example.json` を `config.json` にコピーします。
2. 起動後、`⚙ 設置` で翻訳バックエンドの情報（API Key / Base URL / Model）を入力します。
3. 必要に応じてマイク、VAD 無音しきい値、翻訳先言語、出力形式を調整します。
4. VRChat で OSC を有効化します：**Action Menu → Options → OSC → Enable**。

---

## プライバシーに関して

> **本プロジェクトはデータ収集を一切行わず、チャット内容を保存しません。各メッセージは送信完了後に自動的に破棄されます。**
>
> 本プロジェクトはローカル専用であり、プロジェクト固有のクラウドサーバーはありません。ユーザーの API キーがアップロードされたり悪用されたりすることはありません。
>
> アプリ自体に統計・トラッキング・ローカルへのチャットログ書き込み処理は含まれていません。クラウド翻訳バックエンドを有効にした場合のみ、翻訳のために現在のメッセージが自分で設定した API プロバイダーへ送信されます。

---

## 技術スタック

| モジュール | 技術 |
| --- | --- |
| UI | CustomTkinter |
| 音声入力 | sounddevice |
| VAD | webrtcvad |
| ASR | FunASR / SenseVoice Small |
| 翻訳 | OpenAI SDK / Anthropic SDK |
| VRChat 通信 | python-osc (OSC UDP) |

---

## 既知の制限

- VRChat は他ユーザーの音声ストリームを公開していないため、逆翻訳はチャットボックス文字列ベースです。
- VRChat チャットボックスは 1 メッセージ約 144 文字が上限で、超過分は自動切り詰めされます。
- 騒音環境では VAD の誤検出が発生する場合があります。`設置` でしきい値を調整してください。
