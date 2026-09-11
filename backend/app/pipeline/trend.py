"""키워드 트렌드(time_line) 로 보도량 급감을 정량화한다.

peak_hits  : 월별 최대 기사 수
recent_mean: 최근 3개월(이번 달 포함) 평균, 빈 달은 0
drop_ratio : 1 - recent_mean / peak_hits
"""

from __future__ import annotations

from datetime import date, timedelta

from ..bigkinds.protocol import BigKindsClient
from ..bigkinds.schemas import PublishedAt, TimeLineArgument
from ..db.models import Event


def _month_labels(start: date, end: date) -> list[str]:
    labels = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        labels.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return labels


def compute_trend(client: BigKindsClient, event: Event, today: date) -> dict:
    res = client.time_line(
        TimeLineArgument(
            query=event.seed_query,
            published_at=PublishedAt.of(event.occurred_at.replace(day=1), today + timedelta(days=1)),
            interval="month",
        )
    )
    hits = {p.label: p.hits for p in res.time_line}
    labels = _month_labels(event.occurred_at, today)
    series = [{"label": lb, "hits": int(hits.get(lb, 0))} for lb in labels]
    peak = max(series, key=lambda s: s["hits"]) if series else None
    recent = series[-3:] if series else []
    recent_mean = round(sum(s["hits"] for s in recent) / len(recent), 2) if recent else 0.0
    peak_hits = peak["hits"] if peak else 0
    drop = round(1 - recent_mean / peak_hits, 3) if peak_hits > 0 else 0.0
    return {
        "interval": "month",
        "series": series,
        "peak_label": peak["label"] if peak else None,
        "peak_hits": peak_hits,
        "recent_mean": recent_mean,
        "drop_ratio": max(0.0, drop),
    }
