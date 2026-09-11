"""빅카인즈 클라이언트 팩토리. BIGKINDS_MODE=mock|record|live 하나로 선택한다."""

from __future__ import annotations

from ..config import Settings
from .protocol import BigKindsClient, BigKindsError

__all__ = ["BigKindsClient", "BigKindsError", "build_client"]


def build_client(settings: Settings) -> BigKindsClient:
    if settings.bigkinds_mode == "mock":
        from .mock import MockBigKindsClient

        return MockBigKindsClient(settings.fixtures_dir / "bigkinds")
    if settings.bigkinds_mode == "record":
        from .recording import RecordingBigKindsClient

        return RecordingBigKindsClient(
            settings.bigkinds_access_key,
            settings.bigkinds_base_url,
            rps=settings.bigkinds_rps,
            recordings_dir=settings.fixtures_dir / "bigkinds" / "recordings",
        )
    from .live import LiveBigKindsClient

    return LiveBigKindsClient(settings.bigkinds_access_key, settings.bigkinds_base_url, rps=settings.bigkinds_rps)
