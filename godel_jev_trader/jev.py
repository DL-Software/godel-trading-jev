"""TypeSafe Jev client: one `POST /v1/systemone` call, typed answers back.

Docs: https://docs.typesafe.ai
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"


@dataclass(frozen=True)
class JevResponse:
    answers: dict
    model: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0


class Jev:
    def __init__(self, api_key: str | None = None, model: str = "jev-latest", url: str | None = None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise SystemExit("TYPESAFE_API_KEY is not set. Get a key at https://console.typesafe.ai")
        self.model = model
        self.url = url or os.environ.get("TYPESAFE_API_URL") or DEFAULT_URL
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            timeout=60,
        )

    def ask(self, state: dict | str | list, questions: dict) -> JevResponse:
        """Evaluate every question against `state` in one call.

        Jev evaluates questions in parallel and in isolation, so adding questions costs little
        latency. 429/529 are retried with exponential backoff, as the docs recommend.
        """
        body = {"model": self.model, "state": state, "questions": questions}
        delay = 0.5
        for attempt in range(6):
            t0 = time.perf_counter()
            r = self._client.post(self.url, json=body)
            if r.status_code in (429, 529) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            d = r.json()
            return JevResponse(
                answers=d["answers"],
                model=d.get("model", self.model),
                usage=d.get("usage", {}),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        raise RuntimeError("unreachable")
