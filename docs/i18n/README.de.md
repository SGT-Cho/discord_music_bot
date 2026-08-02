# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md) | [Español](README.es.md) | [Русский](README.ru.md) | **Deutsch** | [Français](README.fr.md) | [Italiano](README.it.md)

Eine portfolioorientierte Referenzimplementierung eines Discord-Musikbots auf Basis
von `discord.py`, `yt-dlp` und FFmpeg.

Dieses Repository enthält ausschließlich Quellcode. Es handelt sich nicht um einen
gehosteten Bot-Dienst und es enthält weder Zugangsdaten, Cookies, heruntergeladene
Medien noch ein laufendes Discord-Deployment.

> [!NOTE]
> Die Antworten in Discord sind lokalisiert: standardmäßig Englisch, Koreanisch mit
> `BOT_LANGUAGE="ko"` in der `.env`-Datei.

## Funktionen

- Wiedergabe von YouTube und YouTube Music
- Auflösung von Spotify- und SoundCloud-Metadaten in YouTube-Suchen
- Erkennung von Apple-Music-URLs mit Such-Fallback
- Direkte Wiedergabe von Audio-URLs über FFmpeg
- Autoplay über YouTube-Mix-Ergebnisse
- Playlist-Verarbeitung mit begrenzter Parallelität
- Unterstützung für lokales Audio-Caching in privaten Deployments
- Adaptive Bitratenwahl mit manueller Übersteuerung
- Stream-Wiederherstellung und Überwachung der Sprachverbindung
- Performance-Metriken und Fehlerbehandlung für Befehle

## Befehle

| Befehl | Beschreibung |
| --- | --- |
| `/play` | Spielt einen Titel oder eine Playlist per URL oder Suchanfrage ab |
| `/join` | Holt den Bot in den eigenen Sprachkanal |
| `/skip` | Überspringt den aktuellen Titel |
| `/pause` / `/resume` | Pausiert die Wiedergabe oder setzt sie fort |
| `/stop` | Stoppt die Wiedergabe und trennt den Bot |
| `/volume` | Legt die Wiedergabelautstärke fest (0–100) |
| `/queue` | Zeigt die aktuelle Warteschlange |
| `/remove` | Entfernt einen Titel anhand seiner Position aus der Warteschlange |
| `/nowplaying` | Zeigt Details zum aktuellen Titel |
| `/autoplay` | Schaltet das Autoplay empfohlener Titel ein oder aus |
| `/bitrate` | Legt die Audio-Bitrate fest (64–384 kbps) |
| `/bitrate-auto` | Passt die Bitrate automatisch an das Maximum des Kanals an |
| `/performance` | Zeigt Performance-Metriken des Bots |
| `/cache-info` | Zeigt den Status des Audio-Caches |
| `/help` | Zeigt die Bedienungshilfe |

## Schnellstart

### Voraussetzungen

- Python 3.11 oder neuer
- Ein systemweit installiertes `ffmpeg` im `PATH`
- Eine von `discord.py` unterstützte Opus-Systembibliothek
- Deno, Node.js oder eine andere von yt-dlp unterstützte JavaScript-Laufzeitumgebung
- Eine Discord-Anwendung samt Bot-Token für die lokale Ausführung

Unter macOS lassen sich die Systemwerkzeuge wie folgt installieren:

```bash
brew install ffmpeg opus deno
```

Die Abhängigkeit `yt-dlp[default]` installiert das lokale EJS-Hilfspaket. Zur
Laufzeit wird eine unterstützte JavaScript-Laufzeitumgebung für die Verarbeitung
von YouTube-Signaturen benötigt; fehlt eine der beiden Abhängigkeiten, beendet
sich der Bot mit einer eindeutigen Einrichtungsfehlermeldung. Das Projekt lädt
zur Laufzeit keine ausführbaren EJS-Komponenten von GitHub herunter.

### Installation und Start

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

Lieber manuell konfigurieren? Dazu `.env.example` nach `.env` kopieren und die
Werte selbst eintragen (siehe [Konfiguration](#konfiguration)). `python setup.py
--check` prüft die Abhängigkeiten, ohne Dateien anzufassen.

## Docker

Das mitgelieferte Compose-Setup führt den Bot als Nicht-Root-Benutzer mit einem
täglich selbstaktualisierenden `yt-dlp` aus:

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- Audio wird auf dem Host unter `./music_library` zwischengespeichert (im
  Container unter `/app/cache/audio` eingebunden).
- `supercronic` aktualisiert `yt-dlp` täglich um 18:15 UTC und startet den Bot
  neu, sodass Extractor-Fixes ohne manuelles Eingreifen ankommen.
- Der Container läuft standardmäßig mit UID/GID `1001`; über die Build-Argumente
  `APP_UID` / `APP_GID` lässt sich das an den Host-Benutzer anpassen.
- Ein Healthcheck startet den Container neu, wenn sich TCP-Verbindungen
  anstauen.

## Konfiguration

`python setup.py` startet eine geführte Konfiguration; alternativ `.env.example`
nach `.env` kopieren und nur die benötigten Werte eintragen:

| Variable | Erforderlich | Beschreibung |
| --- | --- | --- |
| `DISCORD_TOKEN` | Ja | Bot-Token aus dem Discord Developer Portal |
| `BOT_LANGUAGE` | Nein | Sprache der Antworten in Discord: `en` (Standard) oder `ko` |
| `SPOTIFY_CLIENT_ID` | Nein | Aktiviert die Auflösung von Spotify-Links über die Spotify Web API |
| `SPOTIFY_CLIENT_SECRET` | Nein | Gehört zu `SPOTIFY_CLIENT_ID`; ohne beide fallen Spotify-Links auf eine YouTube-Suche zurück |
| `AUDIO_CACHE_DIR` | Nein | Verzeichnis für den Audio-Cache (Standard: `cache/audio`) |

`.env`, Bot-Tokens, Dienst-Zugangsdaten, Cookies, heruntergeladene Medien und
der lokale `music_library/`-Cache dürfen niemals committet werden.

## Projektstruktur

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

## Umfang und verantwortungsvolle Nutzung

Dieses Projekt wird als technisches Portfolio-Beispiel bereitgestellt. Betreiber
sind selbst dafür verantwortlich, die Nutzungsbedingungen von Discord, YouTube
und anderen Diensten sowie geltendes Urheber- und Datenschutzrecht einzuhalten.
Das Projekt gewährt keine Erlaubnis, urheberrechtlich geschützte Inhalte zu
kopieren, herunterzuladen oder weiterzuverbreiten.

## Lizenz

Der Originalcode in diesem Repository ist ausschließlich unter der GNU Affero
General Public License v3.0 lizenziert. Die Hinweise zu Abhängigkeiten stehen in
[LICENSE](../../LICENSE) und [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
