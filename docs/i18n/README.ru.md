# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md) | [Español](README.es.md) | **Русский** | [Deutsch](README.de.md) | [Français](README.fr.md) | [Italiano](README.it.md)

Референсная реализация Discord-бота для воспроизведения музыки, ориентированная
на портфолио и построенная на `discord.py`, `yt-dlp` и FFmpeg.

Этот репозиторий содержит только исходный код. Это не хостинг-сервис бота:
в нём нет учётных данных, cookie-файлов, загруженных медиафайлов или
работающего развёртывания в Discord.

> [!NOTE]
> Ответы бота в Discord локализованы: по умолчанию английский, корейский —
> при `BOT_LANGUAGE="ko"` в вашем `.env`.

## Возможности

- Воспроизведение с YouTube и YouTube Music
- Преобразование метаданных Spotify и SoundCloud в поисковые запросы на YouTube
- Распознавание ссылок Apple Music с резервным вариантом через поиск
- Воспроизведение прямых аудио-URL через FFmpeg
- Автовоспроизведение на основе результатов YouTube Mix
- Обработка плейлистов с ограниченным числом параллельных задач
- Поддержка локального кэширования аудио для приватных развёртываний
- Адаптивный выбор битрейта и возможность ручного переопределения
- Восстановление потока и мониторинг голосового соединения
- Метрики производительности и обработка ошибок команд

## Команды

| Команда | Описание |
| --- | --- |
| `/play` | Воспроизвести трек или плейлист по URL или поисковому запросу |
| `/join` | Позвать бота в ваш голосовой канал |
| `/skip` | Пропустить текущий трек |
| `/pause` / `/resume` | Приостановить или возобновить воспроизведение |
| `/stop` | Остановить воспроизведение и отключить бота |
| `/volume` | Установить громкость воспроизведения (0–100) |
| `/queue` | Показать текущую очередь |
| `/remove` | Удалить трек из очереди по номеру позиции |
| `/nowplaying` | Показать сведения о текущем треке |
| `/autoplay` | Включить или выключить автовоспроизведение рекомендованных треков |
| `/bitrate` | Установить битрейт аудио (64–384 кбит/с) |
| `/bitrate-auto` | Автоматически подстроиться под максимальный битрейт канала |
| `/performance` | Показать метрики производительности бота |
| `/cache-info` | Показать состояние аудиокэша |
| `/help` | Показать справку по использованию |

## Быстрый старт

### Требования

- Python 3.11 или новее
- Системный исполняемый файл `ffmpeg`, доступный в `PATH`
- Системная библиотека Opus, поддерживаемая `discord.py`
- Deno, Node.js или другая среда выполнения JavaScript, поддерживаемая yt-dlp
- Приложение Discord и токен бота для локального запуска

На macOS системные инструменты устанавливаются так:

```bash
brew install ffmpeg opus deno
```

Зависимость `yt-dlp[default]` устанавливает локальный вспомогательный пакет
EJS. Для обработки подписей YouTube во время работы требуется поддерживаемая
среда выполнения JavaScript; если какая-либо из этих зависимостей отсутствует,
бот завершает работу с понятным сообщением об ошибке настройки. Проект не
загружает исполняемые компоненты EJS с GitHub во время выполнения.

### Установка и запуск

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

Предпочитаете ручную настройку? Скопируйте `.env.example` в `.env` и
заполните значения самостоятельно (см. [Настройка](#настройка)). Команда
`python setup.py --check` проверяет зависимости, не изменяя никаких файлов.

## Docker

Входящая в комплект конфигурация Compose запускает бота от имени
непривилегированного пользователя (без root) с ежедневным самообновлением
`yt-dlp`:

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- Аудио кэшируется в `./music_library` на хосте (каталог монтируется в
  `/app/cache/audio` внутри контейнера).
- `supercronic` ежедневно в 18:15 UTC обновляет `yt-dlp` и перезапускает
  бота, поэтому исправления экстракторов применяются без ручного
  вмешательства.
- По умолчанию контейнер работает с UID/GID `1001`; переопределите их
  аргументами сборки `APP_UID` / `APP_GID`, чтобы они совпадали с вашим
  пользователем на хосте.
- Проверка работоспособности (healthcheck) перезапускает контейнер, если
  накапливаются TCP-соединения.

## Настройка

Запустите `python setup.py` для пошаговой настройки либо скопируйте
`.env.example` в `.env` и заполните только те значения, которые вам нужны:

| Переменная | Обязательна | Описание |
| --- | --- | --- |
| `DISCORD_TOKEN` | Да | Токен бота из Discord Developer Portal |
| `BOT_LANGUAGE` | Нет | Язык ответов бота в Discord: `en` (по умолчанию) или `ko` |
| `SPOTIFY_CLIENT_ID` | Нет | Включает разрешение ссылок Spotify через Spotify Web API |
| `SPOTIFY_CLIENT_SECRET` | Нет | Используется в паре с `SPOTIFY_CLIENT_ID`; если задана только одна из переменных, ссылки Spotify обрабатываются через поиск на YouTube |
| `AUDIO_CACHE_DIR` | Нет | Каталог аудиокэша (по умолчанию: `cache/audio`) |

Никогда не коммитьте `.env`, токены ботов, учётные данные сервисов,
cookie-файлы, загруженные медиафайлы и локальный кэш `music_library/`.

## Структура проекта

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

## Область применения и ответственное использование

Этот проект предоставляется как пример для технического портфолио. Операторы
несут ответственность за соблюдение условий использования Discord, YouTube и
других сервисов, а также применимого законодательства об авторском праве и
защите персональных данных. Проект не даёт разрешения копировать, скачивать
или распространять контент, защищённый авторским правом.

## Лицензия

Оригинальный код в этом репозитории распространяется исключительно по
лицензии GNU Affero General Public License v3.0. См. [LICENSE](../../LICENSE)
и [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md), где приведены
уведомления о зависимостях.
