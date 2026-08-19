# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md) | **Español** | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Italiano](README.it.md)

Una implementación de referencia de un bot de música para Discord, orientada a
portafolio, construida con `discord.py`, `yt-dlp` y FFmpeg.

Este repositorio contiene únicamente código fuente. No es un servicio de bot
alojado y no incluye credenciales, cookies, contenido multimedia descargado ni
un despliegue de Discord en funcionamiento.

> [!NOTE]
> Las respuestas dentro de Discord están localizadas: inglés de forma
> predeterminada y coreano con `BOT_LANGUAGE="ko"` en tu `.env`.

## Características

- Reproducción de YouTube y YouTube Music
- Resolución de metadatos de Spotify y SoundCloud hacia búsquedas en YouTube
- Detección de URL de Apple Music con búsqueda como alternativa
- Reproducción directa de URL de audio a través de FFmpeg
- Reproducción automática mediante los resultados de YouTube Mix
- Procesamiento de listas de reproducción con concurrencia limitada
- Compatibilidad con caché de audio local para despliegues privados
- Selección adaptativa de tasa de bits y ajustes manuales
- Recuperación de transmisiones y monitoreo de la conexión de voz
- Métricas de rendimiento y manejo de errores de comandos

## Comandos

| Comando | Descripción |
| --- | --- |
| `/play` | Reproduce una canción o lista de reproducción desde una URL o una búsqueda |
| `/join` | Invoca al bot a tu canal de voz |
| `/skip` | Salta la pista actual |
| `/pause` / `/resume` | Pausa o reanuda la reproducción |
| `/stop` | Detiene la reproducción y desconecta el bot |
| `/volume` | Establece el volumen de reproducción (0–100) |
| `/queue` | Muestra la cola actual |
| `/remove` | Elimina una pista de la cola según su posición |
| `/nowplaying` | Muestra detalles de la pista actual |
| `/autoplay` | Activa o desactiva la reproducción automática de pistas recomendadas |
| `/bitrate` | Establece la tasa de bits del audio (64–384 kbps) |
| `/bitrate-auto` | Ajusta automáticamente la tasa de bits al máximo del canal |
| `/performance` | Muestra métricas de rendimiento del bot |
| `/cache-info` | Muestra el estado de la caché de audio |
| `/help` | Muestra la ayuda de uso |

## Inicio rápido

### Requisitos

- Python 3.11 o más reciente
- Un ejecutable `ffmpeg` del sistema disponible en el `PATH`
- Una biblioteca Opus del sistema compatible con `discord.py`
- Deno, Node.js u otro entorno de ejecución de JavaScript compatible con yt-dlp
- Una aplicación de Discord y un token de bot para la ejecución local

En macOS, instala las herramientas del sistema con:

```bash
brew install ffmpeg opus deno
```

La dependencia `yt-dlp[default]` instala el paquete auxiliar local de EJS. Se
requiere un entorno de ejecución de JavaScript compatible durante la ejecución
para el procesamiento de firmas de YouTube; el bot finaliza con un error de
configuración claro si falta cualquiera de las dos dependencias. El proyecto no
descarga componentes ejecutables de EJS desde GitHub en tiempo de ejecución.

### Instalación y ejecución

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

¿Prefieres la configuración manual? Copia `.env.example` a `.env` y completa
los valores tú mismo (consulta [Configuración](#configuración)). `python setup.py
--check` verifica las dependencias sin modificar ningún archivo.

## Docker

La configuración de Compose incluida ejecuta el bot como un usuario no root con
un `yt-dlp` que se actualiza automáticamente a diario:

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d
```

- El audio se almacena en caché en `./music_library` en el host (montado en
  `/app/cache/audio` dentro del contenedor).
- Las actualizaciones de `yt-dlp` llegan como una imagen nueva que CI ya ha
  verificado contra YouTube real; `bin/update.sh` la descarga periódicamente.
  Consulta [docs/DEPLOYMENT.md](../DEPLOYMENT.md).
- El contenedor se ejecuta con UID/GID `1001` de forma predeterminada; puedes
  cambiarlo con los argumentos de compilación `APP_UID` / `APP_GID` para que
  coincidan con tu usuario del host.
- Un healthcheck reinicia el contenedor si se acumulan conexiones TCP.

## Configuración

Ejecuta `python setup.py` para una configuración guiada, o copia `.env.example`
a `.env` y completa solo los valores que necesites:

| Variable | Obligatoria | Descripción |
| --- | --- | --- |
| `DISCORD_TOKEN` | Sí | Token del bot obtenido en el Portal de Desarrolladores de Discord |
| `BOT_LANGUAGE` | No | Idioma de las respuestas dentro de Discord: `en` (predeterminado) o `ko` |
| `SPOTIFY_CLIENT_ID` | No | Habilita la resolución de enlaces de Spotify mediante la Spotify Web API |
| `SPOTIFY_CLIENT_SECRET` | No | Se usa junto con `SPOTIFY_CLIENT_ID`; sin ambos, los enlaces de Spotify recurren a una búsqueda en YouTube |
| `AUDIO_CACHE_DIR` | No | Directorio de la caché de audio (predeterminado: `cache/audio`) |
| `OPS_CHANNEL_ID` | No | Canal para avisos de operación (fallos de caché y yt-dlp); sin definir, solo en los registros |

Nunca subas al repositorio `.env`, tokens de bot, credenciales de servicios,
cookies, contenido multimedia descargado ni la caché local `music_library/`.

## Estructura del proyecto

```text
music_bot.py             # application entry point and Discord commands
setup.py                 # interactive setup wizard (deps check + .env)
src/audio/               # FFmpeg, bitrate, and stream recovery helpers
src/cache/               # optional local audio cache implementation
src/sources/             # source detection and metadata resolvers
src/utils/               # error handling, monitoring, and yt-dlp lifecycle
tests/                   # standalone test scripts
Dockerfile               # container image (non-root, Deno for JS challenges)
docker-compose.yml       # single-service deployment with healthcheck
bin/docker-entrypoint.sh # launches the bot
bin/update.sh            # pulls the published image and restarts
tools/ytdlp_smoke.py     # canary: checks yt-dlp against real YouTube
requirements.txt         # runtime Python dependencies
```

## Alcance y uso responsable

Este proyecto se ofrece como un ejemplo técnico de portafolio. Los operadores
son responsables de cumplir los términos de Discord, YouTube y otros servicios,
así como las leyes aplicables de derechos de autor y privacidad. El proyecto no
otorga permiso para copiar, descargar ni redistribuir contenido protegido por
derechos de autor.

## Licencia

El código original de este repositorio está licenciado únicamente bajo la GNU
Affero General Public License v3.0. Consulta [LICENSE](../../LICENSE) y
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) para los avisos sobre
dependencias.
