#!/usr/bin/env python3
"""Interactive setup wizard for the Discord Music Bot.

Checks system and Python dependencies, then walks through creating the
.env configuration file.

Usage:
    python setup.py          # full interactive setup
    python setup.py --check  # dependency checks only, no prompts
"""

import ctypes.util
import getpass
import os
import re
import shutil
import subprocess
import sys
import webbrowser

REPO_URL = "https://github.com/SGT-Cho/discord_music_bot"

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")

PYTHON_PACKAGES = {
    "discord": "discord.py[voice]",
    "yt_dlp": "yt-dlp[default]",
    "dotenv": "python-dotenv",
    "nacl": "PyNaCl",
    "spotipy": "spotipy",
    "aiohttp": "aiohttp",
    "psutil": "psutil",
}

TOKEN_PATTERN = re.compile(r"^[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,}$")

OK = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"


def check_python() -> bool:
    version = sys.version_info
    if version >= (3, 11):
        print(f"  {OK} Python {version.major}.{version.minor}.{version.micro}")
        return True
    print(f"  {FAIL} Python {version.major}.{version.minor} found — 3.11 or newer is required")
    return False


def check_binary(names, purpose, hint) -> bool:
    for name in names:
        path = shutil.which(name)
        if path:
            print(f"  {OK} {name} ({purpose}) — {path}")
            return True
    print(f"  {FAIL} {'/'.join(names)} not found ({purpose})")
    print(f"      install hint: {hint}")
    return False


def check_opus() -> bool:
    if ctypes.util.find_library("opus"):
        print(f"  {OK} Opus library")
        return True
    print(f"  {WARN} Opus library not found by the system loader")
    print("      discord.py may still locate it; on macOS: brew install opus,")
    print("      on Debian/Ubuntu: apt install libopus0")
    return False


def check_python_packages() -> list:
    import importlib.util

    missing = []
    for module, package in PYTHON_PACKAGES.items():
        if importlib.util.find_spec(module) is not None:
            print(f"  {OK} {package}")
        else:
            print(f"  {FAIL} {package}")
            missing.append(package)
    return missing


def run_checks() -> tuple:
    print("\nSystem dependencies")
    ok = check_python()
    ok &= check_binary(["ffmpeg"], "audio playback", "brew install ffmpeg / apt install ffmpeg")
    check_opus()  # warning only; discord.py has its own fallback search
    ok &= check_binary(
        ["deno", "node", "bun"],
        "JavaScript runtime for yt-dlp signature processing",
        "brew install deno / apt install nodejs",
    )

    print("\nPython packages")
    missing = check_python_packages()
    return ok, missing


def check_docker() -> bool:
    print("\nDocker environment")
    docker = shutil.which("docker")
    if not docker:
        print(f"  {FAIL} docker not found")
        print("      install hint: https://docs.docker.com/get-docker/")
        return False
    print(f"  {OK} docker — {docker}")
    result = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if result.returncode == 0:
        print(f"  {OK} docker compose plugin")
        return True
    if shutil.which("docker-compose"):
        print(f"  {WARN} legacy docker-compose found — the commands below use")
        print("      'docker compose'; substitute 'docker-compose' if needed")
        return True
    print(f"  {FAIL} docker compose plugin not found")
    print("      install hint: https://docs.docker.com/compose/install/")
    return False


def prompt_mode() -> str:
    print("\nHow will you run the bot?")
    print("  1) Local  — python music_bot.py (needs ffmpeg, Opus, and a JS runtime on this machine)")
    print("  2) Docker — docker compose up -d --build (the image bundles everything; only Docker is needed)")
    while True:
        answer = input("  Choice [1/2, default 1]: ").strip().lower()
        if answer in ("", "1", "l", "local"):
            return "local"
        if answer in ("2", "d", "docker"):
            return "docker"
        print("  Please enter 1 or 2.")


def offer_pip_install(missing) -> None:
    if not missing:
        return
    answer = input(f"\nInstall {len(missing)} missing package(s) with pip now? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS]
        )
        if result.returncode != 0:
            print(f"  {FAIL} pip install failed — install manually with:")
            print(f"      {sys.executable} -m pip install -r requirements.txt")
    else:
        print("  Skipped. Install later with: python -m pip install -r requirements.txt")


def load_existing_env() -> dict:
    values = {}
    if not os.path.exists(ENV_PATH):
        return values
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def prompt_token(existing) -> str:
    current = existing.get("DISCORD_TOKEN", "")
    if current:
        print("  A Discord token is already configured.")
        answer = input("  Replace it? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return current
    while True:
        token = getpass.getpass("  Discord bot token (input hidden): ").strip()
        if not token:
            print("  A token is required to run the bot. Get one at")
            print("  https://discord.com/developers/applications")
            continue
        if not TOKEN_PATTERN.match(token):
            print(f"  {WARN} That doesn't look like a typical bot token.")
            answer = input("  Use it anyway? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                continue
        return token


def prompt_optional(label, key, existing, secret=False) -> str:
    current = existing.get(key, "")
    suffix = " [configured — Enter to keep]" if current else " [Enter to skip]"
    reader = getpass.getpass if secret else input
    value = reader(f"  {label}{suffix}: ").strip()
    return value or current


def write_env(values) -> None:
    lines = [
        "# Generated by setup.py",
        "# Required for local Discord execution",
        f'DISCORD_TOKEN="{values["DISCORD_TOKEN"]}"',
        "",
        "# Optional: used for Spotify metadata resolution",
    ]
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        if values.get(key):
            lines.append(f'{key}="{values[key]}"')
    lines.append("")
    lines.append('# Bot message language: "en" (default) or "ko"')
    lines.append(f'BOT_LANGUAGE="{values.get("BOT_LANGUAGE", "en")}"')
    lines.append("")
    lines.append("# Optional: audio cache directory (default: cache/audio)")
    if values.get("AUDIO_CACHE_DIR"):
        lines.append(f'AUDIO_CACHE_DIR="{values["AUDIO_CACHE_DIR"]}"')
    # Preserve any keys the wizard doesn't know about.
    known = {"DISCORD_TOKEN", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "AUDIO_CACHE_DIR", "BOT_LANGUAGE"}
    extras = {k: v for k, v in values.items() if k not in known and v}
    if extras:
        lines.append("")
        lines.append("# Other settings")
        lines.extend(f'{k}="{v}"' for k, v in extras.items())
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"\n  {OK} Wrote .env (permissions set to 600)")


def prompt_language(existing) -> str:
    current = existing.get("BOT_LANGUAGE", "en")
    print("\n  Bot message language (Discord responses):")
    print("    1) English")
    print("    2) 한국어 (Korean)")
    default = "2" if current == "ko" else "1"
    answer = input(f"  Choice [1/2, default {default}]: ").strip()
    return "ko" if (answer or default) == "2" else "en"


def configure_env() -> None:
    print("\nConfiguration (.env)")
    existing = load_existing_env()
    values = dict(existing)

    values["BOT_LANGUAGE"] = prompt_language(existing)
    values["DISCORD_TOKEN"] = prompt_token(existing)

    print("\n  Spotify credentials enable direct Spotify link resolution.")
    print("  Without them, Spotify links fall back to a YouTube search.")
    values["SPOTIFY_CLIENT_ID"] = prompt_optional("Spotify client ID", "SPOTIFY_CLIENT_ID", existing)
    if values["SPOTIFY_CLIENT_ID"]:
        values["SPOTIFY_CLIENT_SECRET"] = prompt_optional(
            "Spotify client secret", "SPOTIFY_CLIENT_SECRET", existing, secret=True
        )

    values["AUDIO_CACHE_DIR"] = prompt_optional(
        "Audio cache directory (default: cache/audio)", "AUDIO_CACHE_DIR", existing
    )

    write_env(values)


def star_via_gh() -> bool:
    """Star the repo through the gh CLI if it is installed and authenticated."""
    if not shutil.which("gh"):
        return False
    repo_path = REPO_URL.split("github.com/", 1)[-1].strip("/")
    result = subprocess.run(
        ["gh", "api", "-X", "PUT", f"user/starred/{repo_path}"],
        capture_output=True,
    )
    return result.returncode == 0


def offer_star() -> None:
    print("⭐ One last thing: if this bot ends up rocking your server,")
    print("   a GitHub star would absolutely make our day.")
    if "your-github-username" in REPO_URL:
        print("   (No pressure. But the star button is right there. Just saying.)")
        return
    try:
        answer = input("   Star the repo now? [y/N] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    if answer not in ("y", "yes"):
        print("   No pressure — the star button will be there when you're ready:")
        print(f"   → {REPO_URL}")
        return
    if star_via_gh():
        print(f"   {OK} Starred! You're officially our favorite person today. 💛")
        return
    print("   Opening the repo in your browser — the ⭐ button is at the top right!")
    if not webbrowser.open(REPO_URL):
        print(f"   → {REPO_URL}")


def print_outro(mode) -> None:
    print("\n" + "=" * 62)
    print("✨ Setup complete! Start the bot with:")
    if mode == "docker":
        print("     docker compose up -d --build")
    else:
        print("     python music_bot.py")
    print()
    offer_star()
    print("=" * 62)


def main() -> int:
    print("=" * 62)
    print("Discord Music Bot — setup wizard")
    print("=" * 62)

    if "--check" in sys.argv:
        ok, missing = run_checks()
        check_docker()  # informational; the Docker path needs none of the above
        print()
        if ok and not missing:
            print(f"{OK} All required dependencies for local execution found.")
            return 0
        print(f"{FAIL} Some local-execution dependencies are missing (see above).")
        print("    The Docker path only needs docker + compose.")
        return 1

    if not sys.stdin.isatty():
        print("\nNo interactive terminal detected — run with --check for a")
        print("dependency report, or copy .env.example manually.")
        return 1

    mode = prompt_mode()
    if mode == "local":
        ok, missing = run_checks()
        offer_pip_install(missing)
    else:
        if not check_docker():
            print(f"\n  {WARN} Docker isn't ready yet — continuing with .env setup.")
            print("      Install Docker before running the bot.")

    configure_env()
    print_outro(mode)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled — nothing was saved.")
        sys.exit(130)
