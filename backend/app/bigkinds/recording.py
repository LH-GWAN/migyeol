"""Live 응답을 요청 해시 키로 디스크에 저장하고, 있으면 재생하는 클라이언트.

본선 개발 중 API 호출을 절약하고, PT 시연 때 네트워크 없이 동일한 결과를 보장하는 안전장치.
- 저장 위치: fixtures/bigkinds/recordings/<endpoint>/<sha1[:16]>.json (임시 파일 뒤 원자적 교체)
- 요청 키에는 published_at.until 같은 '오늘 의존' 값이 들어간다. 시연 재생은 녹화 때와 같은 TODAY_OVERRIDE 로 고정한다.
- replay_only=True 면 키 없이도 재생만 하고, 녹화에 없는 요청은 BigKindsError 로 바로 실패시키며 misses 에 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .live import LiveBigKindsClient
from .protocol import BigKindsError

log = logging.getLogger("migyeol.bigkinds")


def request_key(endpoint: str, argument: dict[str, Any]) -> str:
    canonical = json.dumps({"endpoint": endpoint, "argument": argument}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


class RecordingBigKindsClient(LiveBigKindsClient):
    mode = "record"

    def __init__(self, *args: Any, recordings_dir: Path, replay_only: bool = False, **kwargs: Any):
        super().__init__(*args, require_key=not replay_only, **kwargs)
        self.recordings_dir = Path(recordings_dir)
        self.replay_only = replay_only
        self.hits = 0
        self.misses = 0
        self.missing: list[dict[str, Any]] = []

    def _path(self, endpoint: str, argument: dict[str, Any]) -> Path:
        folder = self.recordings_dir / endpoint.strip("/").replace("/", "_")
        return folder / f"{request_key(endpoint, argument)}.json"

    def _call(self, endpoint: str, argument: dict[str, Any]) -> dict[str, Any]:
        path = self._path(endpoint, argument)
        if path.exists():
            try:
                with path.open(encoding="utf-8") as f:
                    self.hits += 1
                    return json.load(f)["return_object"]
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                log.warning("깨진 녹화 파일 %s (%s) — 다시 녹화", path.name, exc)
        if self.replay_only or not self.access_key:
            self.misses += 1
            self.missing.append({"endpoint": endpoint, "key": path.stem, "query": argument.get("query") or argument.get("date") or argument.get("from")})
            raise BigKindsError(endpoint, None, f"녹화 없음 (replay_only): {path.name} query={self.missing[-1]['query']!r}")
        self.misses += 1
        return_object = super()._call(endpoint, argument)
        self._save(path, endpoint, argument, return_object)
        log.info("recorded %s -> %s", endpoint, path.name)
        return return_object

    @staticmethod
    def _save(path: Path, endpoint: str, argument: dict[str, Any], return_object: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "endpoint": endpoint,
            "argument": argument,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "return_object": return_object,
        }
        fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
