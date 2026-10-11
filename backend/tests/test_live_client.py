"""live/record 클라이언트 계약 테스트 — httpx.MockTransport 로 요청 형태·재시도·오류 분기·녹화/재생을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.bigkinds.live import LiveBigKindsClient
from app.bigkinds.protocol import DEFAULT_NEWS_FIELDS, BigKindsError
from app.bigkinds.recording import RecordingBigKindsClient
from app.bigkinds.schemas import ChangeTrackerResult, IssueRankingResult, NewsSearchArgument, NewsSearchResult, QuotationSearchResult

GUIDE_NEWS = {
    "result": 0,
    "return_object": {
        "total_hits": 155040,
        "documents": [
            {
                "news_id": "02100601.20211027093629001",
                "title": "여의도 하나금투 담은 리츠",
                "hilight": "…국토<b>교통</b>부는…",
                "published_at": "2021-10-27T00:00:00.000+09:00",
                "provider": "한국경제",
                "category": ["경제>증권_증시"],
                "category_incident": [],
                "byline": "윤아영",
                "provider_link_page": None,
                "tms_raw_stream": "여의\n리츠\n리츠",
            }
        ],
    },
}


def make_client(handler, **kw) -> LiveBigKindsClient:
    transport = httpx.MockTransport(handler)
    return LiveBigKindsClient("key-123", "https://example.test", rps=0, http=httpx.Client(transport=transport), **kw)


def test_request_body_shape_and_default_fields():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx.Response(200, json=GUIDE_NEWS)

    c = make_client(handler)
    res = c.search_news(NewsSearchArgument(query="사과 AND 배", fields=[]))
    assert seen["path"] == "/search/news"
    assert seen["body"]["access_key"] == "key-123"
    assert seen["body"]["argument"]["query"] == "사과 AND 배"
    assert seen["body"]["argument"]["fields"] == DEFAULT_NEWS_FIELDS
    assert isinstance(res, NewsSearchResult) and res.total_hits == 155040
    assert res.documents[0].keywords == ["여의", "리츠"]  # 중복 제거
    assert c.call_log[0]["document_keys"]  # 첫 실호출에서 실제 반환 키 확인용


def test_4xx_is_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(BigKindsError) as exc:
        make_client(handler, max_retries=3).issue_ranking("2025-01-01")
    assert calls["n"] == 1 and exc.value.result == 401


def test_503_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.bigkinds.live.time.sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"result": 0, "return_object": {"date": "2025-01-01", "topics": []}})

    res = make_client(handler, max_retries=3).issue_ranking("2025-01-01")
    assert isinstance(res, IssueRankingResult) and calls["n"] == 3


def test_non_json_and_api_result_error_become_bigkinds_error():
    with pytest.raises(BigKindsError):
        make_client(lambda req: httpx.Response(200, text="<html>oops</html>")).issue_ranking("2025-01-01")
    with pytest.raises(BigKindsError) as exc:
        make_client(lambda req: httpx.Response(200, json={"result": 4, "return_object": None, "reason": "invalid key"})).issue_ranking("2025-01-01")
    assert exc.value.result == 4


def test_null_return_object_is_normalized():
    res = make_client(lambda req: httpx.Response(200, json={"result": 0, "return_object": None})).issue_ranking("2025-01-01")
    assert res.topics == []


def test_guide_samples_parse():
    quote = {"total_hits": 72, "documents": [{"news_id": "01100201.20190111193338001", "published_at": "2019-01-11T00:00:00.000+09:00", "date": "20190111", "provider": "국민일보", "category": None, "category_incident": "범죄>기업범죄>거래제한", "source": "문재인 대통령", "quotation": "한국형 규제 샌드박스"}]}
    q = QuotationSearchResult.model_validate(quote)
    assert q.documents[0].category == [] and q.documents[0].category_incident == ["범죄>기업범죄>거래제한"]
    ct = ChangeTrackerResult.model_validate({"total_count": "14032", "size": "", "message": "OK", "dataList": [{"data": {"media_id": "07101201", "newsitem_id": "07101201.20260727173209001", "news_status": "Update"}}]})
    assert ct.size == 3000 and ct.items[0].newsitem_id == "07101201.20260727173209001"


def test_recording_replays_second_call_and_stores_no_key(tmp_path: Path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=GUIDE_NEWS)

    rec = RecordingBigKindsClient("key-123", "https://example.test", rps=0, http=httpx.Client(transport=httpx.MockTransport(handler)), recordings_dir=tmp_path)
    arg = NewsSearchArgument(query="리츠", fields=["title"])
    a = rec.search_news(arg)
    b = rec.search_news(arg)
    assert calls["n"] == 1 and a.total_hits == b.total_hits and rec.hits == 1 and rec.misses == 1
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1 and "key-123" not in files[0].read_text(encoding="utf-8")


def test_replay_only_without_key_fails_loudly_on_miss(tmp_path: Path):
    rec = RecordingBigKindsClient("", "https://example.test", rps=0, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))), recordings_dir=tmp_path, replay_only=True)
    with pytest.raises(BigKindsError, match="녹화 없음"):
        rec.issue_ranking("2025-01-01")
    assert rec.missing and rec.missing[0]["endpoint"] == "/issue_ranking"


def test_corrupt_recording_is_rerecorded(tmp_path: Path):
    rec = RecordingBigKindsClient("key-123", "https://example.test", rps=0, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=GUIDE_NEWS))), recordings_dir=tmp_path)
    arg = NewsSearchArgument(query="리츠", fields=["title"])
    rec.search_news(arg)
    path = next(tmp_path.rglob("*.json"))
    path.write_text("{broken", encoding="utf-8")
    res = rec.search_news(arg)
    assert res.total_hits == 155040 and json.loads(path.read_text(encoding="utf-8"))["return_object"]
