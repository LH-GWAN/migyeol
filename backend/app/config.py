"""애플리케이션 설정. .env 와 환경변수에서 읽는다 (.env.example 참고)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

DISCLAIMER = "보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다."

# 5개 상태 문자열. 이 밖의 값은 존재하지 않는다.
STATUS_DONE = "조치 완료 근거 확인"
STATUS_IN_PROGRESS = "조치 진행 정황 확인"
STATUS_NEW_PROBLEM = "새로운 문제 발생"
STATUS_NO_FOLLOWUP = "후속보도 부족"
STATUS_UNDETERMINED = "판단 불가"
STATUSES: tuple[str, ...] = (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_NEW_PROBLEM,
    STATUS_NO_FOLLOWUP,
    STATUS_UNDETERMINED,
)
NEEDS_CHECK_STATUSES = frozenset({STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED, STATUS_NEW_PROBLEM})

PHASES: tuple[str, ...] = ("발생", "원인조사", "대응발표", "조치진행", "결과확인", "기타")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # BigKinds
    bigkinds_mode: Literal["mock", "record", "live"] = "mock"
    bigkinds_access_key: str = ""
    bigkinds_base_url: str = "https://tools.kinds.or.kr"
    bigkinds_rps: float = 2.0

    # LLM
    llm_mode: Literal["stub", "live"] = "stub"
    llm_model: str = "claude-opus-5"
    anthropic_api_key: str = ""

    # Embedding
    embedding_backend: Literal["st", "hash"] = "hash"
    embedding_model: str = "jhgan/ko-sroberta-multitask"

    # Rules (12절 평가로 조정하는 값)
    link_auto: float = 0.55
    link_review: float = 0.40
    signal_min_conf: float = 0.6
    completion_min_conf: float = 0.7
    stale_followup_days: int = 60
    max_articles_per_event: int = 300
    quotation_window_days: int = 90
    followup_grace_days: int = 7

    # App
    database_url: str = "sqlite:///./data/migyeol.db"
    pipeline_api_enabled: bool = True
    scheduler_enabled: bool = False
    today_override: str = ""
    cors_origins: str = "http://localhost:3000"

    fixtures_dir: Path = BACKEND_DIR / "fixtures"
    seeds_file: Path = BACKEND_DIR / "seeds" / "events.yaml"
    gold_file: Path = BACKEND_DIR / "gold" / "events.yaml"
    reports_dir: Path = REPO_DIR / "reports"

    def today(self) -> date:
        if self.today_override:
            return date.fromisoformat(self.today_override)
        return date.today()

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
