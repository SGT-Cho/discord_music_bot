# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

[English](../../README.md) | [한국어](README.ko.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md) | [Español](README.es.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | **Français** | [Italiano](README.it.md)

Une implémentation de référence de bot musical Discord à vocation de
portfolio, construite avec `discord.py`, `yt-dlp` et FFmpeg.

Ce dépôt contient uniquement du code source. Il ne s'agit pas d'un service de
bot hébergé et il n'inclut ni identifiants, ni cookies, ni médias téléchargés,
ni déploiement Discord en fonctionnement.

> [!NOTE]
> Les réponses dans Discord sont localisées : anglais par défaut, coréen avec
> `BOT_LANGUAGE="ko"` dans votre fichier `.env`.

## Fonctionnalités

- Lecture depuis YouTube et YouTube Music
- Résolution des métadonnées Spotify et SoundCloud vers des recherches YouTube
- Détection des URL Apple Music avec recherche de repli
- Lecture d'URL audio directes via FFmpeg
- Lecture automatique via les résultats YouTube Mix
- Traitement des playlists avec concurrence bornée
- Prise en charge d'un cache audio local pour les déploiements privés
- Sélection adaptative du débit binaire et réglages manuels
- Récupération des flux et surveillance de la connexion vocale
- Métriques de performance et gestion des erreurs de commandes

## Commandes

| Commande | Description |
| --- | --- |
| `/play` | Lire un morceau ou une playlist à partir d'une URL ou d'une recherche |
| `/join` | Faire venir le bot dans votre salon vocal |
| `/skip` | Passer le morceau en cours |
| `/pause` / `/resume` | Mettre en pause ou reprendre la lecture |
| `/stop` | Arrêter la lecture et déconnecter le bot |
| `/volume` | Régler le volume de lecture (0–100) |
| `/queue` | Afficher la file d'attente actuelle |
| `/remove` | Retirer un morceau de la file d'attente par sa position |
| `/nowplaying` | Afficher les détails du morceau en cours |
| `/autoplay` | Activer ou désactiver la lecture automatique des morceaux recommandés |
| `/bitrate` | Régler le débit binaire audio (64–384 kbps) |
| `/bitrate-auto` | S'aligner automatiquement sur le débit maximal du salon |
| `/performance` | Afficher les métriques de performance du bot |
| `/cache-info` | Afficher l'état du cache audio |
| `/help` | Afficher l'aide d'utilisation |

## Démarrage rapide

### Prérequis

- Python 3.11 ou plus récent
- Un exécutable `ffmpeg` système présent dans le `PATH`
- Une bibliothèque Opus système prise en charge par `discord.py`
- Deno, Node.js ou un autre environnement d'exécution JavaScript pris en charge par yt-dlp
- Une application Discord et un jeton de bot pour l'exécution locale

Sous macOS, installez les outils système avec :

```bash
brew install ffmpeg opus deno
```

La dépendance `yt-dlp[default]` installe le paquet d'assistance EJS local. Un
environnement d'exécution JavaScript pris en charge est requis à l'exécution
pour le traitement des signatures YouTube ; le bot s'arrête avec un message
d'erreur de configuration explicite si l'une de ces dépendances manque. Le
projet ne télécharge pas de composants EJS exécutables depuis GitHub à
l'exécution.

### Installation et lancement

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

Vous préférez une configuration manuelle ? Copiez `.env.example` vers `.env`
et renseignez vous-même les valeurs (voir [Configuration](#configuration)).
`python setup.py --check` vérifie les dépendances sans toucher à aucun
fichier.

## Docker

La configuration Compose fournie exécute le bot en tant qu'utilisateur
non-root avec un `yt-dlp` qui se met à jour quotidiennement :

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d --build
```

- L'audio est mis en cache dans `./music_library` sur l'hôte (monté sur
  `/app/cache/audio` dans le conteneur).
- `supercronic` met à niveau `yt-dlp` chaque jour à 18h15 UTC et redémarre le
  bot afin que les correctifs des extracteurs soient appliqués sans
  intervention manuelle.
- Le conteneur s'exécute par défaut avec l'UID/GID `1001` ; remplacez-les via
  les arguments de build `APP_UID` / `APP_GID` pour correspondre à votre
  utilisateur hôte.
- Un healthcheck redémarre le conteneur si les connexions TCP s'accumulent.

## Configuration

Lancez `python setup.py` pour une configuration guidée, ou copiez
`.env.example` vers `.env` et ne renseignez que les valeurs dont vous avez
besoin :

| Variable | Requis | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Oui | Jeton du bot obtenu depuis le portail développeur Discord |
| `BOT_LANGUAGE` | Non | Langue des réponses dans Discord : `en` (par défaut) ou `ko` |
| `SPOTIFY_CLIENT_ID` | Non | Active la résolution des liens Spotify via l'API Web Spotify |
| `SPOTIFY_CLIENT_SECRET` | Non | Associé à `SPOTIFY_CLIENT_ID` ; sans les deux, les liens Spotify se rabattent sur une recherche YouTube |
| `AUDIO_CACHE_DIR` | Non | Répertoire du cache audio (par défaut : `cache/audio`) |

Ne validez jamais dans le dépôt le fichier `.env`, les jetons de bot, les
identifiants de services, les cookies, les médias téléchargés ni le cache
local `music_library/`.

## Structure du projet

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

## Périmètre et usage responsable

Ce projet est fourni à titre d'exemple technique de portfolio. Les opérateurs
sont responsables du respect des conditions d'utilisation de Discord, de
YouTube et des autres services, ainsi que des lois applicables en matière de
droit d'auteur et de protection de la vie privée. Le projet n'accorde aucune
autorisation de copier, télécharger ou redistribuer des contenus protégés par
le droit d'auteur.

## Licence

Le code original de ce dépôt est publié sous la licence GNU Affero General
Public License v3.0 uniquement. Consultez [LICENSE](../../LICENSE) et
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) pour les mentions
relatives aux dépendances.
