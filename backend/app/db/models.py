"""SQLAlchemy 2.x 모델 (개발 프롬프트 6절 데이터 모델)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ..config import STATUS_UNDETERMINED


def _now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    incident_type: Mapped[str] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    facility: Mapped[str] = mapped_column(String(100), default="")
    responsible_org: Mapped[str] = mapped_column(String(100), default="")
    org_aliases: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    expansions: Mapped[list] = mapped_column(JSON, default=list)  # 연관어 분석으로 얻은 보강 표현
    promise_terms: Mapped[list] = mapped_column(JSON, default=list)  # 추출된 약속의 행동 키워드
    occurred_at: Mapped[date] = mapped_column(Date, index=True)
    seed_query: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(32), default=STATUS_UNDETERMINED, index=True)
    status_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    last_followup_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    followup_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    links: Mapped[list["EventArticle"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    quotations: Mapped[list["Quotation"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    promises: Mapped[list["Promise"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    signals: Mapped[list["FollowupSignal"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    judgments: Mapped[list["StatusJudgment"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    trends: Mapped[list["TrendSnapshot"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    reports: Mapped[list["Report"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Article(Base):
    __tablename__ = "articles"

    news_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[list] = mapped_column(JSON, default=list)
    category_incident: Mapped[list] = mapped_column(JSON, default=list)
    hilight: Mapped[str | None] = mapped_column(String(400), nullable=True)  # API 한도 200자
    content_200: Mapped[str | None] = mapped_column(String(400), nullable=True)  # API 한도 200자
    tms_keywords: Mapped[list] = mapped_column(JSON, default=list)
    byline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_link_page: Mapped[str | None] = mapped_column(String(500), nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_backend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    change_status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|updated|cancelled
    change_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    links: Mapped[list["EventArticle"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class EventArticle(Base):
    __tablename__ = "event_articles"

    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    news_id: Mapped[str] = mapped_column(ForeignKey("articles.news_id", ondelete="CASCADE"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), default="기타")
    link_method: Mapped[str] = mapped_column(String(16), default="rule")  # seed|rule|embedding|manual
    link_score: Mapped[float] = mapped_column(Float, default=0.0)
    link_reason: Mapped[str] = mapped_column(String(300), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # 관리자 확정 (재평가 안 함)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)  # 저신뢰 연결 (검토 큐)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)  # 관리자 해제 (재연결 안 함)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped["Event"] = relationship(back_populates="links")
    article: Mapped["Article"] = relationship(back_populates="links")


class Quotation(Base):
    __tablename__ = "quotations"
    __table_args__ = (UniqueConstraint("event_id", "news_id", "source", "quotation", name="uq_quotation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    news_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(200), default="")
    quotation: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="")
    is_public_actor: Mapped[bool] = mapped_column(Boolean, default=False)

    event: Mapped["Event"] = relationship(back_populates="quotations")
    promises: Mapped[list["Promise"]] = relationship(back_populates="quotation")


class Promise(Base):
    __tablename__ = "promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    quotation_id: Mapped[int | None] = mapped_column(ForeignKey("quotations.id", ondelete="SET NULL"), nullable=True)
    actor_org: Mapped[str] = mapped_column(String(100), default="")
    actor_raw: Mapped[str] = mapped_column(String(200), default="")
    action: Mapped[str] = mapped_column(Text, default="")
    deadline_raw: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deadline_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_precision: Mapped[str] = mapped_column(String(8), default="none")  # day|month|year|none
    strength: Mapped[str] = mapped_column(String(8), default="해당없음")  # 확약|계획|의사표명|해당없음
    is_trackable: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_news_id: Mapped[str] = mapped_column(String(64), default="")
    merged_news_ids: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped["Event"] = relationship(back_populates="promises")
    quotation: Mapped["Quotation | None"] = relationship(back_populates="promises")


class FollowupSignal(Base):
    __tablename__ = "followup_signals"
    __table_args__ = (UniqueConstraint("event_id", "news_id", "prompt_version", name="uq_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    promise_id: Mapped[int | None] = mapped_column(ForeignKey("promises.id", ondelete="SET NULL"), nullable=True)
    news_id: Mapped[str] = mapped_column(String(64), index=True)
    signal: Mapped[str] = mapped_column(String(16), default="불명확")  # 완료|진행|새로운문제|무관|불명확
    matches_promise: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_span: Mapped[str] = mapped_column(String(200), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped["Event"] = relationship(back_populates="signals")


class StatusJudgment(Base):
    __tablename__ = "status_judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64), default="")
    reason_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{news_id, span, signal, confidence}]
    unresolved_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    judged_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped["Event"] = relationship(back_populates="judgments")


class TrendSnapshot(Base):
    __tablename__ = "trend_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    interval: Mapped[str] = mapped_column(String(8), default="month")
    series: Mapped[list] = mapped_column(JSON, default=list)  # [{label, hits}]
    peak_label: Mapped[str | None] = mapped_column(String(8), nullable=True)
    peak_hits: Mapped[int] = mapped_column(Integer, default=0)
    recent_mean: Mapped[float] = mapped_column(Float, default=0.0)
    drop_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    event: Mapped["Event"] = relationship(back_populates="trends")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    news_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(16))  # wrong_link|wrong_status|wrong_promise|other
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    event: Mapped["Event"] = relationship(back_populates="reports")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
