from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


class BilibiliLiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class DanmakuMessage:
    nickname: str
    text: str
    timeline: str
    ts: float
    raw: dict[str, Any]


def _parse_timeline_ts(timeline: str) -> float:
    s = str(timeline or "").strip()
    if not s:
        return 0.0
    try:
        # Common format: "YYYY-MM-DD HH:MM:SS"
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0.0


class BilibiliLivePoller:
    """
    Minimal polling implementation based on:
      https://api.live.bilibili.com/ajax/msg?roomid=<room_id>

    Note: This is a lightweight approach (not the official danmaku websocket).
    """

    def __init__(
        self,
        *,
        room_id: int,
        user_agent: str | None = None,
        base_url: str = "https://api.live.bilibili.com/ajax/msg",
        dedupe_size: int = 512,
    ) -> None:
        self.room_id = int(room_id)
        self.base_url = str(base_url).strip().rstrip("/")
        self.user_agent = (user_agent or "").strip() or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        self._last_seen_ts = time.time()
        self._dedupe_size = int(dedupe_size)
        self._dedupe: list[tuple[str, str, str]] = []

    def _seen(self, key: tuple[str, str, str]) -> bool:
        return key in self._dedupe

    def _mark_seen(self, key: tuple[str, str, str]) -> None:
        self._dedupe.append(key)
        if len(self._dedupe) > self._dedupe_size:
            self._dedupe = self._dedupe[-self._dedupe_size :]

    def fetch(self, *, timeout_s: int = 10) -> list[DanmakuMessage]:
        url = f"{self.base_url}?roomid={self.room_id}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent}, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if getattr(e, "fp", None) else ""
            raise BilibiliLiveError(f"HTTP {e.code} from bilibili ajax/msg: {body[:200]}") from e
        except Exception as e:
            raise BilibiliLiveError(f"Failed to fetch bilibili ajax/msg: {e}") from e

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise BilibiliLiveError(f"Invalid JSON from bilibili ajax/msg: {payload[:200]}") from e

        room = None
        try:
            room = data["data"]["room"]
        except Exception:
            room = None

        if not isinstance(room, list):
            return []

        out: list[DanmakuMessage] = []
        for item in room:
            if not isinstance(item, dict):
                continue
            nickname = str(item.get("nickname", "") or "").strip()
            text = str(item.get("text", "") or "").strip()
            timeline = str(item.get("timeline", "") or "").strip()
            if not nickname or not text:
                continue
            ts = _parse_timeline_ts(timeline)
            out.append(DanmakuMessage(nickname=nickname, text=text, timeline=timeline, ts=ts, raw=item))

        return out

    def poll_new(self) -> list[DanmakuMessage]:
        messages = self.fetch()
        new: list[DanmakuMessage] = []
        max_ts = self._last_seen_ts

        for m in messages:
            key = (m.timeline, m.nickname, m.text)
            if self._seen(key):
                continue

            # Prefer timeline ordering, but also allow same-second messages via dedupe.
            if m.ts and m.ts < self._last_seen_ts:
                continue

            self._mark_seen(key)
            new.append(m)
            if m.ts and m.ts > max_ts:
                max_ts = m.ts

        self._last_seen_ts = max_ts
        return new


def iter_poll(
    poller: BilibiliLivePoller,
    *,
    interval_s: float = 2.0,
    stop_after_s: float = 0.0,
) -> Iterable[DanmakuMessage]:
    start = time.time()
    while True:
        for m in poller.poll_new():
            yield m
        if stop_after_s and (time.time() - start) >= stop_after_s:
            return
        time.sleep(float(interval_s))
