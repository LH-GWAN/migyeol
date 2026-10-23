"""v2 §4 실험 파이프라인 (트랙 A): mock 픽스처로 전 단계를 돌려 산출물 형식·스키마·라벨 보존을 고정한다.

실 API 결과(숫자)는 reports/experiment_*.md 에 있고, 여기서는 배선과 계약(§4.6 키 이름)만 검증한다.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from app.experiment import autoseed, labels, metrics
from app.experiment.runner import BUDGET, CountingClient, Experiment, ExperimentConfig
from app.experiment.sources import SourceQuote, call_plan, month_windows
from app.experiment.text import casualties, find_facility, find_regions, scrub
from app.rules.orgs import canonical_org, org_aliases

FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")


@pytest.fixture(scope="module")
def mock_run(tmp_path_factory, fixtures_ready):
    from app.bigkinds import build_client
    from app.config import get_settings
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.rules_adapter import RulesLLM
    from app.pipeline.embeddings import HashEmbedding

    tmp = tmp_path_factory.mktemp("exp")
    import app.pipeline.runner as runner_mod

    orig_lock = runner_mod._lock_path
    runner_mod._lock_path = lambda: tmp / "pipeline.lock"  # 실 실험이 backend/data 의 Lock 을 쥐고 있어도 테스트가 돈다
    settings = get_settings()
    reset_engine()
    init_db(f"sqlite:///{tmp / 'exp.db'}", drop=True)
    session = session_factory()()
    cfg = ExperimentConfig(
        start=date(2025, 1, 1), until=date(2025, 12, 31), today=date(2026, 9, 11), work_dir=tmp / "work", draft_path=tmp / "experiment.draft.yaml",
        gold_dir=tmp / "gold", reports_dir=tmp / "reports", max_events=20, min_unique=1,
    )
    exp = Experiment(session, settings, build_client(settings), RulesLLM(settings), HashEmbedding(), cfg)
    out = exp.run("all")
    yield exp, out, tmp
    runner_mod._lock_path = orig_lock
    session.close()
    reset_engine()


def _shape(x):
    if isinstance(x, dict):
        return {k: _shape(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_shape(x[0])] if x and isinstance(x[0], dict) else []
    return None


# §4.6 예시 JSON 의 키 구조 (트랙 B·C 가 그대로 읽는다 — 바꾸면 이 테스트가 깨져야 한다)
SPEC_EXAMPLE = {
    "generated_at": "", "as_of": "", "bigkinds_mode": "", "seeds_file": "", "embedding_backend": "", "llm_mode": "",
    "source_articles": {"collected": 0, "unique": 0},
    "events": {"total": 0, "with_followup": 0, "followup_rate": 0.0, "by_status": {"조치 완료 근거 확인": 0, "조치 진행 정황 확인": 0, "새로운 문제 발생": 0, "후속보도 부족": 0, "판단 불가": 0},
               "with_org_notice": 0, "articles_per_event": {"median": 0, "max": 0}},
    "windows_days": {"30": 0, "90": 0, "180": 0, "365": 0},
    "signals": {"완료": 0, "진행": 0, "새로운문제": 0},
    "by_incident_type": [{"incident_type": "", "events": 0, "with_followup": 0, "rate": 0.0}],
    "by_org": [{"org": "", "events": 0, "with_followup": 0, "rate": 0.0, "latest_followup_at": "", "event_ids": []}],
    "timeseries": [{"bucket": "", "occurred": 0, "followup_articles": 0, "dead": None, "injured": None}],
    "daily": [{"date": "", "occurred": 0, "followup_articles": 0}],
    "labels": {"labeled": 0, "followup_precision": None, "followup_recall": None, "cluster_precision": None, "org_accuracy": None},
    "cost": {"api_calls": {"search_news": 0, "search_quotation": 0, "issue_ranking": 0, "time_line": 0, "word_cloud": 0}, "elapsed_sec": 0, "recordings": 0},
    "success": {"min_followup_events": 5, "target_followup_events": 20, "met_min": False, "met_target": False},
}


def test_latest_json_matches_spec_schema(mock_run):
    exp, out, tmp = mock_run
    latest = json.loads((tmp / "reports" / "experiment_latest.json").read_text(encoding="utf-8"))
    assert _shape(latest) == _shape(SPEC_EXAMPLE)
    assert _shape(metrics.empty_latest()) == {k: (v if not isinstance(v, list) else []) for k, v in _shape(SPEC_EXAMPLE).items()}
    assert latest["llm_mode"] == "rules" and latest["bigkinds_mode"] == "mock"
    assert latest["events"]["total"] >= 1 and latest["source_articles"]["unique"] == latest["events"]["total"]
    assert latest["success"]["met_min"] == (latest["events"]["with_followup"] >= 5)
    assert sum(latest["events"]["by_status"].values()) == latest["events"]["total"]
    assert latest["cost"]["api_calls"]["search_quotation"] > 0 and latest["cost"]["api_calls"]["issue_ranking"] == 365
    # 기관 정렬: 후속보도 확인 건수 → 비율 내림차순
    keys = [(-r["with_followup"], -r["rate"]) for r in latest["by_org"]]
    assert keys == sorted(keys)


def test_report_md_and_run_record(mock_run):
    exp, out, tmp = mock_run
    md = Path(out["report"]["report"]).read_text(encoding="utf-8")
    assert "후속보도 확인 사건" in md and "비용" in md and "한계" in md
    assert not any(w in md for w in FORBIDDEN)
    from app.db.models import ExperimentRun

    run = exp.session.query(ExperimentRun).order_by(ExperimentRun.id.desc()).first()
    assert run is not None and run.ok and run.metrics["events"]["total"] == out["report"]["events"]


def test_draft_seed_is_loadable_and_marks_source(mock_run):
    exp, _, tmp = mock_run
    from app.db.models import Event
    from app.pipeline.seeds import load_seeds

    seeds = load_seeds(tmp / "experiment.draft.yaml")
    assert seeds and all(s.auto_seed and s.source_news_id and s.query for s in seeds)
    ev = exp.session.query(Event).filter_by(key=seeds[0].key).one()
    assert ev.auto_seed and ev.source_news_id == seeds[0].source_news_id and ev.source_quotation
    assert any(l.news_id == ev.source_news_id and l.is_active for l in ev.links)  # 원천 기사는 사건의 연결 기사


def test_label_csvs_keep_human_columns(mock_run):
    exp, _, tmp = mock_run
    g = tmp / "gold"
    with (g / "experiment_labels.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows and list(rows[0].keys()) == labels.EXPERIMENT_COLS
    assert all(r[h] == "" for r in rows for h in labels.EXPERIMENT_HUMAN)  # 사람 열은 비어 있다
    assert all(r["시스템_후속보도"] in ("있음", "없음") for r in rows)
    for name, cols in (("cluster_sample.csv", labels.CLUSTER_COLS), ("org_sample.csv", labels.ORG_COLS)):
        with (g / name).open(encoding="utf-8-sig") as f:
            assert next(csv.reader(f)) == cols
    # 사람이 라벨을 채운 뒤 다시 내보내도 보존되고, 지표가 계산된다
    rows[0]["사람_후속보도(O/X)"] = "O"
    with (g / "experiment_labels.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=labels.EXPERIMENT_COLS)
        w.writeheader()
        w.writerows(rows)
    res = exp.run("report")["report"]
    with (g / "experiment_labels.csv").open(encoding="utf-8-sig") as f:
        again = list(csv.DictReader(f))
    assert again[0]["사람_후속보도(O/X)"] == "O"
    latest = json.loads(Path(res["latest"]).read_text(encoding="utf-8"))
    assert latest["labels"]["labeled"] == 1
    assert latest["labels"]["followup_recall"] == (1.0 if again[0]["시스템_후속보도"] == "있음" else 0.0)


def test_labels_to_gold_roundtrip(mock_run, tmp_path):
    import subprocess
    import sys

    from app.config import BACKEND_DIR

    _, _, tmp = mock_run
    out = tmp_path / "gold.yaml"
    r = subprocess.run([sys.executable, str(BACKEND_DIR / "scripts" / "labels_to_gold.py"), "--gold-dir", str(tmp / "gold"), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import yaml

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["events"] and all(isinstance(e["key"], str) for e in data["events"])


def _q(news_id, published, facility="", regions=(), hazards=("침수",), org="청주시", quotation="배수펌프를 증설하겠다", source="청주시 관계자") -> SourceQuote:
    return SourceQuote(
        news_id=news_id, published=published, source=source, quotation=quotation, org=org, category_incident=["재해>자연재해>홍수"],
        incident_type="재해>자연재해", incident_detail="재해>자연재해>홍수", strength="계획", action=quotation, deadline=None,
        title=f"{' '.join(regions)} {facility} {' '.join(hazards)}", regions=list(regions), facility=facility, hazards=list(hazards),
    )


def test_uniqueness_grouping_rules():
    a = _q("1", "2023-07-15", facility="궁평2지하차도", regions=["청주"])
    b = _q("2", "2023-07-20", facility="궁평2지하차도", regions=["청주"], org="충북", quotation="재발 방지 대책을 마련하겠다", source="충북도 관계자")
    c = _q("3", "2023-08-10", facility="궁평2지하차도", regions=["청주"], quotation="전수조사하겠다")  # 14일 밖
    d = _q("4", "2023-07-16", regions=["청주"], hazards=["호우"], org="청주시", quotation="복구하겠다")  # 같은 지역·같은 계통(물)
    e = _q("5", "2023-07-16", regions=["부산"], hazards=["화재"], org="부산시", quotation="점검하겠다")
    groups = autoseed.group_quotes([a, b, c, d, e])
    by = {frozenset(q.news_id for q in g.quotes) for g in groups}
    assert frozenset({"1", "2", "4"}) in by and frozenset({"3"}) in by and frozenset({"5"}) in by
    g = next(g for g in groups if len(g.quotes) == 3)
    assert g.rep.news_id == "1" and g.facility == "궁평2지하차도"  # 대표는 가장 이른 기사


def test_selection_is_stratified_and_preventive_ranked_low():
    qs = [_q(str(i), f"202{3 + i % 3}-07-1{i % 9}", facility=f"시설{i}교", regions=["청주"]) for i in range(9)]
    groups = autoseed.group_quotes(qs)
    picked = autoseed.select_groups(groups, 3)
    assert len({g.occurred.year for g in picked}) == 3
    prev = autoseed.group_quotes([_q("p", "2023-07-01", regions=["청주"], hazards=["호우"])])
    prev[0].quotes[0].title = "청주시, 장마철 대비 집중 점검"
    autoseed._describe(prev[0])
    occurred = autoseed.group_quotes([_q("o", "2023-07-01", facility="미호천", regions=["청주"])])
    assert occurred[0].score > prev[0].score


def test_narrow_query_shapes():
    seed = {"facility": "궁평2지하차도", "keywords": ["침수"], "region": "청주", "responsible_org": "청주시", "org_aliases": ["청주시청"], "title": "t"}
    assert autoseed.narrow_query(seed) == '"궁평2지하차도" AND ("청주" OR "청주시")'
    generic = {"facility": "9호선", "keywords": ["싱크홀"], "region": "강동구", "responsible_org": "서울시", "org_aliases": [], "title": "t"}
    assert autoseed.narrow_query(generic) == '"9호선" AND ("싱크홀") AND ("강동구")'  # 광역 기관은 범위를 넓혀 넣지 않는다
    central = {"facility": "", "keywords": ["추락"], "region": "완도", "responsible_org": "국토교통부", "org_aliases": ["국토부"], "title": "t"}
    assert autoseed.narrow_query(central) == '("추락") AND ("완도")'  # 중앙부처는 범위 절에 넣지 않는다
    national = {"facility": "", "keywords": ["산불"], "region": "", "responsible_org": "산림청", "org_aliases": [], "title": "헬기 노후 기체 교체", "_specific_terms": ["헬기"]}
    assert autoseed.narrow_query(national) == '("산불") AND "헬기" AND ("산림청")'  # 지역이 없으면 기관이 범위


@pytest.mark.parametrize(
    "raw,canon",
    [
        ("서울특별시", "서울시"), ("서울", "서울시"), ("서울시", "서울시"), ("서울시청", "서울시"), ("서울시장", "서울시"), ("오세훈 서울시장", "서울시"),
        ("국토교통부", "국토교통부"), ("국토부", "국토교통부"), ("원희룡 국토교통부 장관", "국토교통부"), ("이상민행정안전부 장관", "행정안전부"),
        ("한국도로공사", "한국도로공사"), ("도로공사 관계자", "한국도로공사"), ("LH", "한국토지주택공사"), ("이한준 LH 사장", "한국토지주택공사"),
        ("청주시 관계자", "청주시"), ("최재형 보은군수", "보은군"), ("연수구청장", "연수구"), ("김영환 충북지사", "충북"), ("충청북도", "충북"),
        ("목포해경 관계자", "목포해양경찰서"), ("해경 관계자", "해양경찰청"), ("충남 소방본부장", "충남소방본부"), ("소방청장", "소방청"),
        ("행복청 관계자", "행정중심복합도시건설청"), ("코레일", "한국철도공사"), ("수원", "수원시"), ("보은", "보은군"), ("류효종창원시", "창원시"), ("", ""),
    ],
)
def test_canonical_org_dictionary(raw, canon):
    assert canonical_org(raw) == canon


def test_org_aliases_for_link_gate():
    assert "국토부" in org_aliases("국토교통부")
    assert "LH" in org_aliases("한국토지주택공사")
    assert "서울시청" in org_aliases("서울시")


def test_text_helpers():
    t = "청주 오송 궁평2지하차도 침수 참사…14명 숨지고 10명 부상"
    assert find_regions(t)[0] == "청주"
    assert find_facility(t) == "궁평2지하차도"
    assert casualties(t) == (14, 10)
    assert casualties("배수펌프 2대 설치") == (None, None)
    assert find_facility("평택해경, 여름 성수기 대비 연안사고 위험구역 점검") == ""
    assert find_facility("李 시장 폭우 피해 최소화 대전시장") == ""
    assert scrub("계약 " + FORBIDDEN[1] + " 논란") == "계약 불○행 논란"


def test_call_plan_and_counting_client(fixtures_ready):
    plan = call_plan(date(2023, 1, 1), date(2025, 12, 31), ("사고>산업사고", "사고>교통사고", "재해>자연재해"), 4, True, 150)
    assert plan["total"] <= BUDGET * 2
    assert len(month_windows(date(2023, 1, 1), date(2025, 12, 31))) == 36
    from app.bigkinds import build_client
    from app.config import get_settings

    c = CountingClient(build_client(get_settings()))
    c.issue_ranking("2025-07-15")
    c.get_news_by_ids(["x"])
    assert c.calls == {"issue_ranking": 1, "search_news": 1} and c.mode == "mock"
