#!/usr/bin/env python3
"""Send a Discord message from CI.

Used by the yt-dlp canary to report its verdict. Three delivery routes, tried
in the order below; the first one that is configured wins:

1. **Webhook** — ``DISCORD_WEBHOOK``. Posts to one channel. Nothing else can be
   done with the URL if it leaks, so this is the route to prefer.

2. **Bot → channel** — ``DISCORD_BOT_TOKEN`` + ``DISCORD_CHANNEL_ID``.

3. **Bot → DM** — ``DISCORD_BOT_TOKEN`` + ``DISCORD_USER_ID``. Discord has no
   webhook that can DM, so a bot token is the only way to reach a DM.

A note on routes 2 and 3: a bot token in CI is not the same risk as a webhook
URL. A leaked webhook lets someone post to one channel; a leaked bot token lets
someone act as the bot everywhere it is present. If you want DMs, consider a
second bot that exists only to deliver these notifications, rather than handing
CI the token the music bot runs on.

Standard library only — CI needs no install step to use this.

    python tools/notify_discord.py "message text"
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_ROOT = "https://discord.com/api/v10"


def post_json(url, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "music-bot-ci (+https://github.com/SGT-Cho/discord_music_bot)")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", "replace")
        return response.status, raw


def send_via_webhook(webhook, content):
    post_json(webhook, {"content": content})
    return "webhook"


def send_via_bot(token, content, *, channel_id=None, user_id=None):
    auth = {"Authorization": f"Bot {token}"}

    if user_id:
        # A DM needs its channel opened first; Discord returns the existing one
        # if there already is a DM open with this user.
        _, raw = post_json(
            f"{API_ROOT}/users/@me/channels", {"recipient_id": str(user_id)}, auth
        )
        channel_id = json.loads(raw)["id"]
        route = "dm"
    else:
        route = "channel"

    post_json(f"{API_ROOT}/channels/{channel_id}/messages", {"content": content}, auth)
    return route


def main(argv):
    content = " ".join(argv[1:]).strip() or os.getenv("DISCORD_MESSAGE", "").strip()
    if not content:
        print("nothing to send", file=sys.stderr)
        return 2

    # Discord rejects anything longer than this outright.
    if len(content) > 2000:
        content = content[:1997] + "..."

    webhook = os.getenv("DISCORD_WEBHOOK", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
    user_id = os.getenv("DISCORD_USER_ID", "").strip()

    try:
        if webhook:
            route = send_via_webhook(webhook, content)
        elif token and (channel_id or user_id):
            route = send_via_bot(token, content, channel_id=channel_id, user_id=user_id)
        else:
            # Not an error: notifications are opt-in, and the workflow still
            # records its verdict in the run summary either way.
            print("no Discord destination configured; skipping notification")
            return 0
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        # Never echo the message body here — it is fine, but the token is in
        # the request headers and this output goes to a public build log.
        print(f"Discord rejected the message: HTTP {e.code} {detail}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"could not reach Discord: {e}", file=sys.stderr)
        return 1

    print(f"notification sent via {route}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
