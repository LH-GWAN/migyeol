"""DB 엔진/세션. sqlite 상대 경로는 backend/ 기준으로 해석한다.

- SQLite 는 WAL + busy_timeout 으로 배치 중에도 신고·관리자 쓰기가 잠금 오류로 끝나지 않게 한다.
- 모델에 컬럼이 추가되면 기동 시 누락 컬럼을 ALTER TABLE ADD COLUMN 으로 보충한다(간이 마이그레이션). --reset 없이도 동작한다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import BACKEND_DIR, get_settings
from .models import Base

log = logging.getLogger("migyeol.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def resolve_database_url(url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and url != "sqlite:///:memory:":
        rel = url[len(prefix) :]
        path = (BACKEND_DIR / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"
    return url


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    if _engine is None or url is not None:
        resolved = resolve_database_url(url or get_settings().database_url)
        _engine = create_engine(resolved, future=True, connect_args={"check_same_thread": False} if "sqlite" in resolved else {})
        if "sqlite" in resolved:

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                if ":memory:" not in resolved:
                    cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def migrate_missing_columns(engine: Engine) -> list[str]:
    """모델에는 있고 테이블에는 없는 컬럼을 추가한다 (nullable 또는 기본값 있는 컬럼만). 반환: 추가한 'table.column' 목록."""
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v)}" if isinstance(v, bool) else (f" DEFAULT {v!r}" if isinstance(v, (int, float, str)) else "")
                if not col.nullable and not default:
                    raise RuntimeError(f"컬럼 {table.name}.{col.name} 은 NOT NULL 이고 기본값이 없어 자동 추가할 수 없다. --reset 또는 수동 마이그레이션 필요")
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.warning("DB 컬럼 추가(간이 마이그레이션): %s", added)
    return added


def init_db(url: str | None = None, drop: bool = False) -> Engine:
    engine = get_engine(url)
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if not drop:
        migrate_missing_columns(engine)
    return engine


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def db_path() -> Path | None:
    url = resolve_database_url(get_settings().database_url)
    return Path(url.removeprefix("sqlite:///")) if url.startswith("sqlite:///") else None
