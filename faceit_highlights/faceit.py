"""Minimal client for the FACEIT Data API and Downloads API."""

from __future__ import annotations

import time

import requests

DATA_API = "https://open.faceit.com/data/v4"
DOWNLOAD_API = "https://open.faceit.com/download/v2/demos/download"


class FaceitError(RuntimeError):
    pass


class FaceitClient:
    def __init__(self, api_key: str, download_token: str = ""):
        self.api_key = api_key
        self.download_token = download_token
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"

    def _request(self, method: str, url: str, token: str, **kwargs) -> dict:
        for attempt in range(5):
            resp = self.session.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kwargs
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                try:
                    wait = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 5 * 2**attempt
                time.sleep(min(wait, 120))
                continue
            if not resp.ok:
                raise FaceitError(f"{method} {url} returned HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise FaceitError(f"{method} {url} kept failing (rate limited or FACEIT is down)")

    def _get(self, path: str, **params) -> dict:
        return self._request("GET", f"{DATA_API}{path}", self.api_key, params=params)

    def player(self, nickname: str) -> dict:
        return self._get("/players", nickname=nickname)

    def recent_matches(self, player_id: str, limit: int) -> list[dict]:
        return self._get(f"/players/{player_id}/history", game="cs2", offset=0, limit=limit).get("items", [])

    def match(self, match_id: str) -> dict:
        return self._get(f"/matches/{match_id}")

    def signed_demo_url(self, resource_url: str) -> str:
        if not self.download_token:
            raise FaceitError("No Downloads API token configured")
        data = self._request("POST", DOWNLOAD_API, self.download_token, json={"resource_url": resource_url})
        return data["payload"]["download_url"]


def steam_id_of(player: dict) -> str:
    return str(player.get("games", {}).get("cs2", {}).get("game_player_id") or player.get("steam_id_64") or "")


def room_url(match_id: str) -> str:
    return f"https://www.faceit.com/en/cs2/room/{match_id}"
