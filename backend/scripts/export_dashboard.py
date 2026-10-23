"""대시보드 집계 파일 내보내기 (v2 §7.1) — 일별·월별·재난 유형별·기관별 집계를 한 파일로.

  python scripts/export_dashboard.py                 # reports/dashboard_<기준일>.json
  python scripts/export_dashboard.py --out-dir /tmp  # 다른 폴더로

/api/dashboard/* 와 같은 함수(app.api.dashboard.build_dashboard)로 만든다 — 화면 숫자와 파일 숫자가 같다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.dashboard import build_dashboard  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402


def export(out_dir: Path | None = None) -> Path:
    settings = get_settings()
    init_db()
    session = session_factory()()
    try:
        data = build_dashboard(session, settings)
    finally:
        session.close()
    out_dir = Path(out_dir or settings.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"dashboard_{settings.today().strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None, help="기본: REPORTS_DIR (저장소 reports/)")
    args = ap.parse_args()
    out = export(Path(args.out_dir) if args.out_dir else None)
    data = json.loads(out.read_text(encoding="utf-8"))
    s = data["summary"]
    print(f"{out}  사건 {s['events_total']} · 후속보도 확인 {s['with_followup']} ({s['followup_rate']:.0%}) · 기관 {len(data['by_org'])} · 일별 {len(data['daily'])}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
