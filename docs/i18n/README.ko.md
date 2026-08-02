# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | **한국어** | [中文](README.zh-CN.md) | [日本語](README.ja.md) | [Español](README.es.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Italiano](README.it.md)

`discord.py`, `yt-dlp`, FFmpeg로 구축된 포트폴리오 목적의 Discord 음악 봇
참조 구현입니다.

이 저장소에는 소스 코드만 포함되어 있습니다. 호스팅되는 봇 서비스가 아니며,
자격 증명, 쿠키, 다운로드된 미디어, 실행 중인 Discord 배포 환경은 포함하지
않습니다.

> [!NOTE]
> Discord 내 응답은 현지화되어 있습니다. 기본값은 영어이며, `.env`에
> `BOT_LANGUAGE="ko"`를 설정하면 한국어로 표시됩니다.

## 주요 기능

- YouTube 및 YouTube Music 재생
- Spotify 및 SoundCloud 메타데이터를 YouTube 검색으로 변환
- Apple Music URL 감지 및 검색 대체(fallback) 지원
- FFmpeg를 통한 직접 오디오 URL 재생
- YouTube Mix 결과를 활용한 자동 재생
- 동시성 제한을 적용한 재생목록 처리
- 개인 배포 환경을 위한 로컬 오디오 캐싱 지원
- 적응형 비트레이트 선택 및 수동 재정의
- 스트림 복구 및 음성 연결 모니터링
- 성능 지표 수집 및 명령어 오류 처리

## 명령어

| 명령어 | 설명 |
| --- | --- |
| `/play` | URL 또는 검색어로 곡이나 재생목록을 재생합니다 |
| `/join` | 봇을 사용자의 음성 채널로 호출합니다 |
| `/skip` | 현재 트랙을 건너뜁니다 |
| `/pause` / `/resume` | 재생을 일시정지하거나 다시 시작합니다 |
| `/stop` | 재생을 중지하고 봇의 연결을 해제합니다 |
| `/volume` | 재생 볼륨을 설정합니다 (0–100) |
| `/queue` | 현재 대기열을 표시합니다 |
| `/remove` | 대기열에서 지정한 위치의 트랙을 제거합니다 |
| `/nowplaying` | 현재 트랙의 상세 정보를 표시합니다 |
| `/autoplay` | 추천 트랙 자동 재생을 켜거나 끕니다 |
| `/bitrate` | 오디오 비트레이트를 설정합니다 (64–384 kbps) |
| `/bitrate-auto` | 채널의 최대 비트레이트에 자동으로 맞춥니다 |
| `/performance` | 봇 성능 지표를 표시합니다 |
| `/cache-info` | 오디오 캐시 상태를 표시합니다 |
| `/help` | 사용법 도움말을 표시합니다 |

## 빠른 시작

### 요구 사항

- Python 3.11 이상
- `PATH`에 있는 시스템 `ffmpeg` 실행 파일
- `discord.py`가 지원하는 시스템 Opus 라이브러리
- Deno, Node.js 또는 yt-dlp가 지원하는 기타 JavaScript 런타임
- 로컬 실행을 위한 Discord 애플리케이션 및 봇 토큰

macOS에서는 다음 명령으로 시스템 도구를 설치합니다.

```bash
brew install ffmpeg opus deno
```

`yt-dlp[default]` 의존성은 로컬 EJS 헬퍼 패키지를 설치합니다. YouTube 서명
처리를 위해 실행 시점에 지원되는 JavaScript 런타임이 필요하며, 두 의존성 중
하나라도 없으면 봇이 명확한 설정 오류 메시지와 함께 종료됩니다. 이 프로젝트는
실행 시점에 GitHub에서 실행 가능한 EJS 구성 요소를 가져오지 않습니다.

### 설치 및 실행

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

수동으로 구성하고 싶다면 `.env.example`을 `.env`로 복사한 뒤 값을 직접
입력합니다([설정](#설정) 참고). `python setup.py --check`는 어떤 파일도
변경하지 않고 의존성만 확인합니다.

## Docker

포함된 Compose 구성은 봇을 비루트(non-root) 사용자로 실행하며, `yt-dlp`를
매일 자동으로 업데이트합니다.

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- 오디오는 호스트의 `./music_library`에 캐시됩니다(컨테이너 내부의
  `/app/cache/audio`에 마운트됨).
- `supercronic`이 매일 18:15 UTC에 `yt-dlp`를 업그레이드하고 봇을
  재시작하므로, 추출기(extractor) 수정 사항이 수동 개입 없이 반영됩니다.
- 컨테이너는 기본적으로 UID/GID `1001`로 실행됩니다. 호스트 사용자와 맞추려면
  `APP_UID` / `APP_GID` 빌드 인자로 재정의합니다.
- TCP 연결이 과도하게 쌓이면 헬스체크가 컨테이너를 재시작합니다.

## 설정

`python setup.py`를 실행해 안내에 따라 구성하거나, `.env.example`을 `.env`로
복사한 뒤 필요한 값만 입력합니다.

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 예 | Discord 개발자 포털에서 발급받은 봇 토큰 |
| `BOT_LANGUAGE` | 아니요 | Discord 내 응답 언어: `en`(기본값) 또는 `ko` |
| `SPOTIFY_CLIENT_ID` | 아니요 | Spotify Web API를 통한 Spotify 링크 변환을 활성화합니다 |
| `SPOTIFY_CLIENT_SECRET` | 아니요 | `SPOTIFY_CLIENT_ID`와 함께 사용합니다. 둘 다 설정하지 않으면 Spotify 링크는 YouTube 검색으로 대체됩니다 |
| `AUDIO_CACHE_DIR` | 아니요 | 오디오 캐시 디렉터리 (기본값: `cache/audio`) |

`.env`, 봇 토큰, 서비스 자격 증명, 쿠키, 다운로드된 미디어, 로컬
`music_library/` 캐시는 절대 커밋해서는 안 됩니다.

## 프로젝트 구조

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

## 범위 및 책임 있는 사용

이 프로젝트는 기술 포트폴리오 예제로 제공됩니다. 운영자는 Discord, YouTube 및
기타 서비스의 약관은 물론 관련 저작권법과 개인정보 보호법을 준수할 책임이
있습니다. 이 프로젝트는 저작권이 있는 콘텐츠를 복사, 다운로드 또는 재배포할
권한을 부여하지 않습니다.

## 라이선스

이 저장소의 원본 코드는 GNU Affero General Public License v3.0 only
라이선스로 배포됩니다. 의존성 관련 고지는 [LICENSE](../../LICENSE)와
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)를 참고하세요.
