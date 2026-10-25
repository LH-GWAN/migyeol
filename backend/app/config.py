"""애플리케이션 설정. .env 와 환경변수에서 읽는다 (.env.example 참고)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
KST = timezone(timedelta(hours=9))

DISCLAIMER = "보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다."
UNJUDGED_TEXT = "아직 상태 판정이 실행되지 않음"

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


def kst_today() -> date:
    return datetime.now(KST).date()


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
    bigkinds_replay_only: bool = False  # record 모드에서 녹화만 재생 (키·네트워크 없이 시연)

    # LLM
    llm_mode: Literal["stub", "rules", "live"] = "stub"  # rules: LLM 없이 규칙 어댑터 (v2 §5)
    llm_model: str = "claude-opus-5"
    anthropic_api_key: str = ""
    llm_max_calls_per_run: int = 400  # 실행당 LLM 호출 상한 (넘으면 나머지는 다음 실행으로)

    # Embedding
    embedding_backend: Literal["st", "hash"] = "hash"
    embedding_model: str = "jhgan/ko-sroberta-multitask"

    # Rules (12절 평가로 조정하는 값)
    link_auto: float = 0.55
    link_review: float = 0.40
    # 사건별 단어 가중치 (rules/weights.py). 셋 다 0 이면 이전 동작과 같다. 기본값 = 2026-10-24 라벨 스윕(docs/EXPERIMENT.md §6)
    weight_min: float = 0.15  # 강한 규칙이 아닌 기사의 weighted_overlap 하한 (게이트)
    weight_alpha: float = 0.5  # 연결 점수 = (1-α)·임베딩 코사인 + α·weighted_overlap
    weight_rule_min: float = 0.25  # 강한 규칙 기사도 이 값 미만이면 강한 규칙으로 보지 않는다 (0 = 끔)
    # 기간 창·지역 필수 조건 (link.scope_checks). LINK_SCOPE_RULES=false 면 이전 동작. 기본값 = 2026-10-25 라벨 스윕(docs/EXPERIMENT.md §7)
    link_scope_rules: bool = True
    link_early_days: int = 90  # 발생일 + N일 뒤의 기사는 제목·하이라이트에 사건 고유어(또는 약속 행동 키워드 ≥2)가 있어야 한다 (0 = 기간 창 끔)
    link_window_from_source: bool = True  # 창 기준 = max(발생일, 원천 기사(대응 발언) 발행일) — 발생 1년 뒤 복구 약속 같은 시드의 후속을 창 안에 둔다
    # 창 뒤 기사의 사건 고유어를 어디서 찾나: title_hilight | title | title_region(제목, 또는 제목에 사건 지역어가 있을 때 하이라이트)
    # 하이라이트는 검색 질의어 주변 발췌라 무관 기사에도 고유어가 남는다
    link_late_match: Literal["title_hilight", "title", "title_region"] = "title_region"
    link_unique_min: float = 0.6  # 사건 고유어 = 사건 단어 가중치가 이 값 이상인 지역·일반어 아닌 단어 (+ 시설명)
    link_region_parent_ok: bool = False  # 기초 지역 사건에서 상위 광역명 + 사건 고유어를 지역어 대신 받아 준다
    # 학습 기반 재순위 (pipeline/rerank.py, docs/EXPERIMENT.md §9). none 이면 위 임계값(link.decide) 그대로.
    # 기본 cross(2026-10-25 채택, §9·§10): 군집 정밀도·재현율 0.77/0.78 → 0.90/0.89. 교차 인코더가 없으면 features, 모델·의존성·학습 임베딩이 맞지 않으면 none
    link_reranker: Literal["none", "features", "cross"] = "cross"
    rerank_model_path: str = ""  # 비우면 backend/models/reranker_features.joblib (scripts/train_reranker.py 가 만든다)
    rerank_cross_dir: str = ""  # 비우면 backend/models/cross_encoder/ (scripts/train_cross_encoder.py --save, gitignore)
    rerank_blend: float = 0.5  # LINK_RERANKER=cross 에서 피처 분류기 확률을 섞는 비율 (0 = 교차 인코더만, 교차검증 최고 = 0.5)
    rerank_auto: float = 0.5  # 분류기 확률 ≥ 이면 자동 연결 (강한 규칙 연결도 이 값으로 다시 정한다)
    rerank_review: float = 0.2  # 분류기 확률 ≥ 이면 검토 큐, 미만 폐기
    rerank_crossfit: bool = True  # 학습에 쓴 사건은 그 사건을 뺀 fold 모델로 점수 (실험 DB 수치가 학습 재사용으로 부풀지 않게)
    # 창 뒤(발생 + LINK_EARLY_DAYS) 자동 연결 중 제목에 사건 시설명이 있는 기사는 분류기가 내리지 않는다 — 몇 년 뒤 후속(만덕~센텀 2026 개통·지반 침하)은
    # 다른 사건에서 배운 '늦은 같은 장소 기사 = 다른 사고' 경향과 구별되지 않는다
    rerank_keep_late_facility: bool = True
    followup_top_sentences: int = 3  # 규칙 후속 신호: 관련도 상위 N 문장만 본다
    followup_sentence_min: float = 0.0  # 규칙 후속 신호: 문장 관련도(임베딩 코사인 + 가중치 단어 점수) 하한
    # 규칙 후속 신호 보강 (docs/EXPERIMENT.md §10). 채택: 표현 고침 · 기준일 source · 연결 확률 바닥 0.6 / 비교만 하고 끈 것: action_match · skip_commitment · kw_alone 끔
    followup_action_match: bool = False  # 약속 대상어 겹침만 센다 — 사건 이름(시설·지역) 겹침은 약속과 같은 종류의 행동어가 문장에 있을 때만
    followup_kw_alone: bool = True  # 약속 키워드가 하나도 안 겹쳐도 사건 고유어가 무거운 문장을 신호 후보로 (rules-v2 경로)
    followup_skip_commitment: bool = False  # '…하겠다·할 계획·필요·촉구' 처럼 다짐·요구인 문장의 진행 표현은 진행 보도로 보지 않는다
    followup_expression_fixes: bool = True  # '추가 피해 최소화·예방' 은 새 문제가 아니고, '내년 2월 재개장' 처럼 앞날을 가리키는 재개장·재개통은 완료가 아니다
    # 후속 기사 기준일: grace = max(발생 + FOLLOWUP_GRACE_DAYS, 가장 이른 추적 약속 발언일) (이전) /
    # source = 원천 발언 다음 날부터, 신호가 가리킨 약속의 발언 다음 날부터 (유예 없음 — 라벨은 다음 날의 '철거 완료' 도 후속으로 본다)
    followup_cutoff: Literal["grace", "source"] = "source"
    followup_min_link_prob: float = 0.6  # 재순위 확률이 이보다 낮은 연결은 후속 근거로 쓰지 않는다 (재순위를 켠 연결만, 관리자 확정·수동 연결은 그대로)
    signal_min_conf: float = 0.6
    completion_min_conf: float = 0.7
    stale_followup_days: int = 60
    max_articles_per_event: int = 300  # 수집 총 상한 (구간별 상한과 별개)
    collect_initial_cap: int = 150  # 발생일-1 ~ +14일 구간 상한
    collect_month_cap: int = 80  # 이후 구간: 조치 상태 질의의 월별 상한 (큰 달은 상·중·하순 분할)
    collect_tail_sample: int = 40  # 이후 구간: 조치 상태 질의 밖의 일반 후속 보도 표본 (분기별, 0 이면 생략 — 추이는 time_line 이 제공)
    quotation_window_days: int = 90
    followup_grace_days: int = 7

    # App
    database_url: str = "sqlite:///./data/migyeol.db"
    pipeline_api_enabled: bool = False  # 개발·시연에서만 true (.env)
    admin_token: str = ""  # 비우면 검사 안 함 (로컬 전용). 설정하면 /api/admin/*, /api/pipeline/run 에 X-Admin-Token 필요
    scheduler_enabled: bool = False
    today_override: date | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---- v2 실험 (§4) ----
    experiment_from: date = date(2023, 1, 1)
    experiment_until: date = date(2025, 12, 31)
    incident_prefixes: str = ""  # 비우면 codes.MVP_INCIDENT_PREFIXES. 쉼표 구분으로 넓힐 수 있다 ("사고>,재해>")
    experiment_latest_path: Path = REPO_DIR / "reports" / "experiment_latest.json"
    experiment_query_hit_limit: int = 400  # 시드 질의의 전 기간 건수 상한 — 넘으면 좁힌다 (0 이면 보정 안 함). 넓은 질의는 사건당 100회 넘게 부른다
    experiment_max_articles_per_event: int = 600  # 실험 수집 총상한 (기본 파이프라인 300 과 별개)

    # ---- v2 공공기관 게시판 확인 (§3) ----
    notice_trigger_days: int = 90  # 후속보도 부족·판단 불가 사건이 이 일수 이상 조용하면 책임기관 게시판 확인
    notice_max_pages: int = 5  # 사이트당 목록 페이지 상한
    notice_request_interval_sec: float = 2.0
    notice_user_agent: str = "migyeol/0.3 (news-aftercare hackathon prototype)"
    notice_allowed_domains: str = ".go.kr,.or.kr,korea.kr"  # 쉼표 구분 접미. sites.yaml 의 도메인은 추가 허용
    notice_match_threshold: float = 0.5

    fixtures_dir: Path = BACKEND_DIR / "fixtures"
    # 비워 두면 모드로 정한다: mock 은 가상 픽스처(fixtures/seeds·gold), record/live 는 실사건(seeds/·gold/).
    # 가상 사건으로 실 API 를 부르거나, 실사건 시드가 테스트·픽스처 생성기와 섞이지 않게 하기 위함.
    seeds_file: Path | None = None
    gold_file: Path | None = None
    reports_dir: Path = REPO_DIR / "reports"

    @field_validator("seeds_file", "gold_file", "today_override", mode="before")
    @classmethod
    def _blank_is_default(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _dataset_paths_by_mode(self) -> Settings:
        base = self.fixtures_dir if self.bigkinds_mode == "mock" else BACKEND_DIR
        # 상대경로는 실행 위치와 무관하게 backend/ 기준
        self.seeds_file = BACKEND_DIR / self.seeds_file if self.seeds_file else base / "seeds" / "events.yaml"
        self.gold_file = BACKEND_DIR / self.gold_file if self.gold_file else base / "gold" / "events.yaml"
        return self

    def today(self) -> date:
        return self.today_override or kst_today()

    def today_overridden(self) -> bool:
        return self.today_override is not None

    def startup_warnings(self) -> list[str]:
        """기동 로그·/api/meta 용. 실데이터 모드에서 기준일이 과거로 고정돼 수집 범위가 잘리는 경우 등."""
        out = []
        if self.bigkinds_mode != "mock" and self.today_override and self.today_override < kst_today():
            out.append(f"TODAY_OVERRIDE={self.today_override} 가 실제 오늘보다 과거다: 수집·트렌드 범위가 그 날짜에서 잘린다 (시연 녹화 재생이 아니면 .env 에서 제거)")
        if self.bigkinds_mode == "record" and self.bigkinds_replay_only and not self.bigkinds_access_key:
            out.append("record + replay_only: 녹화에 없는 요청은 실패한다")
        return out

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
