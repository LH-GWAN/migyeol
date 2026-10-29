"""이름+직함 발언 주체의 뉴스 검색 해석 (rules/actor_search.py, docs/EXPERIMENT.md §15)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.bigkinds.recording import RecordingBigKindsClient
from app.bigkinds.schemas import NewsSearchResult
from app.rules import actor_search as a
from app.rules.actors import person_title
from app.rules.orgs import canonical_org, is_known_org, wide_alias


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTOR_CACHE_PATH", str(tmp_path / "actor_orgs.json"))
    a.reset_cache()
    yield
    a.reset_cache()


# ---------------------------------------------------------------- 판별
@pytest.mark.parametrize(
    "source,expected",
    [
        ("김동연 지사", ("김동연", "지사")),
        ("이상민 장관", ("이상민", "장관")),
        ("김찬진 구청장", ("김찬진", "구청장")),
        ("이철우 도지사", ("이철우", "도지사")),
        ("이도운 대변인", ("이도운", "대변인")),
        ("김 지사", None),  # 성만 있음
        ("경기 지사", None),  # 지역명
        ("경기도 김동연 지사", None),  # 기관 있음 (기존 규칙)
        ("김찬진 동구청장", None),  # 직함에 기관이 붙음 (기존 규칙)
        ("원희룡 국토교통부 장관", None),
        ("주민 대표", None),
        ("김동연", None),
    ],
)
def test_person_title(source, expected):
    assert person_title(source) == expected


def test_person_title_respects_lexicon():
    assert person_title("가온시 시장", ["가온시"]) is None


# ---------------------------------------------------------------- 표기 집계
@pytest.mark.parametrize(
    "text,name,title,expected",
    [
        ("<b>김동연</b> 경기도<b>지사</b>가 3일 화재현장을", "김동연", "지사", ["경기도"]),
        ("김동연 지사와 김관영 전북지사가", "김동연", "지사", []),  # 기관 없는 표기·다른 사람
        ("김동연, 김관영 전북지사", "김동연", "지사", []),
        ("김동연은 경기도지사 시절", "김동연", "지사", []),  # 조사
        ("원희룡 국토부 장관은", "원희룡", "장관", ["국토교통부"]),
        ("원희룡 전 국토교통부 장관", "원희룡", "장관", ["국토교통부"]),
        ("김찬진 인천 동구청장은", "김찬진", "구청장", ["동구"]),
        ("이철우 경북도지사는", "이철우", "도지사", ["경북"]),  # 도지사 → 핵심형 지사
        ("김성중 경기도 행정1부지사", "김성중", "행정1부지사", ["경기도"]),
        ("곽성일 NH농협은행 인천본부장", "곽성일", "본부장", []),  # 민간
        ("곽성일 인천본부장은", "곽성일", "본부장", []),  # 광역명 + 본부장은 기관 미상
        ("박김동연 경기도지사", "김동연", "지사", []),  # 더 긴 낱말의 일부
    ],
)
def test_mentions(text, name, title, expected):
    assert a.mentions(text, name, title) == expected


def test_tally_counts_documents_once():
    docs = [
        {"title": "김동연 경기도지사 현장 점검", "hilight": "김동연 경기도지사가", "content": "김동연 경기도지사는"},
        {"title": "김동연 지사", "hilight": "", "content": "김동연 경기도지사가"},
        {"title": "김동연 지사", "hilight": "김동연 지사", "content": ""},
    ]
    dist, docs_with = a.tally(docs, "김동연", "지사")
    assert dist == {"경기도": 2} and docs_with == 2


# ---------------------------------------------------------------- 임계
def test_decide_thresholds():
    assert a.decide({"경기도": 3}).org == "경기도"
    assert a.decide({"경기도": 2}).org is None  # 근거 부족
    assert a.decide({"경기도": 6, "서울시": 3}).org == "경기도"  # 2배
    d = a.decide({"기후에너지환경부": 17, "환경부": 13})  # 재임·조직 개편 경합
    assert d.org is None and "경합" in d.reason
    assert a.decide({}).org is None


def test_decide_prefers_event_region():
    dist = {"서천군": 5, "통일부": 6}
    assert a.decide(dist).org is None  # 지역 모르면 경합
    assert a.decide(dist, "충남 서천").org == "서천군"
    assert a.decide({"경기도": 4, "전남": 3}, "성남시").org == "경기도"
    assert a.decide({"동구": 5}, "인천 동구").org == "동구"
    # 지역 후보가 다른 지역 1위에 2배 이상 밀리면 보류하지 않고 1위 판정으로
    assert a.decide({"부여군": 3, "국토교통부": 20}, "충남 부여").org == "국토교통부"


def test_region_match():
    assert a.region_match("경기도", "성남시")
    assert a.region_match("담양군", "전남")
    assert a.region_match("동구", "인천 동구")
    assert not a.region_match("전남", "성남시")
    assert not a.region_match("행정안전부", "성남시")
    assert not a.region_match("동구", "대구 서구")


def test_bucket_window():
    assert a.bucket_of(date(2025, 2, 10)) == "2025Q1"
    assert a.bucket_of(date(2024, 12, 31)) == "2024Q4"
    assert a.bucket_window("2025Q1") == (date(2024, 11, 17), date(2025, 5, 16))
    assert a.bucket_window("2024Q4") == (date(2024, 8, 17), date(2025, 2, 15))
    assert a.search_query("이철우", "도지사") == '"이철우" AND "지사"'
    assert a.cache_key("이철우", "도지사", "2025Q1") == a.cache_key("이철우", "지사", "2025Q1")


# ---------------------------------------------------------------- 캐시·재생
class _FakeClient:
    mode = "record"

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def search_news(self, arg):
        self.calls.append(arg.argument())
        return NewsSearchResult(total_hits=len(self.docs), documents=self.docs)


_DOCS = [{"news_id": str(i), "title": "", "hilight": "", "content": "이상민 행정안전부 장관은 이날"} for i in range(4)]


def test_ensure_cache_then_lookup_without_network():
    client = _FakeClient(_DOCS)
    items = [("이상민 장관", date(2023, 8, 1), []), ("이상민 장관", date(2023, 8, 20), []), ("경찰 관계자", date(2023, 8, 1), [])]
    stats = a.ensure_cache(client, items)
    assert stats == {"needed": 1, "searched": 1, "failed": 0}  # 같은 분기는 한 번
    arg = client.calls[0]
    assert arg["query"] == '"이상민" AND "장관"' and arg["published_at"] == {"from": "2023-05-17", "until": "2023-11-15"}
    assert arg["return_size"] == a.RETURN_SIZE
    # 다시 돌려도 호출하지 않는다 (파일 캐시)
    a.reset_cache()
    assert a.ensure_cache(client, items)["needed"] == 0 and len(client.calls) == 1
    assert a.lookup("이상민 장관", date(2023, 9, 1)) == "행정안전부"
    assert a.lookup("이상민 장관", date(2024, 9, 1)) is None  # 다른 분기는 캐시 없음 → 원문 유지
    assert a.lookup("김 지사", date(2023, 9, 1)) is None
    saved = json.loads(a.cache_path().read_text(encoding="utf-8"))
    ent = saved["entries"]["이상민|장관|2023Q3"]
    assert ent["dist"] == {"행정안전부": 4} and ent["docs_with_org"] == 4


def test_replay_only_recording_gives_same_result(tmp_path, monkeypatch):
    """record 모드: 처음 호출은 녹화, replay_only 로 같은 요청은 네트워크 없이 같은 결과."""
    ret = {"total_hits": 4, "documents": _DOCS}
    calls = []

    def fake_live(self, endpoint, argument):
        calls.append(endpoint)
        return ret

    monkeypatch.setattr("app.bigkinds.live.LiveBigKindsClient._call", fake_live)
    rec = RecordingBigKindsClient(access_key="test", recordings_dir=tmp_path / "rec")
    items = [("이상민 장관", date(2023, 8, 1), [])]
    a.ensure_cache(rec, items)
    first = a.lookup("이상민 장관", date(2023, 8, 1))
    a.cache_path().unlink()
    a.reset_cache()
    replay = RecordingBigKindsClient(access_key="", recordings_dir=tmp_path / "rec", replay_only=True)
    assert a.ensure_cache(replay, items) == {"needed": 1, "searched": 1, "failed": 0}
    assert replay.hits == 1 and replay.misses == 0 and len(calls) == 1
    assert a.lookup("이상민 장관", date(2023, 8, 1)) == first == "행정안전부"


def test_replay_only_miss_is_skipped(tmp_path):
    replay = RecordingBigKindsClient(access_key="", recordings_dir=tmp_path / "rec", replay_only=True)
    assert a.ensure_cache(replay, [("김동연 지사", date(2025, 1, 3), [])]) == {"needed": 1, "searched": 0, "failed": 1}
    assert a.lookup("김동연 지사", date(2025, 1, 3)) is None


def test_extract_resolve_person_title_uses_responsible_org_spelling():
    from types import SimpleNamespace

    from app.pipeline.extract import resolve_person_title

    a.save_cache({"entries": {"김찬진|구청장|2023Q1": {"dist": {"동구": 5}}, "김동연|지사|2025Q1": {"dist": {"경기도": 9}}}})
    ev = SimpleNamespace(region="인천 동구", responsible_org="인천 동구")
    assert resolve_person_title(ev, "김찬진 구청장", date(2023, 1, 10), []) == "인천 동구"  # 책임기관과 같은 기관 → 책임기관 표기
    ev2 = SimpleNamespace(region="성남시", responsible_org="성남시")
    assert resolve_person_title(ev2, "김동연 지사", date(2025, 1, 3), []) == "경기도"
    assert resolve_person_title(ev2, "경찰 관계자", date(2025, 1, 3), []) is None


# ---------------------------------------------------------------- 광역 + 기초
@pytest.mark.parametrize(
    "name,expected",
    [
        ("부산 영도구청", "영도구"),
        ("김찬진 인천 동구청장", "동구"),
        ("인천 동구", "동구"),
        ("서울 강서구", "강서구"),
        ("서울시 강남구청장", "강남구"),
        ("충남 소방본부", "충남소방본부"),  # 광역 + 기관 종류는 그대로
        ("부산시", "부산시"),
        ("경기도 김동연 지사", "경기도"),
        ("인천 본부장", "인천시"),
    ],
)
def test_canonical_basic_over_wide(name, expected):
    assert canonical_org(name) == expected


def test_wide_alias_and_known_org():
    assert wide_alias("인천 동구") == "인천시"
    assert wide_alias("부산 영도구청") == "부산시"
    assert wide_alias("동구") is None and wide_alias("경기도") is None
    assert is_known_org("동구") and is_known_org("경기도") and is_known_org("행정안전부") and is_known_org("전남소방본부")
    assert not is_known_org("지사") and not is_known_org("김동연 지사") and not is_known_org("")


def test_notice_sites_fall_back_to_wide_alias():
    from app.notices.sites import Site, sites_for_org

    s_incheon = Site(key="incheon", name="인천", orgs=("인천시",), domain="incheon.go.kr", list_url="https://www.incheon.go.kr/x?p={page}", item="li")
    assert sites_for_org("인천 동구", [s_incheon]) == [s_incheon]
    assert sites_for_org("동구", [s_incheon]) == []


# ---------------------------------------------------------------- 라벨 재채점
def test_org_regrade_when_normalization_changes():
    from app.experiment.labels import _regrade_org

    # X 였고 정답이 있는 행: 새 정규화가 정답과 같은 기관이면 O
    assert _regrade_org({"정규화": "경기도"}, {"정규화": "김동연 지사", "사람_OK(O/X)": "X", "사람_정답": "경기도"}) == {"사람_OK(O/X)": "O", "사람_정답": "경기도"}
    assert _regrade_org({"정규화": "강원도"}, {"정규화": "김진태 지사", "사람_OK(O/X)": "X", "사람_정답": "강원"})["사람_OK(O/X)"] == "O"
    assert _regrade_org({"정규화": "전북"}, {"정규화": "김동연 지사", "사람_OK(O/X)": "X", "사람_정답": "경기도"})["사람_OK(O/X)"] == "X"
    # O 였던 행이 다른 기관으로 바뀌면 옛 정규화가 정답 → X
    assert _regrade_org({"원문": "인천 본부장", "정규화": "경기도"}, {"정규화": "인천시", "사람_OK(O/X)": "O", "사람_정답": ""}) == {"사람_OK(O/X)": "X", "사람_정답": "인천시"}
    # '원문 그대로 둠' 을 O 로 본 라벨은 새 기관을 판단할 수 없어 비운다
    assert _regrade_org({"원문": "김기웅 군수", "정규화": "서천군"}, {"정규화": "김기웅 군수", "사람_OK(O/X)": "O", "사람_정답": ""}) == {"사람_OK(O/X)": "", "사람_정답": ""}
    # 정규화가 같으면 그대로, 라벨 없으면 그대로
    assert _regrade_org({"정규화": "경기도"}, {"정규화": "경기도", "사람_OK(O/X)": "O", "사람_정답": ""}) == {}
    assert _regrade_org({"정규화": "경기도"}, {"정규화": "김동연 지사", "사람_OK(O/X)": "", "사람_정답": ""}) == {}


def test_wants_only_person_title_without_known_org():
    assert a.wants("김동연 지사", None)
    assert a.wants("안병윤 부시장", "부시")  # 규칙 결과가 기관이 아닌 조각
    assert not a.wants("김민석 총리", "총리")  # 국무총리 = 이미 기관
    assert not a.wants("경찰 관계자", None)
    assert not a.wants("원희룡 국토교통부 장관", "국토교통부")
