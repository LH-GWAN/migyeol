"""v2 §8 공공기관 게시판 확인 테이블 — 트랙 C. init_db 가 이 모듈을 import 하므로 여기 정의한 모델은 create_all 에 포함된다.

- org_notices   : 트리거된 사건에 대해 책임기관 공식 게시판에서 제목이 매칭된 항목 (제목·게시일·URL·본문 첫 300자)
- notice_checks : 사건별 확인 실행 기록 (트리거 여부·사유·확인한 게시판·찾은 항목 수). 트리거되지 않은 확인도 남긴다
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.models import Base, _now


class OrgNotice(Base):
    __tablename__ = "org_notices"
    __table_args__ = (UniqueConstraint("event_id", "url", name="uq_org_notice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    org: Mapped[str] = mapped_column(String(100), default="")  # 정규화 기관 (canonical_org)
    site_key: Mapped[str] = mapped_column(String(64), default="")  # sites.yaml 의 key
    site_url: Mapped[str] = mapped_column(String(500), default="")  # 게시판 목록 URL (1쪽)
    title: Mapped[str] = mapped_column(String(300), default="")
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    url: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(String(400), default="")  # 본문 첫 300자 (첨부·이미지·전체 본문은 저장하지 않는다)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_terms: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NoticeCheck(Base):
    __tablename__ = "notice_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(16), default="")  # not_triggered|no_site|found|not_found|error
    sites_checked: Mapped[list] = mapped_column(JSON, default=list)  # [{key, org, url, pages, items, error}]
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)  # 실제로 보낸 HTTP 요청 수 (robots.txt 포함)
