"""민간 지원 소식 테이블. init_db 가 이 모듈을 import 하므로 create_all 에 포함된다.

- support_mentions : 사건에 연결된 기사에서 규칙으로 뽑은 '민간 주체의 지원 언급' 한 건 (같은 기사에 주체가 여럿이면 여러 행).
  (event_id, news_id, actor) 유일. 상태 판정과 무관한 보조 근거.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.models import Base, _now

ACTOR_KINDS = ("기업", "단체", "개인")
ACTOR_ROLES = ("당사자", "제3자")


class SupportMention(Base):
    __tablename__ = "support_mentions"
    __table_args__ = (UniqueConstraint("event_id", "news_id", "actor", name="uq_support_mention"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    news_id: Mapped[str] = mapped_column(ForeignKey("articles.news_id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(100), default="")  # 기업·단체 이름. 개인은 이름을 저장하지 않고 '개인'
    actor_kind: Mapped[str] = mapped_column(String(8), default="기업")  # 기업|단체|개인
    actor_role: Mapped[str] = mapped_column(String(8), default="제3자")  # 당사자|제3자 — 사건 설명(제목·요약·책임기관·시설)에 이름이 있으면 당사자
    support_terms: Mapped[list] = mapped_column(JSON, default=list)  # 문장에서 맞은 지원 어휘
    evidence_span: Mapped[str] = mapped_column(String(200), default="")  # 주체와 어휘가 함께 있는 문장 (≤200자, 메타데이터 범위)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
