"""reports/experiment_<YYYYMMDD_HHMM>.md — §4.6 지표 표. 모든 숫자는 experiment_latest.json 과 같은 값이다."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import DISCLAIMER, STATUSES, Settings
from .metrics import EventFacts
from .text import scrub


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _v(x: Any) -> str:
    return "—" if x is None else str(x)


def _rules_name(latest: dict, settings: Settings) -> str | None:
    """규칙 어댑터로 돌린 실험이면 그 규칙 이름(rules-v2 / rules-v3 — FOLLOWUP_* 보강을 켰는가), 아니면 None."""
    if latest.get("llm_mode") != "rules":
        return None
    from ..llm.rules_adapter import RulesLLM

    return RulesLLM(settings).model


def render(latest: dict, facts: list[EventFacts], state: dict, lab: dict, org_rows: list[dict], cfg, settings: Settings, session: Session) -> str:
    ev = latest["events"]
    rules = _rules_name(latest, settings)
    st = state.get("stages", {})
    disc = (st.get("discover") or {}).get("stats", {})
    L: list[str] = []
    a = L.append
    a("# 미결 실험 리포트 — 원천 기사 → 사건 군집 → 후속보도 탐지 (v2 §4)")
    a("")
    a(f"- 생성: {latest['generated_at']} · 기준일(as_of): {latest['as_of']} · 빅카인즈 모드: `{latest['bigkinds_mode']}` · LLM: `{latest['llm_mode']}`{f'(규칙 어댑터 {rules})' if rules else ''} · 임베딩: `{latest['embedding_backend']}`")
    a(f"- 모집단: 전국, 발생 {cfg.start} ~ {cfg.until}, 사건 분류 {', '.join(cfg.prefixes)} · 시드 파일: `{latest['seeds_file']}`")
    if cfg.embedding_note:
        a(f"- 임베딩 메모: {cfg.embedding_note}")
    a(f"- {DISCLAIMER}")
    a("")
    a("## 1. 요약")
    a("")
    a("| 지표 | 값 |")
    a("|---|---|")
    a(f"| 원천 기사 (수집 / 유니크) | {latest['source_articles']['collected']} / {latest['source_articles']['unique']} |")
    a(f"| 사건 군집 수 | {ev['total']} (사건당 연결 기사 중앙값 {ev['articles_per_event']['median']} · 최대 {ev['articles_per_event']['max']}) |")
    a(f"| 후속보도 확인 사건 | **{ev['with_followup']}건 ({_pct(ev['followup_rate'])})** — 성공선 {latest['success']['min_followup_events']}건 {'달성' if latest['success']['met_min'] else '미달'}, 목표 {latest['success']['target_followup_events']}건 {'달성' if latest['success']['met_target'] else '미달'} |")
    a(f"| 경과 창별 (30/90/180/365일) | {' / '.join(str(latest['windows_days'][k]) for k in ('30', '90', '180', '365'))} |")
    a(f"| 후속 신호 (완료/진행/새로운문제) | {latest['signals']['완료']} / {latest['signals']['진행']} / {latest['signals']['새로운문제']} |")
    a(f"| 기관 발표 확인 사건 | {ev['with_org_notice']} |")
    a("")
    a("후속보도 있음 = 사건에 연결된 기사 중 발행일 > max(발생일+7일, 원천 발언일) 인 기사에서, 추적 약속의 행동 키워드와 겹치는 문장이 완료·진행·새로운문제 표현을 담고(신뢰도 ≥ "
      f"{settings.signal_min_conf}) 있는 경우(§4.4). 보도가 확인되지 않은 사건은 '조치가 없었다' 가 아니라 '확인이 필요하다' 는 뜻이다.")
    a("")
    a("## 2. 원천 기사 수집 (§4.2)")
    a("")
    a("| 단계 | 값 |")
    a("|---|---|")
    a(f"| 인용문 검색 구간 (월 × 계열) | {disc.get('windows', '—')} |")
    a(f"| 구간 총 적중 합계 (total_hits) | {disc.get('quotation_total_hits', '—')} |")
    a(f"| 받은 인용문 (점수 상위 표본) | {disc.get('raw_quotations', '—')} |")
    a(f"| 공공 주체 + 확약·계획 발언 | {disc.get('public_promise_quotes', '—')} |")
    a(f"| 제목 재난 어휘·국내 필터 후 (인용문 / 기사) | {disc.get('source_quotes', '—')} / {disc.get('source_articles', '—')} |")
    dd = disc.get("detail_dropped") or {}
    if dd:
        a(f"| 상세 조회 뒤 제외 | {', '.join(f'{k} {v}' for k, v in dd.items())} |")
    iss = disc.get("issue") or {}
    if iss:
        a(f"| 이슈 랭킹 (일 / 토픽 / 재난 토픽 / 3계열 이슈 사건) | {iss.get('days')} / {iss.get('topics')} / {iss.get('disaster_topics')} / {iss.get('issue_events')} |")
        a(f"| 이슈 사건 중 인용문 경로와 겹치지 않은 것 → 보강 검색 / 추가 원천 인용문 | {iss.get('unmatched')} → {iss.get('followup_searched')} / {iss.get('followup_quotes')} |")
    sd = st.get("seed") or {}
    a(f"| 유니크화 (지역·시설·±14일·기관) 사건 그룹 | {sd.get('groups', '—')} |")
    qc = sd.get("query_calibration") or {}
    if qc:
        a(f"| 시드 질의 건수 보정 (상한 {qc.get('limit')}건): 후보 / 상한 안 / 좁힌 질의 | {qc.get('pool', '—')} / {qc.get('within_limit', '—')} / {qc.get('calibrated', '—')} |")
    a(f"| 자동 시드 (선택, 계열×연도 층별 점수순) | {sd.get('unique', '—')} |")
    a(f"| 잘린 구간 (점수 상위만 받음) | {(st.get('discover') or {}).get('truncated_windows', '—')} |")
    lk = st.get("link") or {}
    fu = st.get("followup") or {}
    a(f"| 원천 기사 연결 (link 단계): 게이트 통과 / 원천 기사 사유로 확정 | {lk.get('source_linked_auto', '—')} / {lk.get('source_linked_pinned', '—')} |")
    pf = fu.get("promise_followups") or {}
    if pf:
        a(f"| 약속 키워드 재검색 (사건 / 검색 / 건수 초과·키워드 없음으로 생략 / 받은 기사) | {pf.get('events')} / {pf.get('searched')} / {pf.get('skipped')} / {pf.get('fetched')} |")
    ex = fu.get("extract") or {}
    if ex:
        a(f"| 인용문 → 약속 (연결 기사 인용문 / 공공 주체 / 추적 약속) | {ex.get('quotes')} / {ex.get('public')} / {ex.get('promises')} |")
    a("")
    a("## 3. 상태 분포")
    a("")
    a("| 상태 | 사건 수 |")
    a("|---|---|")
    for s in STATUSES:
        a(f"| {s} | {ev['by_status'].get(s, 0)} |")
    a("")
    a("## 4. 재난 유형별")
    a("")
    a("| 계열 | 사건 | 후속보도 확인 | 비율 |")
    a("|---|---|---|---|")
    for r in latest["by_incident_type"]:
        a(f"| {r['incident_type']} | {r['events']} | {r['with_followup']} | {_pct(r['rate'])} |")
    a("")
    a("## 5. 기관별 (정규화 기관, 후속보도 확인 건수·비율 내림차순 상위 10)")
    a("")
    a(f"정규화 기관 수: {len(latest['by_org'])}")
    a("")
    a("| 순위 | 기관 | 사건 | 후속보도 확인 | 비율 | 마지막 후속 신호 |")
    a("|---|---|---|---|---|---|")
    for i, r in enumerate(latest["by_org"][:10], 1):
        a(f"| {i} | {r['org']} | {r['events']} | {r['with_followup']} | {_pct(r['rate'])} | {_v(r['latest_followup_at'])} |")
    a("")
    a("### 기관 정규화 전후 (발언 주체 원문 → 정규 키, 빈도 상위 25)")
    a("")
    a("| 원문 | 정규화 | 출처 | 건수 |")
    a("|---|---|---|---|")
    for r in org_rows[:25]:
        a(f"| {scrub(r['원문'])} | {r['정규화']} | {r['출처']} | {r['건수']} |")
    a("")
    a("## 6. 시계열 (월별 발생 사건 · 후속 기사)")
    a("")
    a("| 월 | 발생 사건 | 후속 기사 | 사망 | 부상 |")
    a("|---|---|---|---|---|")
    for r in latest["timeseries"]:
        if r["occurred"] or r["followup_articles"]:
            a(f"| {r['bucket']} | {r['occurred']} | {r['followup_articles']} | {_v(r['dead'])} | {_v(r['injured'])} |")
    a("")
    a("피해 규모는 원천 기사 그룹(제목·본문 200자) 정규식 최댓값이고, 없으면 비워 둔다(추정하지 않는다).")
    a("")
    a("## 7. 후속보도가 확인된 사건")
    a("")
    a("| 사건 | 발생일 | 기관 | 상태 | 첫 유효 신호 | 근거 기사 |")
    a("|---|---|---|---|---|---|")
    for f in sorted([f for f in facts if f.has_followup], key=lambda f: f.event.occurred_at):
        s, art = f.first_valid
        a(f"| {scrub(f.event.title)[:50]} | {f.event.occurred_at} | {f.org} | {f.event.status} | {s.signal} ({s.confidence:.2f}) «{scrub(s.evidence_span)[:60]}» | {scrub(art.title)[:40]} ({art.provider}, {art.published_at}) |")
    a("")
    a("## 8. 사람 라벨 대비 (§4.5)")
    a("")
    a(f"- 라벨된 사건: {lab['labeled']}건 (분모 = 라벨된 건수) · 군집 표본 라벨: {lab.get('_cluster_labeled', 0)}건 · 기관 쌍 라벨: {lab.get('_org_labeled', 0)}건")
    a(f"- 후속보도 탐지 precision {_v(lab['followup_precision'])} · recall {_v(lab['followup_recall'])} · 군집 정밀도 {_v(lab['cluster_precision'])} · 기관 정규화 정확도 {_v(lab['org_accuracy'])}")
    a("- 라벨 파일: `backend/gold/experiment_labels.csv`, `cluster_sample.csv`, `org_sample.csv` — 사람 열(O/X)을 채우고 `scripts/experiment.py --stage report` 를 다시 돌리면 이 절이 갱신된다.")
    a("")
    a("## 9. 비용")
    a("")
    a("| 단계 | 요청 수(엔드포인트별) | 네트워크 호출 | 소요(초) |")
    a("|---|---|---|---|")
    total_req = total_net = 0
    for name in ("discover", "seed", "collect", "collect_events", "link", "followup", "report"):
        e = st.get(name)
        if not e:
            continue
        req = e.get("api_calls") or {}
        net = int(e.get("network_calls") or 0)
        total_req += sum(req.values())
        total_net += net
        a(f"| {name} | {', '.join(f'{k} {v}' for k, v in sorted(req.items())) or '—'} | {net} | {e.get('elapsed_sec', 0)} |")
    a(f"| **합계** | {total_req} | {total_net} | {latest['cost']['elapsed_sec']} |")
    a("")
    a(f"녹화 파일 수(누적, `fixtures/bigkinds/recordings`): {latest['cost']['recordings']} — 같은 기준일(`--today {latest['as_of']}`)로 다시 돌리면 녹화를 재생해 호출 비용이 없다.")
    a("")
    a("## 10. 한계")
    a("")
    a("- 인용문 검색은 실서버에서 기사 본문까지 매칭해 (월, 계열)당 1만 건 이상이 나온다. 전수 대신 `_score` 상위 표본만 받았다(잘린 구간 수 위 표). 표본 밖의 원천 기사는 이번 실험에 없다.")
    if rules:
        a(f"- 약속 추출·후속 신호 분류는 LLM 없이 규칙({rules})이다. 행동 키워드가 기사 문장과 겹치지 않는 후속 기사(다른 표현)는 '불명확'으로 남아 후속보도 수가 실제보다 적게 잡힐 수 있다.")
    a(f"- 사건 군집은 기존 연결 엔진(규칙 게이트 + 임베딩)에 사건별 단어 가중치를 더했다 (WEIGHT_MIN={settings.weight_min} · WEIGHT_ALPHA={settings.weight_alpha} · WEIGHT_RULE_MIN={settings.weight_rule_min}). 같은 시설·지역의 다른 사건 기사가 남을 수 있어 `cluster_sample.csv` 로 사람이 확인한다.")
    a("- 기관 정규화는 사전 기반이다. 사전에 없는 기관은 원문 그대로 남는다(`org_sample.csv`).")
    a("- 보도가 없다는 사실은 조치가 없었다는 근거가 아니다. 후속보도가 확인되지 않은 사건은 기관 공식 게시판 확인(트랙 C)의 대상이다.")
    return "\n".join(L) + "\n"
