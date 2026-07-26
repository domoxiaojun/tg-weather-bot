#!/usr/bin/env python
"""Container healthcheck: is the token still valid and Telegram reachable?

This does not prove the polling loop is healthy, but it catches a revoked
token, broken DNS/egress and a wedged container — the failures that otherwise
look like "the bot is just quiet".
"""

import os
import sys

import httpx


def main() -> int:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN is not set")
        return 1
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        payload = response.json()
    except Exception as error:
        print(f"getMe failed: {type(error).__name__}: {error}")
        return 1

    if not payload.get("ok"):
        print(f"getMe rejected: {payload.get('description')}")
        return 1
    print(f"ok: @{payload['result'].get('username')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
