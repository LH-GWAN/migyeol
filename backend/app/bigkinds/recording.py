"""Live 응답을 요청 해시 키로 디스크에 저장하고, 있으면 재생하는 클라이언트.

본선 개발 중 API 호출을 절약하고, PT 시연 때 네트워크 없이 동일한 결과를 보장하는 안전장치.
저장 위치: fixtures/bigkinds/recordings/<endpoint>/<sha1[:16]>.json
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .live import LiveBigKindsClient

log = logging.getLogger("migyeol.bigkinds")


def request_key(endpoint: str, argument: dict[str, Any]) -> str:
    canonical = json.dumps({"endpoint": endpoint, "argument": argument}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


class RecordingBigKindsClient(LiveBigKindsClient):
    mode = "record"

    def __init__(self, *args: Any, recordings_dir: Path, replay_only: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.recordings_dir = Path(recordings_dir)
        self.replay_only = replay_only
        self.hits = 0
        self.misses = 0

    def _path(self, endpoint: str, argument: dict[str, Any]) -> Path:
        folder = self.recordings_dir / endpoint.strip("/").replace("/", "_")
        return folder / f"{request_key(endpoint, argument)}.json"

    def _call(self, endpoint: str, argument: dict[str, Any]) -> dict[str, Any]:
        path = self._path(endpoint, argument)
        if path.exists():
            self.hits += 1
            with path.open(encoding="utf-8") as f:
                return json.load(f)["return_object"]
        if self.replay_only:
            raise FileNotFoundError(f"recording 없음 (replay_only): {path}")
        self.misses += 1
        return_object = super()._call(endpoint, argument)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "endpoint": endpoint,
                    "argument": argument,
                    "recorded_at": datetime.now().isoformat(timespec="seconds"),
                    "return_object": return_object,
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
        log.info("recorded %s -> %s", endpoint, path.name)
        return return_object
