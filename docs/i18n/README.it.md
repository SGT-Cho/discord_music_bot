# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md) | [Español](README.es.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | **Italiano**

Un'implementazione di riferimento di un bot musicale per Discord, pensata come
progetto da portfolio e realizzata con `discord.py`, `yt-dlp` e FFmpeg.

Questo repository contiene esclusivamente codice sorgente. Non è un servizio
di bot in hosting e non include credenziali, cookie, contenuti multimediali
scaricati né un deployment Discord in esecuzione.

> [!NOTE]
> Le risposte all'interno di Discord sono localizzate: inglese per impostazione
> predefinita, coreano con `BOT_LANGUAGE="ko"` nel tuo `.env`.

## Funzionalità

- Riproduzione da YouTube e YouTube Music
- Risoluzione dei metadati di Spotify e SoundCloud in ricerche su YouTube
- Rilevamento degli URL di Apple Music con fallback alla ricerca
- Riproduzione di URL audio diretti tramite FFmpeg
- Riproduzione automatica tramite i risultati di YouTube Mix
- Elaborazione delle playlist con concorrenza limitata
- Supporto per la cache audio locale nei deployment privati
- Selezione adattiva del bitrate e regolazioni manuali
- Ripristino dello stream e monitoraggio della connessione vocale
- Metriche delle prestazioni e gestione degli errori dei comandi

## Comandi

| Comando | Descrizione |
| --- | --- |
| `/play` | Riproduce un brano o una playlist da un URL o da una query di ricerca |
| `/join` | Richiama il bot nel tuo canale vocale |
| `/skip` | Salta il brano corrente |
| `/pause` / `/resume` | Mette in pausa o riprende la riproduzione |
| `/stop` | Interrompe la riproduzione e disconnette il bot |
| `/volume` | Imposta il volume di riproduzione (0–100) |
| `/queue` | Mostra la coda corrente |
| `/remove` | Rimuove un brano dalla coda in base alla posizione |
| `/nowplaying` | Mostra i dettagli del brano corrente |
| `/autoplay` | Attiva o disattiva la riproduzione automatica dei brani consigliati |
| `/bitrate` | Imposta il bitrate audio (64–384 kbps) |
| `/bitrate-auto` | Adegua automaticamente il bitrate al massimo del canale |
| `/performance` | Mostra le metriche delle prestazioni del bot |
| `/cache-info` | Mostra lo stato della cache audio |
| `/help` | Mostra la guida all'uso |

## Avvio rapido

### Requisiti

- Python 3.11 o successivo
- Un eseguibile `ffmpeg` di sistema disponibile nel `PATH`
- Una libreria Opus di sistema supportata da `discord.py`
- Deno, Node.js o un altro runtime JavaScript supportato da yt-dlp
- Un'applicazione Discord e un token del bot per l'esecuzione locale

Su macOS, installa gli strumenti di sistema con:

```bash
brew install ffmpeg opus deno
```

La dipendenza `yt-dlp[default]` installa il pacchetto helper EJS locale. Un
runtime JavaScript supportato è necessario in fase di esecuzione per
l'elaborazione delle firme di YouTube; se una delle due dipendenze manca, il
bot termina con un chiaro errore di configurazione. Il progetto non scarica
componenti EJS eseguibili da GitHub in fase di esecuzione.

### Installazione ed esecuzione

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

Preferisci la configurazione manuale? Copia `.env.example` in `.env` e
compila i valori manualmente (vedi [Configurazione](#configurazione)).
`python setup.py --check` verifica le dipendenze senza toccare alcun file.

## Docker

La configurazione Compose inclusa esegue il bot come utente non root, con un
`yt-dlp` che si aggiorna automaticamente ogni giorno:

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- L'audio viene memorizzato nella cache in `./music_library` sull'host
  (montata su `/app/cache/audio` nel container).
- `supercronic` aggiorna `yt-dlp` ogni giorno alle 18:15 UTC e riavvia il bot,
  così le correzioni degli extractor arrivano senza intervento manuale.
- Per impostazione predefinita il container viene eseguito con UID/GID `1001`;
  sovrascrivi i build arg `APP_UID` / `APP_GID` per farli corrispondere al tuo
  utente host.
- Un healthcheck riavvia il container se le connessioni TCP si accumulano.

## Configurazione

Esegui `python setup.py` per una configurazione guidata, oppure copia
`.env.example` in `.env` e compila solo i valori di cui hai bisogno:

| Variabile | Obbligatoria | Descrizione |
| --- | --- | --- |
| `DISCORD_TOKEN` | Sì | Token del bot dal Discord Developer Portal |
| `BOT_LANGUAGE` | No | Lingua delle risposte all'interno di Discord: `en` (predefinita) o `ko` |
| `SPOTIFY_CLIENT_ID` | No | Abilita la risoluzione dei link Spotify tramite la Spotify Web API |
| `SPOTIFY_CLIENT_SECRET` | No | Da usare insieme a `SPOTIFY_CLIENT_ID`; in mancanza di entrambi, i link Spotify ripiegano su una ricerca YouTube |
| `AUDIO_CACHE_DIR` | No | Directory della cache audio (predefinita: `cache/audio`) |

Non committare mai `.env`, i token del bot, le credenziali di servizio, i
cookie, i contenuti multimediali scaricati o la cache locale `music_library/`.

## Struttura del progetto

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

## Ambito e uso responsabile

Questo progetto è fornito come esempio tecnico da portfolio. Gli operatori
sono responsabili del rispetto dei termini di servizio di Discord, YouTube e
degli altri servizi, nonché delle leggi applicabili in materia di diritto
d'autore e privacy. Il progetto non concede alcuna autorizzazione a copiare,
scaricare o ridistribuire contenuti protetti da diritto d'autore.

## Licenza

Il codice originale di questo repository è distribuito esclusivamente secondo
i termini della GNU Affero General Public License v3.0. Consulta
[LICENSE](../../LICENSE) e [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)
per gli avvisi relativi alle dipendenze.
