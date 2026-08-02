# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | **日本語** | [Español](README.es.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Italiano](README.it.md)

`discord.py`、`yt-dlp`、FFmpeg で構築された、ポートフォリオ向けの Discord 音楽ボットのリファレンス実装です。

このリポジトリにはソースコードのみが含まれています。ホスティングされたボットサービスではなく、認証情報、Cookie、ダウンロード済みメディア、稼働中の Discord デプロイメントは含まれていません。

> [!NOTE]
> Discord 内の応答はローカライズされています。デフォルトは英語で、`.env` に
> `BOT_LANGUAGE="ko"` を設定すると韓国語になります。

## 機能

- YouTube および YouTube Music の再生
- Spotify と SoundCloud のメタデータを YouTube 検索へ解決
- Apple Music の URL 検出と検索フォールバック
- FFmpeg による音声 URL の直接再生
- YouTube Mix の結果を利用した自動再生(オートプレイ)
- 並列数を制限したプレイリスト処理
- プライベート環境向けのローカル音声キャッシュ対応
- アダプティブビットレート選択と手動設定
- ストリーム復旧とボイス接続の監視
- パフォーマンスメトリクスとコマンドエラー処理

## コマンド

| コマンド | 説明 |
| --- | --- |
| `/play` | URL または検索キーワードから曲やプレイリストを再生します |
| `/join` | ボットを自分のボイスチャンネルに呼び出します |
| `/skip` | 現在のトラックをスキップします |
| `/pause` / `/resume` | 再生を一時停止または再開します |
| `/stop` | 再生を停止してボットを切断します |
| `/volume` | 再生音量を設定します(0–100) |
| `/queue` | 現在のキューを表示します |
| `/remove` | 位置を指定してキューからトラックを削除します |
| `/nowplaying` | 現在のトラックの詳細を表示します |
| `/autoplay` | おすすめトラックの自動再生を切り替えます |
| `/bitrate` | 音声ビットレートを設定します(64–384 kbps) |
| `/bitrate-auto` | チャンネルの最大ビットレートに自動的に合わせます |
| `/performance` | ボットのパフォーマンスメトリクスを表示します |
| `/cache-info` | 音声キャッシュの状態を表示します |
| `/help` | 使い方のヘルプを表示します |

## クイックスタート

### 必要条件

- Python 3.11 以降
- `PATH` 上にあるシステムの `ffmpeg` 実行ファイル
- `discord.py` がサポートするシステムの Opus ライブラリ
- Deno、Node.js、または yt-dlp がサポートするその他の JavaScript ランタイム
- ローカル実行用の Discord アプリケーションとボットトークン

macOS では、次のコマンドでシステムツールをインストールできます。

```bash
brew install ffmpeg opus deno
```

`yt-dlp[default]` 依存関係により、ローカルの EJS ヘルパーパッケージがインストールされます。YouTube の署名処理には、実行時にサポート対象の JavaScript ランタイムが必要です。いずれかの依存関係が欠けている場合、ボットは分かりやすいセットアップエラーを表示して終了します。本プロジェクトは、実行時に GitHub から実行可能な EJS コンポーネントを取得することはありません。

### インストールと実行

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

手動で設定したい場合は、`.env.example` を `.env` にコピーして値を自分で入力してください([設定](#設定)を参照)。`python setup.py --check` は、ファイルに一切変更を加えずに依存関係を検証します。

## Docker

付属の Compose 構成では、ボットを非 root ユーザーとして実行し、`yt-dlp` を毎日自動更新します。

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- 音声はホスト側の `./music_library` にキャッシュされます(コンテナ内では
  `/app/cache/audio` にマウントされます)。
- `supercronic` が毎日 18:15 UTC に `yt-dlp` をアップグレードしてボットを再起動するため、エクストラクターの修正が手動操作なしで反映されます。
- コンテナはデフォルトで UID/GID `1001` として動作します。ホストユーザーに合わせるには、ビルド引数
  `APP_UID` / `APP_GID` で上書きしてください。
- TCP 接続が滞留した場合は、ヘルスチェックがコンテナを再起動します。

## 設定

`python setup.py` を実行して対話形式で設定するか、`.env.example` を `.env` にコピーして必要な値のみを入力してください。

| 変数 | 必須 | 説明 |
| --- | --- | --- |
| `DISCORD_TOKEN` | はい | Discord Developer Portal で取得したボットトークン |
| `BOT_LANGUAGE` | いいえ | Discord 内の応答に使用する言語: `en`(デフォルト)または `ko` |
| `SPOTIFY_CLIENT_ID` | いいえ | Spotify Web API による Spotify リンクの解決を有効にします |
| `SPOTIFY_CLIENT_SECRET` | いいえ | `SPOTIFY_CLIENT_ID` とセットで使用します。両方が揃っていない場合、Spotify リンクは YouTube 検索にフォールバックします |
| `AUDIO_CACHE_DIR` | いいえ | 音声キャッシュディレクトリ(デフォルト: `cache/audio`) |

`.env`、ボットトークン、サービスの認証情報、Cookie、ダウンロード済みメディア、ローカルの `music_library/` キャッシュは、決してコミットしないでください。

## プロジェクト構成

```text
music_bot.py             # application entry point and Discord commands
setup.py                 # interactive setup wizard (deps check + .env)
src/audio/               # FFmpeg, bitrate, and stream recovery helpers
src/cache/               # optional local audio cache implementation
src/sources/             # source detection and metadata resolvers
src/utils/               # error handling, monitoring, and yt-dlp lifecycle
tests/                   # standalone test scripts
Dockerfile               # container image (non-root, supercronic + Deno)
docker-compose.yml       # single-service deployment with healthcheck
bin/docker-entrypoint.sh # runs the bot alongside the update cron
config/crontab           # daily yt-dlp upgrade schedule
requirements.txt         # runtime Python dependencies
```

## 適用範囲と責任ある利用

本プロジェクトは、技術ポートフォリオの一例として提供されています。運用者は、Discord、YouTube、その他のサービスの利用規約、および適用される著作権法・プライバシー法を遵守する責任を負います。本プロジェクトは、著作権で保護されたコンテンツの複製、ダウンロード、再配布を許可するものではありません。

## ライセンス

このリポジトリのオリジナルコードは、GNU Affero General Public License v3.0 only の下でライセンスされています。依存関係に関する表記については、[LICENSE](../../LICENSE) および
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) を参照してください。
