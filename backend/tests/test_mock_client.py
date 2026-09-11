from datetime import date

from app.bigkinds.mock import MockBigKindsClient
from app.bigkinds.protocol import DEFAULT_NEWS_FIELDS, DEFAULT_QUOTATION_FIELDS
from app.bigkinds.schemas import (
    ChangeTrackerArgument,
    NewsSearchArgument,
    PublishedAt,
    QuotationSearchArgument,
    TimeLineArgument,
    WordCloudArgument,
)

Q = '("달빛지하차도" OR "지하차도 침수") AND ("가온시" OR "가온시청" OR "가온시장")'


def client(fixtures_ready):
    return MockBigKindsClient(fixtures_ready / "bigkinds")


def test_search_news_filters_sorts_and_projects_fields(fixtures_ready):
    c = client(fixtures_ready)
    r = c.search_news(NewsSearchArgument(query=Q, published_at=PublishedAt.of(date(2025, 7, 14), date(2025, 8, 1)), sort={"date": "asc"}, fields=list(DEFAULT_NEWS_FIELDS)))
    assert r.total_hits >= 4
    dates = [d.published_date for d in r.documents]
    assert dates == sorted(dates)
    assert all(d.published_date < date(2025, 8, 1) for d in r.documents)  # until 제외
    d0 = r.documents[0]
    assert d0.hilight and len(d0.hilight) <= 200 + 7 * 4  # <b></b> 태그 포함 허용
    assert d0.category_incident == ["재해>자연재해>홍수"]
    assert d0.content is None  # fields 에 없으면 반환하지 않음


def test_search_news_category_incident_code_or_name(fixtures_ready):
    c = client(fixtures_ready)
    by_name = c.search_news(NewsSearchArgument(query="", category_incident=["재해>자연재해"], return_size=100, fields=[]))
    by_code = c.search_news(NewsSearchArgument(query="", category_incident=["3,11"], return_size=100, fields=[]))
    assert by_name.total_hits == by_code.total_hits > 0


def test_pagination(fixtures_ready):
    c = client(fixtures_ready)
    a = c.search_news(NewsSearchArgument(query="", return_from=0, return_size=5, fields=[]))
    b = c.search_news(NewsSearchArgument(query="", return_from=5, return_size=5, fields=[]))
    assert a.total_hits == b.total_hits
    assert {d.news_id for d in a.documents}.isdisjoint({d.news_id for d in b.documents})


def test_news_by_ids_and_issue_ranking(fixtures_ready):
    c = client(fixtures_ready)
    ir = c.issue_ranking("2025-07-15")
    assert any("달빛지하차도" in t.topic for t in ir.topics)
    ids = ir.topics[0].news_cluster
    r = c.get_news_by_ids(ids, fields=["category_incident"])
    assert {d.news_id for d in r.documents} == set(ids)


def test_quotation_search_returns_source_and_quotation(fixtures_ready):
    c = client(fixtures_ready)
    r = c.search_quotation(QuotationSearchArgument(query=Q, published_at=PublishedAt.of(date(2025, 7, 15), date(2025, 10, 15)), fields=list(DEFAULT_QUOTATION_FIELDS)))
    assert r.total_hits >= 3
    assert any(d.source == "가온시장" and "배수펌프" in d.quotation for d in r.documents)


def test_time_line_month_labels(fixtures_ready):
    c = client(fixtures_ready)
    tl = c.time_line(TimeLineArgument(query=Q, published_at=PublishedAt.of(date(2025, 7, 1), date(2026, 9, 12)), interval="month"))
    assert tl.time_line[0].label == "202507"
    assert all(len(p.label) == 6 for p in tl.time_line)


def test_word_cloud_excludes_query_terms(fixtures_ready):
    c = client(fixtures_ready)
    wc = c.word_cloud(WordCloudArgument(query='"달빛지하차도"', published_at=PublishedAt.of(date(2025, 6, 15), date(2025, 8, 15))))
    names = [n.name for n in wc.nodes]
    assert "배수펌프" in names
    assert "달빛지하차도" not in names


def test_change_tracker_window(fixtures_ready):
    c = client(fixtures_ready)
    r = c.change_tracker(ChangeTrackerArgument(**{"from": "2026-09-11 05:00:00", "interval": "DAY_1", "offset": "1"}))
    assert r.total_count >= 1
    assert {i.news_status for i in r.items} <= {"Update", "Cancelled"}
