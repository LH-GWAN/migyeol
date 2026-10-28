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
                    try:
                        cur.execute("PRAGMA journal_mode=WAL")
                    except Exception as exc:  # noqa: BLE001 — 동시 첫 기동에서 journal 전환이 잠기면 다음 연결이 한다
                        log.warning("journal_mode=WAL 전환 실패(%s) — 이번 연결은 기본 모드로", exc)
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def _column_default_sql(col) -> str | None:
    """ALTER TABLE ADD COLUMN 에 쓸 DEFAULT 절. 없으면 None.

    스칼라 기본값은 그대로, JSON 컬럼의 list/dict 콜러블 기본값은 '[]'/'{}' 로 둔다 (ORM 의 default=list 와 같은 뜻).
    """
    d = col.default
    if d is None:
        return None
    if getattr(d, "is_scalar", False):
        v = d.arg
        if isinstance(v, bool):
            return f" DEFAULT {int(v)}"
        if isinstance(v, (int, float)):
            return f" DEFAULT {v}"
        if isinstance(v, str):
            return " DEFAULT '" + v.replace("'", "''") + "'"
        return None
    if getattr(d, "is_callable", False):
        arg = getattr(d, "arg", None)
        raw = getattr(arg, "__wrapped__", arg)
        if raw is list:
            return " DEFAULT '[]'"
        if raw is dict:
            return " DEFAULT '{}'"
    return None


def migrate_missing_columns(engine: Engine) -> list[str]:
    """모델에는 있고 테이블에는 없는 컬럼을 추가한다 (nullable 또는 기본값 있는 컬럼만). 반환: 추가한 'table.column' 목록.

    SQLite 의 DDL 은 롤백되지 않으므로, 전체를 먼저 검사해 추가할 수 없는 컬럼이 하나라도 있으면 아무것도 바꾸지 않고 실패한다.
    """
    insp = inspect(engine)
    plan: list[tuple[str, str, str]] = []
    blocked: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(engine.dialect)
            default = _column_default_sql(col) or ""
            if not col.nullable and not default:
                blocked.append(f"{table.name}.{col.name}")
                continue
            plan.append((table.name, col.name, f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))
    if blocked:
        raise RuntimeError(f"컬럼 {', '.join(blocked)} 은 NOT NULL 이고 기본값이 없어 자동 추가할 수 없다 (아무 컬럼도 바꾸지 않았다). --reset 또는 수동 마이그레이션 필요")
    added: list[str] = []
    if not plan:
        return added
    # pysqlite 는 DDL 을 자동 커밋하므로 BEGIN IMMEDIATE 를 직접 열어 한 트랜잭션으로 묶는다 (중간 실패 시 전부 롤백).
    # BEGIN IMMEDIATE 는 쓰기 잠금이라 여러 프로세스가 동시에 처음 기동해도 한 번만 실행된다 (busy_timeout 안에서 대기).
    raw = engine.raw_connection()
    try:
        dbapi = raw.driver_connection if hasattr(raw, "driver_connection") else raw.connection
        if "sqlite" in engine.dialect.name:
            dbapi.isolation_level = None
            cur = dbapi.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                existing_now = {(t, c["name"]) for t, _, _ in plan for c in inspect(engine).get_columns(t)}
                for table_name, col_name, ddl in plan:
                    if (table_name, col_name) in existing_now:
                        continue  # 다른 프로세스가 먼저 추가했다
                    cur.execute(ddl)
                    added.append(f"{table_name}.{col_name}")
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()
        else:
            with engine.begin() as conn:
                for table_name, col_name, ddl in plan:
                    conn.execute(text(ddl))
                    added.append(f"{table_name}.{col_name}")
    finally:
        raw.close()
    if added:
        log.warning("DB 컬럼 추가(간이 마이그레이션): %s", added)
    return added


def init_db(url: str | None = None, drop: bool = False) -> Engine:
    # v2: 트랙별 모델 모듈을 여기서 import 해 Base.metadata 에 등록한다 (create_all 이 보게)
    from ..notices import models as _notice_models  # noqa: F401
    from ..support import models as _support_models  # noqa: F401

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
