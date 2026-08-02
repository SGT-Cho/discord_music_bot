# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | **中文** | [日本語](README.ja.md) | [Español](README.es.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Italiano](README.it.md)

一个面向作品集展示的 Discord 音乐机器人参考实现，基于
`discord.py`、`yt-dlp` 和 FFmpeg 构建。

本仓库仅包含源代码。它不是托管的机器人服务，也不包含凭据、
Cookie、已下载的媒体文件或正在运行的 Discord 部署。

> [!NOTE]
> Discord 内的响应支持本地化：默认为英语，在 `.env` 中设置
> `BOT_LANGUAGE="ko"` 可切换为韩语。

## 功能特性

- 支持 YouTube 和 YouTube Music 播放
- 将 Spotify 和 SoundCloud 元数据解析为 YouTube 搜索
- 识别 Apple Music 链接并回退到搜索
- 通过 FFmpeg 直接播放音频 URL
- 基于 YouTube Mix 结果的自动播放
- 支持有限并发的播放列表处理
- 面向私有部署的本地音频缓存支持
- 自适应比特率选择与手动覆盖
- 流恢复与语音连接监控
- 性能指标统计与命令错误处理

## 命令

| 命令 | 说明 |
| --- | --- |
| `/play` | 通过 URL 或搜索关键词播放歌曲或播放列表 |
| `/join` | 将机器人召唤到你所在的语音频道 |
| `/skip` | 跳过当前曲目 |
| `/pause` / `/resume` | 暂停或恢复播放 |
| `/stop` | 停止播放并断开机器人连接 |
| `/volume` | 设置播放音量（0–100） |
| `/queue` | 显示当前播放队列 |
| `/remove` | 按位置从队列中移除曲目 |
| `/nowplaying` | 显示当前曲目的详细信息 |
| `/autoplay` | 开关推荐曲目的自动播放 |
| `/bitrate` | 设置音频比特率（64–384 kbps） |
| `/bitrate-auto` | 自动匹配频道的最大比特率 |
| `/performance` | 显示机器人性能指标 |
| `/cache-info` | 显示音频缓存状态 |
| `/help` | 显示使用帮助 |

## 快速开始

### 环境要求

- Python 3.11 或更高版本
- 系统 `PATH` 中可用的 `ffmpeg` 可执行文件
- `discord.py` 支持的系统 Opus 库
- Deno、Node.js 或其他 yt-dlp 支持的 JavaScript 运行时
- 用于本地运行的 Discord 应用及机器人令牌

在 macOS 上，可通过以下命令安装系统工具：

```bash
brew install ffmpeg opus deno
```

`yt-dlp[default]` 依赖会安装本地 EJS 辅助包。运行时需要一个受
支持的 JavaScript 运行时来处理 YouTube 签名；如果缺少任一依赖，
机器人会退出并给出明确的配置错误提示。本项目不会在运行时从
GitHub 获取可执行的 EJS 组件。

### 安装与运行

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

更喜欢手动配置？将 `.env.example` 复制为 `.env` 并自行填写各项值
（参见[配置](#配置)）。`python setup.py --check` 仅检查依赖，
不会修改任何文件。

## Docker

随附的 Compose 配置以非 root 用户运行机器人，并让 `yt-dlp`
每日自动更新：

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- 音频缓存在宿主机的 `./music_library` 目录（挂载到容器内的
  `/app/cache/audio`）。
- `supercronic` 每天 18:15 UTC 升级 `yt-dlp` 并重启机器人，
  使提取器修复无需人工干预即可生效。
- 容器默认以 UID/GID `1001` 运行；可通过 `APP_UID` / `APP_GID`
  构建参数覆盖，以匹配宿主机用户。
- 健康检查会在 TCP 连接堆积时重启容器。

## 配置

运行 `python setup.py` 进行引导式配置，或将 `.env.example` 复制为
`.env` 并只填写你需要的值：

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 是 | 从 Discord 开发者门户获取的机器人令牌 |
| `BOT_LANGUAGE` | 否 | Discord 内响应的语言：`en`（默认）或 `ko` |
| `SPOTIFY_CLIENT_ID` | 否 | 启用通过 Spotify Web API 解析 Spotify 链接 |
| `SPOTIFY_CLIENT_SECRET` | 否 | 与 `SPOTIFY_CLIENT_ID` 配对使用；若两者不全，Spotify 链接会回退到 YouTube 搜索 |
| `AUDIO_CACHE_DIR` | 否 | 音频缓存目录（默认：`cache/audio`） |

切勿提交 `.env`、机器人令牌、服务凭据、Cookie、已下载的媒体
文件或本地 `music_library/` 缓存。

## 项目结构

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

## 适用范围与合理使用

本项目仅作为技术作品集示例提供。运营者需自行遵守 Discord、
YouTube 及其他服务的条款，以及适用的版权和隐私法律。本项目不
授予复制、下载或再分发受版权保护内容的许可。

## 许可证

本仓库中的原创代码采用 GNU Affero General Public License v3.0 only
许可。依赖项声明请参见 [LICENSE](../../LICENSE) 和
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。
