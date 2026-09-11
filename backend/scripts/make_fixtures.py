"""예선용 픽스처 생성기.

지침서(newstore V1.0) 응답 스키마를 필드명·타입·중첩 구조까지 그대로 따르는 **가상 사건** 코퍼스를 만든다.
- 가상 지명·기관·시설(가온시, 누리군 …). 실존 사건·인물의 발언을 지어내지 않는다. 모든 문서에 "_fixture": true.
- 8개 사건이 5개 상태를 모두 만들도록 설계했다 (아래 EVENTS 의 expected_status).

산출물:
  fixtures/bigkinds/articles.json, quotations.json, issues.json, change_tracker.json
  fixtures/llm/stub_answers.json      (LLM_MODE=stub 정답)
  seeds/events.yaml                   (추적 대상 시드)
  gold/events.yaml                    (정답 데이터셋 — 픽스처 기준)
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "부산일보": "01500701",
    "경남신문": "01500051",
    "경남도민일보": "01500151",
    "국제신문": "01500401",
    "경남일보": "01500101",
    "KBS": "08100101",
    "MBC": "08100201",
    "YTN": "08100401",
    "경향신문": "01100101",
    "한겨레": "01101001",
}
BYLINES = ["김가온", "박누리", "이해솔", "정다온", "최서리", "한별빛"]

# ---------------------------------------------------------------------------
# 사건 정의
# role: initial | investigation | response | progress | result | problem | unrelated | noise
# quotes: [(source, quotation, promise_answer|None)]
# signal: 후속 기사의 stub 정답 (FollowupSignal 필드 + promise_index)
# ---------------------------------------------------------------------------


def A(day, prov, title, content, role, *, inc=None, cat=None, quotes=None, signal=None, link=True, hour=9):
    return {
        "day": day,
        "prov": prov,
        "title": title,
        "content": content,
        "role": role,
        "inc": inc,
        "cat": cat,
        "quotes": quotes or [],
        "signal": signal,
        "link": link,
        "hour": hour,
    }


def P(actor_org, action, deadline_raw, deadline_date, precision, strength, conf, rationale, actor_raw=None):
    return {
        "is_promise": strength != "해당없음",
        "actor_org": actor_org,
        "actor_raw": actor_raw,
        "action": action,
        "deadline_raw": deadline_raw,
        "deadline_date": deadline_date,
        "deadline_precision": precision,
        "strength": strength,
        "confidence": conf,
        "rationale": rationale,
    }


def S(signal, promise_index, matches, span, conf, rationale):
    return {
        "signal": signal,
        "promise_index": promise_index,
        "matches_promise": matches,
        "evidence_span": span,
        "confidence": conf,
        "rationale": rationale,
    }


EVENTS = [
    # 1 --------------------------------------------------------------- 후속보도 부족 (R4 stale)
    {
        "key": "gaon-underpass-flood",
        "title": "가온시 달빛지하차도 침수 사고",
        "incident_type": "재해>자연재해>홍수",
        "region": "가온시",
        "facility": "달빛지하차도",
        "responsible_org": "가온시",
        "org_aliases": ["가온시청", "가온시장"],
        "keywords": ["지하차도 침수", "달빛지하차도"],
        "occurred_at": "2025-07-15",
        "summary": "2025년 7월 15일 밤 집중호우로 가온시 달빛지하차도가 침수돼 차량 12대가 고립되고 2명이 다쳤다. 배수펌프 용량 부족이 원인으로 지목됐다.",
        "expected_status": "후속보도 부족",
        "articles": [
            A(0, "부산일보", "가온시 달빛지하차도 침수… 차량 12대 고립·2명 부상",
              "15일 밤 시간당 80mm 집중호우로 가온시 달빛지하차도가 침수돼 차량 12대가 고립됐다. 소방당국은 운전자 등 15명을 구조했으며 2명이 병원으로 옮겨졌다. 지하차도는 전면 통제됐다.",
              "initial", inc="재해>자연재해>홍수", hour=23,
              quotes=[("가온소방서 관계자", "밤 10시 40분쯤 신고를 받고 출동해 고립된 15명을 모두 구조했다",
                       P("가온소방서", None, None, None, "none", "해당없음", 0.95, "구조 경과 설명으로 약속이 아님", "가온소방서 관계자"))]),
            A(1, "KBS", "달빛지하차도 침수 하루 만에 통행 재개… 배수펌프 작동 여부 조사",
              "가온시는 16일 오후 달빛지하차도 통행을 재개했다. 침수 당시 배수펌프 3대 중 2대가 작동하지 않았다는 증언이 나와 시는 원인 조사에 착수했다.",
              "initial", inc="재해>자연재해>홍수"),
            A(1, "경남신문", "\"배수펌프 용량 절반\"… 달빛지하차도 침수 예견된 사고였나",
              "달빛지하차도 배수펌프 용량이 설계 기준의 절반 수준이었다는 지적이 나왔다. 가온시의회는 침수 경위와 관리 실태를 점검하기로 했다.",
              "initial", inc="재해>자연재해>홍수"),
            A(3, "MBC", "가온시장 \"내년 10월까지 달빛지하차도 배수펌프 2배 증설\"",
              "가온시장은 18일 사고 현장을 찾아 내년 10월까지 배수펌프 용량을 2배로 늘리고 진입 차단시설을 설치하겠다고 밝혔다. 침수 감지 시 자동 차단되는 시스템도 검토한다.",
              "response", inc="재해>자연재해>홍수",
              quotes=[("가온시장", "내년 10월까지 달빛지하차도 배수펌프 용량을 2배로 늘리겠다",
                       P("가온시", "달빛지하차도 배수펌프 용량을 2배로 증설한다", "내년 10월까지", "2026-10-31", "month", "확약", 0.92, "기한과 행동이 명시된 단정형 발언", "가온시장")),
                      ("가온시장", "지하차도 진입 차단시설을 설치하고 침수 감지 시 자동으로 차단되는 시스템도 검토하겠다",
                       P("가온시", "달빛지하차도 진입 차단시설을 설치한다", None, None, "none", "계획", 0.8, "행동은 구체적이나 기한이 없음", "가온시장"))]),
            A(4, "경남도민일보", "가온시 \"지하차도 침수 재발 방지 대책 마련\"… 예산은 미정",
              "가온시가 달빛지하차도 침수 재발 방지 대책을 발표했지만 배수펌프 증설 예산 확보 방안은 제시하지 않았다. 시의회는 추경 편성을 요구했다.",
              "response", inc="재해>자연재해>홍수",
              quotes=[("가온시 안전건설국장", "배수펌프 증설과 차단시설 설치를 위한 예산을 추경에 반영하도록 노력하겠다",
                       P("가온시", None, None, None, "none", "의사표명", 0.7, "노력하겠다는 의사 표명으로 행동이 특정되지 않음", "가온시 안전건설국장"))]),
            A(36, "부산일보", "달빛지하차도 침수 원인은 '펌프 용량 부족·경보 지연'… 감사 결과 발표",
              "가온시 감사 결과 달빛지하차도 침수는 배수펌프 용량 부족과 침수 경보 지연이 겹친 결과로 확인됐다. 담당 부서 직원 3명이 징계 대상에 올랐다.",
              "investigation", inc="재해>자연재해>홍수",
              signal=S("무관", None, False, "", 0.8, "감사 결과와 징계 보도로 조치 이행과 무관")),
            A(233, "경남신문", "장마 앞두고 가온시 지하차도 점검… 달빛지하차도 펌프 증설은 \"시점 미정\"",
              "가온시가 장마철을 앞두고 관내 지하차도 6곳을 점검했다. 달빛지하차도 배수펌프 증설에 대해 시는 설계 검토 중이며 착공 시점은 정해지지 않았다고 밝혔다.",
              "unrelated", inc=None, cat=["지역>경남", "사회>사건_사고"],
              signal=S("불명확", 0, True, "착공 시점은 정해지지 않았다", 0.55, "증설 언급은 있으나 진행 여부를 확인할 근거 문장이 없음")),
            A(340, "국제신문", "달빛지하차도 침수 1년… 주민 \"달라진 게 없다\"",
              "달빛지하차도 침수 사고 1년을 앞두고 주민들이 배수펌프 증설이 아직 이뤄지지 않았다며 불안을 호소했다. 시는 절차를 진행 중이라고만 답했다.",
              "unrelated", inc=None, cat=["지역>경남"],
              signal=S("불명확", 0, True, "절차를 진행 중이라고만 답했다", 0.5, "진행을 확인할 구체 근거(착공·발주 등)가 없어 불명확")),
            # 후보이지만 다른 시설 (연결되면 안 됨)
            A(250, "경남일보", "가온시 별빛지하차도 정기 안전점검 실시",
              "가온시가 별빛지하차도 정기 안전점검을 실시했다. 배수펌프와 조명 설비를 점검했으며 특이사항은 없었다.",
              "noise", inc=None, cat=["지역>경남"], link=False),
        ],
    },
    # 2 --------------------------------------------------------------- 조치 완료 근거 확인 (R2)
    {
        "key": "nuri-tunnel-collapse",
        "title": "누리군 해맞이터널 붕괴 사고",
        "incident_type": "사고>산업사고>붕괴",
        "region": "누리군",
        "facility": "해맞이터널",
        "responsible_org": "누리군",
        "org_aliases": ["누리군청", "누리군수"],
        "keywords": ["터널 붕괴", "해맞이터널"],
        "occurred_at": "2025-03-02",
        "summary": "2025년 3월 2일 누리군 해맞이터널 입구 상부 구조물이 붕괴해 차량 3대가 파손되고 1명이 중상을 입었다. 노후 콘크리트 박리가 원인으로 조사됐다.",
        "expected_status": "조치 완료 근거 확인",
        "articles": [
            A(0, "경남신문", "누리군 해맞이터널 입구 붕괴… 차량 3대 파손, 1명 중상",
              "2일 오전 누리군 해맞이터널 입구 상부 콘크리트 구조물이 무너져 지나던 차량 3대가 파손되고 운전자 1명이 중상을 입었다. 터널은 양방향 전면 통제됐다.",
              "initial", inc="사고>산업사고>붕괴"),
            A(0, "YTN", "해맞이터널 붕괴, 준공 32년 노후 터널… 정밀안전진단 D등급",
              "붕괴한 누리군 해맞이터널은 1993년 준공된 노후 터널로 지난해 정밀안전진단에서 D등급을 받았던 것으로 확인됐다.",
              "initial", inc="사고>산업사고>붕괴", hour=15),
            A(2, "부산일보", "누리군수 \"해맞이터널 연말까지 전 구간 보강 후 재개통\"",
              "누리군수는 4일 해맞이터널 붕괴 현장에서 연말까지 전 구간 보강공사를 마치고 재개통하겠다고 밝혔다. 공사 기간 우회도로를 운영한다.",
              "response", inc="사고>산업사고>붕괴",
              quotes=[("누리군수", "올해 연말까지 해맞이터널 전 구간 보강공사를 마치고 재개통하겠다",
                       P("누리군", "해맞이터널 전 구간 보강공사를 마치고 재개통한다", "올해 연말까지", "2025-12-31", "year", "확약", 0.93, "기한(연말)과 행동이 명시된 확약", "누리군수"))]),
            A(20, "경남도민일보", "해맞이터널 붕괴 원인 '콘크리트 박리·배수 불량'… 국토부 조사 결과",
              "국토교통부 사고조사위원회는 해맞이터널 붕괴 원인을 노후 콘크리트 박리와 배수 불량으로 결론지었다. 위원회는 전국 노후 터널 점검을 권고했다.",
              "investigation", inc="사고>산업사고>붕괴",
              signal=S("무관", None, False, "", 0.85, "원인 조사 결과 보도로 보강공사 진행 여부와 무관")),
            A(69, "KBS", "누리군 해맞이터널 보강공사 착공… 12월 재개통 목표",
              "누리군이 10일 해맞이터널 보강공사에 착공했다. 군은 12월 재개통을 목표로 상부 구조물 철거와 라이닝 보강을 진행한다.",
              "progress", inc=None, cat=["지역>경남", "사회>사건_사고"],
              signal=S("진행", 0, True, "해맞이터널 보강공사에 착공했다", 0.85, "착공 보도로 약속된 보강공사가 진행 중")),
            A(295, "경남신문", "해맞이터널 보강공사 완료… 붕괴 9개월 만에 재개통",
              "누리군 해맞이터널이 22일 재개통됐다. 군은 상부 구조물 재시공과 전 구간 라이닝 보강을 완료했으며 정밀안전진단에서 B등급을 받았다고 밝혔다.",
              "result", inc=None, cat=["지역>경남", "사회>사건_사고"],
              signal=S("완료", 0, True, "전 구간 라이닝 보강을 완료했으며", 0.9, "완료형 표현과 재개통 사실이 약속 내용과 일치")),
            A(340, "국제신문", "해맞이터널 붕괴 책임 공무원 2명 기소",
              "검찰은 해맞이터널 붕괴 사고와 관련해 안전점검을 소홀히 한 혐의로 누리군 공무원 2명을 불구속 기소했다.",
              "unrelated", inc=None, cat=["사회>사건_사고"],
              signal=S("무관", None, False, "", 0.9, "기소 보도로 조치 이행과 무관")),
            # 같은 지역 유사 사고 (다른 터널) — 검토 큐 후보
            A(400, "경남일보", "누리군 달빛터널 낙석… 노후 터널 안전 우려 재점화",
              "누리군 달빛터널에서 낙석이 발생해 차량 1대가 파손됐다. 지난해 터널 붕괴 사고 이후 군내 노후 터널 안전 우려가 다시 커지고 있다.",
              "noise", inc="사고>산업사고>붕괴", link=False),
        ],
    },
    # 3 --------------------------------------------------------------- 새로운 문제 발생 (R1)
    {
        "key": "gaon-seori-levee",
        "title": "가온시 서리천 제방 유실",
        "incident_type": "재해>자연재해>태풍",
        "region": "가온시",
        "facility": "서리천 제방",
        "responsible_org": "가온시",
        "org_aliases": ["가온시청", "가온시장"],
        "keywords": ["제방 유실", "서리천"],
        "occurred_at": "2024-09-20",
        "summary": "2024년 9월 20일 태풍 '해온' 북상으로 가온시 서리천 제방 120m가 유실돼 인근 주택 40가구가 침수됐다.",
        "expected_status": "새로운 문제 발생",
        "articles": [
            A(0, "부산일보", "태풍 '해온'에 가온시 서리천 제방 120m 유실… 주택 40가구 침수",
              "20일 새벽 태풍 해온이 몰고 온 폭우로 가온시 서리천 제방 120m 구간이 유실돼 인근 주택 40가구가 침수됐다. 주민 90여 명이 대피했다.",
              "initial", inc="재해>자연재해>태풍", hour=8),
            A(1, "MBC", "서리천 제방, 2019년에도 유실… 임시 복구만 반복",
              "이번에 유실된 가온시 서리천 제방은 2019년 태풍 때도 무너졌던 구간으로 임시 복구만 반복돼 왔다는 지적이 나온다.",
              "initial", inc="재해>자연재해>태풍"),
            A(3, "경남신문", "가온시 \"서리천 제방 내년 우기 전 전면 보강\"",
              "가온시는 23일 서리천 유실 구간을 포함해 제방 1.2km를 내년 6월 우기 전까지 전면 보강하겠다고 발표했다. 국비 60억 원을 신청했다.",
              "response", inc="재해>자연재해>태풍",
              quotes=[("가온시 하천과장", "내년 6월 우기 전까지 서리천 제방 1.2km 구간을 전면 보강하겠다",
                       P("가온시", "서리천 제방 1.2km 구간을 전면 보강한다", "내년 6월 우기 전까지", "2025-06-30", "month", "확약", 0.9, "기한과 구간이 명시된 확약", "가온시 하천과장"))]),
            A(120, "경남도민일보", "서리천 제방 보강공사 착공… 6월 완공 목표",
              "가온시가 서리천 제방 보강공사에 착공했다. 시는 유실 구간을 콘크리트 옹벽으로 교체하고 6월까지 완공할 계획이다.",
              "progress", inc=None, cat=["지역>경남"],
              signal=S("진행", 0, True, "서리천 제방 보강공사에 착공했다", 0.85, "착공 보도")),
            A(237, "KBS", "가온시 서리천 제방 보강공사 완료… 우기 전 마무리",
              "가온시는 15일 서리천 제방 1.2km 보강공사를 완료했다고 밝혔다. 유실 구간은 콘크리트 옹벽으로 교체됐고 나머지 구간은 사석 보강을 마쳤다.",
              "result", inc=None, cat=["지역>경남"],
              signal=S("완료", 0, True, "서리천 제방 1.2km 보강공사를 완료했다", 0.9, "완료형 표현, 약속 구간과 일치")),
            A(660, "부산일보", "서리천 제방 또 유실… 보강 구간 바로 옆 30m 무너져",
              "12일 집중호우로 가온시 서리천 제방이 또 유실됐다. 무너진 30m 구간은 지난해 보강공사 구간 바로 옆으로, 주택 10가구가 침수됐다.",
              "problem", inc="재해>자연재해>홍수",
              signal=S("새로운문제", 0, True, "서리천 제방이 또 유실됐다", 0.88, "같은 시설에서 재발한 문제")),
            A(662, "경남신문", "\"보강 범위 좁았다\"… 서리천 재유실에 가온시 재조사 착수",
              "서리천 제방 재유실과 관련해 가온시는 보강 범위와 설계 적정성을 재조사하기로 했다. 주민들은 전 구간 재점검을 요구했다.",
              "problem", inc="재해>자연재해>홍수",
              signal=S("새로운문제", 0, True, "서리천 재유실에 가온시 재조사 착수", 0.8, "재발에 따른 재조사 보도")),
        ],
    },
    # 4 --------------------------------------------------------------- 조치 진행 정황 확인 (R3), 기한 없음
    {
        "key": "nuri-train-derail",
        "title": "누리군 화물열차 탈선 사고",
        "incident_type": "사고>교통사고>철도사고",
        "region": "누리군",
        "facility": "누리선",
        "responsible_org": "한국철도공사",
        "org_aliases": ["코레일", "철도공사"],
        "keywords": ["화물열차 탈선", "누리선"],
        "occurred_at": "2025-11-08",
        "summary": "2025년 11월 8일 누리군 누리선 구간에서 화물열차 6량이 탈선해 인근 주택으로 컨테이너가 떨어졌다. 노후 선로 균열이 원인으로 지목됐다.",
        "expected_status": "조치 진행 정황 확인",
        "articles": [
            A(0, "경남신문", "누리군 누리선 화물열차 탈선… 컨테이너 주택 덮쳐 1명 경상",
              "8일 오전 누리군 누리선 구간에서 화물열차 6량이 탈선해 컨테이너 2개가 인근 주택으로 떨어졌다. 주민 1명이 경상을 입었고 누리선 운행이 중단됐다.",
              "initial", inc="사고>교통사고>철도사고"),
            A(1, "YTN", "누리선 탈선 원인은 선로 균열… 40년 넘은 노후 구간",
              "누리선 화물열차 탈선 사고 원인이 선로 균열로 파악됐다. 사고 구간은 1980년대 부설된 노후 선로로 교체 시기가 지났다는 지적이 나온다.",
              "initial", inc="사고>교통사고>철도사고"),
            A(3, "부산일보", "철도공사 \"누리선 노후 선로 교체 추진\"… 시점은 안 밝혀",
              "한국철도공사는 11일 누리선 탈선 구간을 포함한 노후 선로 교체를 추진하겠다고 밝혔다. 다만 구체적인 교체 시점과 예산은 제시하지 않았다.",
              "response", inc="사고>교통사고>철도사고",
              quotes=[("한국철도공사 관계자", "누리선 탈선 구간을 포함한 노후 선로 교체를 추진하겠다",
                       P("한국철도공사", "누리선 노후 선로를 교체한다", None, None, "none", "계획", 0.82, "행동은 구체적이나 기한이 없음", "한국철도공사 관계자"))]),
            A(30, "KBS", "항공철도사고조사위 \"누리선 탈선, 선로 피로 균열이 원인\"",
              "항공철도사고조사위원회는 누리선 화물열차 탈선 사고의 원인을 선로 피로 균열로 결론지었다.",
              "investigation", inc="사고>교통사고>철도사고",
              signal=S("무관", None, False, "", 0.85, "사고 원인 조사 결과")),
            A(193, "경남도민일보", "누리선 노후 선로 교체 공사 착공… 8km 구간 내년 완료",
              "한국철도공사가 20일 누리선 노후 선로 교체 공사에 착공했다. 탈선 구간을 포함한 8km 구간이 대상이며 내년 상반기 완료가 목표다.",
              "progress", inc=None, cat=["지역>경남", "경제>산업_기업"],
              signal=S("진행", 0, True, "누리선 노후 선로 교체 공사에 착공했다", 0.85, "착공 보도, 약속 대상과 일치")),
        ],
    },
    # 5 --------------------------------------------------------------- 조치 완료 근거 확인 (R2), 소방서 주체
    {
        "key": "gaon-logistics-fire",
        "title": "가온시 별빛물류센터 화재",
        "incident_type": "사고>산업사고>화재",
        "region": "가온시",
        "facility": "별빛물류센터",
        "responsible_org": "가온소방서",
        "org_aliases": ["가온소방", "소방당국"],
        "keywords": ["물류센터 화재", "별빛물류센터"],
        "occurred_at": "2025-05-21",
        "summary": "2025년 5월 21일 가온시 별빛물류센터에서 불이 나 창고 2개 동이 전소됐다. 스프링클러가 작동하지 않은 것으로 조사됐다.",
        "expected_status": "조치 완료 근거 확인",
        "articles": [
            A(0, "부산일보", "가온시 별빛물류센터 화재… 창고 2개동 전소, 인명피해 없어",
              "21일 새벽 가온시 별빛물류센터에서 불이 나 창고 2개 동이 전소됐다. 인명피해는 없었으나 스프링클러가 작동하지 않은 것으로 파악됐다.",
              "initial", inc="사고>산업사고>화재", hour=6),
            A(2, "경남신문", "가온소방서 \"관내 물류창고 8월까지 전수 소방점검\"",
              "가온소방서는 23일 별빛물류센터 화재를 계기로 관내 물류창고 34곳에 대해 8월 말까지 전수 소방점검을 완료하겠다고 밝혔다.",
              "response", inc="사고>산업사고>화재",
              quotes=[("가온소방서장", "관내 물류창고 34곳에 대해 8월 말까지 전수 소방점검을 완료하겠다",
                       P("가온소방서", "관내 물류창고 34곳 전수 소방점검을 완료한다", "8월 말까지", "2025-08-31", "month", "확약", 0.93, "기한·대상·행동이 명시된 확약", "가온소방서장"))]),
            A(35, "MBC", "별빛물류센터 화재 원인은 전기 배선… 스프링클러 밸브 잠겨 있었다",
              "가온소방서 조사 결과 별빛물류센터 화재는 전기 배선 합선이 원인이며 스프링클러 주밸브가 잠겨 있어 작동하지 않은 것으로 확인됐다.",
              "investigation", inc="사고>산업사고>화재",
              signal=S("무관", None, False, "", 0.85, "화재 원인 조사 결과")),
            A(104, "경남도민일보", "가온소방서, 물류창고 34곳 전수점검 완료… 12곳 개선명령",
              "가온소방서가 관내 물류창고 34곳에 대한 전수 소방점검을 완료했다. 스프링클러 밸브 폐쇄 등 위반이 확인된 12곳에는 개선명령을 내렸다.",
              "result", inc=None, cat=["지역>경남", "사회>사건_사고"],
              signal=S("완료", 0, True, "물류창고 34곳에 대한 전수 소방점검을 완료했다", 0.92, "완료형 표현, 대상·행동이 약속과 일치")),
        ],
    },
    # 6 --------------------------------------------------------------- 후속보도 부족 (R4), 기한 경과 → 최상단
    {
        "key": "nuri-landslide",
        "title": "누리군 청솔산 산사태",
        "incident_type": "재해>자연재해>눈사태_산사태",
        "region": "누리군",
        "facility": "청솔산",
        "responsible_org": "누리군",
        "org_aliases": ["누리군청", "누리군수"],
        "keywords": ["산사태", "청솔산"],
        "occurred_at": "2025-08-03",
        "summary": "2025년 8월 3일 집중호우로 누리군 청솔산 자락에서 산사태가 발생해 주택 5채가 매몰되고 1명이 숨졌다.",
        "expected_status": "후속보도 부족",
        "articles": [
            A(0, "경남신문", "누리군 청솔산 산사태… 주택 5채 매몰, 1명 사망",
              "3일 새벽 집중호우로 누리군 청솔산 자락에서 산사태가 발생해 주택 5채가 매몰됐다. 1명이 숨지고 3명이 구조됐으며 주민 60여 명이 대피했다.",
              "initial", inc="재해>자연재해>눈사태_산사태", hour=7),
            A(1, "KBS", "청솔산 산사태 지역, 산사태 취약지역 지정 안 돼 있었다",
              "산사태가 난 누리군 청솔산 자락은 산림청 산사태 취약지역으로 지정되지 않아 사방시설이 전혀 없었던 것으로 드러났다.",
              "initial", inc="재해>자연재해>눈사태_산사태"),
            A(4, "부산일보", "누리군 \"청솔산 사방댐 2곳 내년 6월까지 설치\"",
              "누리군은 7일 청솔산 산사태 피해 지역에 사방댐 2곳을 내년 6월까지 설치하고 취약지역 지정을 신청하겠다고 밝혔다.",
              "response", inc="재해>자연재해>눈사태_산사태",
              quotes=[("누리군수", "청솔산 피해 지역에 사방댐 2곳을 내년 6월까지 설치하겠다",
                       P("누리군", "청솔산 피해 지역에 사방댐 2곳을 설치한다", "내년 6월까지", "2026-06-30", "month", "확약", 0.92, "기한과 행동이 명시된 확약", "누리군수"))]),
            A(129, "경남도민일보", "청솔산 산사태 이재민 임시주거 지원 연장",
              "누리군은 청솔산 산사태 이재민 5가구에 대한 임시주거 지원을 내년 3월까지 연장하기로 했다.",
              "unrelated", inc=None, cat=["지역>경남", "사회>노동_복지"],
              signal=S("무관", None, False, "", 0.85, "이재민 지원 보도로 사방댐 설치와 무관")),
        ],
    },
    # 7 --------------------------------------------------------------- 판단 불가 (R5), 추적 가능한 약속 없음
    {
        "key": "gaon-bridge-crash",
        "title": "가온시 한빛대교 접속도로 연쇄 추돌 사고",
        "incident_type": "사고>교통사고>노상사고",
        "region": "가온시",
        "facility": "한빛대교 접속도로",
        "responsible_org": "가온시",
        "org_aliases": ["가온시청", "가온시장"],
        "keywords": ["연쇄 추돌", "한빛대교"],
        "occurred_at": "2026-01-14",
        "summary": "2026년 1월 14일 새벽 가온시 한빛대교 접속도로에서 도로 결빙으로 차량 11대가 연쇄 추돌해 8명이 다쳤다.",
        "expected_status": "판단 불가",
        "articles": [
            A(0, "부산일보", "가온시 한빛대교 접속도로 11중 추돌… 블랙아이스에 8명 부상",
              "14일 새벽 가온시 한빛대교 접속도로에서 도로 결빙으로 차량 11대가 연쇄 추돌해 8명이 다쳤다. 경찰은 블랙아이스가 원인인 것으로 보고 있다.",
              "initial", inc="사고>교통사고>노상사고", hour=7),
            A(2, "경남신문", "가온시 \"한빛대교 결빙 방지 대책 검토\"… 열선·염수 분사 거론",
              "가온시는 16일 한빛대교 접속도로 연쇄 추돌과 관련해 결빙 방지 대책을 검토하겠다고 밝혔다. 열선 설치와 자동 염수 분사 장치가 거론됐으나 확정된 것은 없다.",
              "response", inc="사고>교통사고>노상사고",
              quotes=[("가온시 도로과 관계자", "한빛대교 접속도로 결빙 방지 대책을 다각도로 검토하겠다",
                       P("가온시", None, None, None, "none", "의사표명", 0.85, "검토하겠다는 의사 표명으로 행동이 특정되지 않음", "가온시 도로과 관계자"))]),
            A(37, "KBS", "한빛대교 11중 추돌, 선행 차량 운전자 입건",
              "경찰은 한빛대교 접속도로 연쇄 추돌 사고와 관련해 과속 혐의로 선행 차량 운전자를 입건했다.",
              "unrelated", inc="사고>교통사고>노상사고",
              signal=S("무관", None, False, "", 0.9, "수사 보도로 조치와 무관")),
            A(203, "경남도민일보", "가온시, 겨울철 앞두고 한빛대교 등 결빙 취약구간 점검",
              "가온시가 겨울철을 앞두고 한빛대교 접속도로 등 결빙 취약구간 12곳을 점검했다. 결빙 방지 시설 설치 여부는 언급되지 않았다.",
              "unrelated", inc=None, cat=["지역>경남"],
              signal=S("불명확", None, False, "결빙 방지 시설 설치 여부는 언급되지 않았다", 0.5, "점검 보도이나 추적 중인 구체 약속이 없어 대응 여부를 판단할 수 없음")),
        ],
    },
    # 8 --------------------------------------------------------------- 조치 진행 정황 확인 (R3), 기한 경과 (완료 예정 ≠ 완료)
    {
        "key": "gaon-market-explosion",
        "title": "가온시 남문시장 가스폭발 사고",
        "incident_type": "사고>산업사고>폭발",
        "region": "가온시",
        "facility": "남문시장",
        "responsible_org": "가온시",
        "org_aliases": ["가온시청", "가온시장"],
        "keywords": ["가스폭발", "남문시장"],
        "occurred_at": "2025-10-30",
        "summary": "2025년 10월 30일 가온시 남문시장 점포에서 노후 가스배관 누출로 폭발이 일어나 6명이 다치고 점포 9곳이 파손됐다.",
        "expected_status": "조치 진행 정황 확인",
        "articles": [
            A(0, "부산일보", "가온시 남문시장 가스폭발… 6명 부상·점포 9곳 파손",
              "30일 오후 가온시 남문시장 점포에서 가스폭발이 발생해 6명이 다치고 점포 9곳이 파손됐다. 노후 가스배관 누출이 원인으로 추정된다.",
              "initial", inc="사고>산업사고>폭발", hour=17),
            A(1, "MBC", "남문시장 가스배관 30년 넘어… 점검 기록도 부실",
              "폭발이 난 가온시 남문시장 가스배관은 설치 30년이 넘은 노후 배관으로 최근 3년간 점검 기록이 부실했던 것으로 드러났다.",
              "initial", inc="사고>산업사고>폭발"),
            A(3, "경남신문", "가온시 \"남문시장 노후 가스배관 내년 3월까지 전면 교체\"",
              "가온시는 2일 남문시장 노후 가스배관을 내년 3월까지 전면 교체하겠다고 밝혔다. 교체 비용 12억 원은 시비로 충당한다.",
              "response", inc="사고>산업사고>폭발",
              quotes=[("가온시장", "남문시장 내 노후 가스배관을 내년 3월까지 전면 교체하겠다",
                       P("가온시", "남문시장 노후 가스배관을 전면 교체한다", "내년 3월까지", "2026-03-31", "month", "확약", 0.93, "기한과 행동이 명시된 확약", "가온시장"))]),
            A(96, "경남도민일보", "남문시장 가스배관 교체 공사 착공… 3월 완료 목표",
              "가온시가 3일 남문시장 노후 가스배관 교체 공사에 착공했다. 시는 3월까지 시장 전 구간 배관을 교체할 계획이다.",
              "progress", inc=None, cat=["지역>경남", "사회>사건_사고"],
              signal=S("진행", 0, True, "남문시장 노후 가스배관 교체 공사에 착공했다", 0.87, "착공 보도")),
            A(167, "KBS", "남문시장 가스배관 교체 기한 넘겨… 시 \"5월 완료 예정\"",
              "3월까지 마치기로 했던 가온시 남문시장 가스배관 교체 공사가 기한을 넘겼다. 시는 상인 영업시간을 피해 공사하느라 늦어졌다며 5월 완료 예정이라고 밝혔다.",
              "progress", inc=None, cat=["지역>경남"],
              signal=S("진행", 0, True, "5월 완료 예정이라고 밝혔다", 0.78, "'완료 예정'은 완료가 아니라 진행 중인 상태")),
        ],
    },
]

# 이슈 필터링 검증용 비사건 기사 (오늘의 이슈 클러스터에 섞는다)
FILLERS = [
    ("2025-07-15", "경남신문", "가온시 여름 별빛축제 개막… 사흘간 달빛공원서", "가온시 여름 별빛축제가 15일 달빛공원에서 개막했다. 사흘간 공연과 야시장이 열린다.", ["문화>전시_공연", "지역>경남"]),
    ("2025-03-02", "부산일보", "누리군 봄맞이 농산물 직거래 장터 열려", "누리군이 2일 군청 광장에서 봄맞이 농산물 직거래 장터를 열었다.", ["경제>유통", "지역>경남"]),
    ("2025-10-30", "KBS", "가온시 도서관 야간 개방 확대", "가온시는 11월부터 시립도서관 야간 개방을 밤 10시까지 확대한다고 밝혔다.", ["문화>생활", "지역>경남"]),
]


# ---------------------------------------------------------------------------


def news_id(prov_code: str, dt: datetime, seq: int) -> str:
    return f"{prov_code}.{dt.strftime('%Y%m%d%H%M%S')}{seq:03d}"


def iso_kst(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00")


def keywords_of(title: str, content: str) -> str:
    """tms_raw_stream 대용: 명사처럼 보이는 2자 이상 토큰을 개행으로 잇는다."""
    import re

    toks = re.findall(r"[가-힣A-Za-z0-9]{2,}", title + " " + content)
    seen: list[str] = []
    for t in toks:
        if t.endswith(("다", "며", "고", "해", "돼", "져", "던", "지", "면", "서", "니", "게", "히")):  # 용언·부사 제외
            continue
        if any(ch.isdigit() for ch in t):  # 수량 표현 제외
            continue
        t2 = re.sub(r"(으로|에서|에게|까지|부터|이라|라고|하고|에는|은|는|이|가|을|를|의|에|과|와|로)$", "", t)
        if len(t2) >= 2 and t2 not in seen:
            seen.append(t2)
    return "\n".join(seen[:40])


def build(today: date) -> dict:
    articles: list[dict] = []
    quotations: list[dict] = []
    issues: dict[str, list[dict]] = {}
    stub = {"promises": {}, "signals": {}}
    seeds = {"events": []}
    gold = {"generated_for": "fixture", "today": today.isoformat(), "events": []}

    seq = 0
    for ev in EVENTS:
        occurred = date.fromisoformat(ev["occurred_at"])
        linked_ids: list[str] = []
        followup_ids: list[str] = []
        promise_list: list[dict] = []
        day0_ids: list[str] = []
        for spec in ev["articles"]:
            seq += 1
            dt = datetime.combine(occurred + timedelta(days=spec["day"]), datetime.min.time()) + timedelta(hours=spec["hour"], minutes=seq % 60)
            code = PROVIDERS[spec["prov"]]
            nid = news_id(code, dt, seq % 1000)
            content = spec["content"]
            assert len(content) <= 200, f"content > 200자: {spec['title']}"
            cat = spec["cat"] or ["사회>사건_사고", "지역>경남"]
            inc = [spec["inc"]] if spec["inc"] else []
            doc = {
                "_fixture": True,
                "news_id": nid,
                "title": spec["title"],
                "content": content,
                "published_at": iso_kst(dt.replace(hour=0, minute=0, second=0)),
                "enveloped_at": iso_kst(dt + timedelta(minutes=20)),
                "dateline": iso_kst(dt),
                "provider": spec["prov"],
                "category": cat,
                "category_incident": inc,
                "byline": BYLINES[seq % len(BYLINES)],
                "images": None,
                "images_caption": None,
                "provider_news_id": f"F{seq:06d}",
                "publisher_code": f"F{seq:06d}",
                "provider_link_page": f"https://example.invalid/news/{nid}",
                "printing_page": None,
                "tms_raw_stream": keywords_of(spec["title"], content),
            }
            articles.append(doc)
            if spec["link"]:
                linked_ids.append(nid)
            if spec["day"] <= 1 and spec["link"]:
                day0_ids.append(nid)
            for source, quotation, answer in spec["quotes"]:
                quotations.append(
                    {
                        "_fixture": True,
                        "news_id": nid,
                        "published_at": doc["published_at"],
                        "date": dt.strftime("%Y%m%d"),
                        "provider": spec["prov"],
                        "category": cat,
                        "category_incident": inc,
                        "source": source,
                        "quotation": quotation,
                        "title": spec["title"],
                    }
                )
                if answer is not None:
                    stub["promises"][f"{nid}||{source}||{quotation}"] = answer
                    if answer["strength"] in ("확약", "계획"):
                        promise_list.append({**answer, "evidence_news_id": nid})
            if spec["signal"] is not None:
                stub["signals"][f"{ev['key']}||{nid}"] = spec["signal"]
                followup_ids.append(nid)

        # 오늘의 이슈: 발생일 토픽 + 비사건 필러 토픽
        topic_kw = ",".join(dict.fromkeys(keywords_of(ev["title"], ev["summary"]).split("\n")[:20]))
        issues.setdefault(ev["occurred_at"], []).append(
            {
                "topic": ev["title"],
                "topic_rank": 1 + len(issues.get(ev["occurred_at"], [])),
                "topic_keyword": topic_kw,
                "topic_content": ev["summary"],
                "issue_category": "003000000",
                "news_cluster": day0_ids,
            }
        )
        seeds["events"].append(
            {
                "key": ev["key"],
                "title": ev["title"],
                "incident_type": ev["incident_type"],
                "region": ev["region"],
                "facility": ev["facility"],
                "responsible_org": ev["responsible_org"],
                "org_aliases": ev["org_aliases"],
                "keywords": ev["keywords"],
                "occurred_at": ev["occurred_at"],
                "summary": ev["summary"],
            }
        )
        gold["events"].append(
            {
                "key": ev["key"],
                "title": ev["title"],
                "expected_status": ev["expected_status"],
                "linked_news_ids": linked_ids,
                "followup_news_ids": followup_ids,
                "promises": [
                    {
                        "actor_org": p["actor_org"],
                        "action": p["action"],
                        "deadline_date": p["deadline_date"],
                        "strength": p["strength"],
                        "evidence_news_id": p["evidence_news_id"],
                    }
                    for p in promise_list
                ],
            }
        )

    # 필러 기사 + 필러 토픽
    for day, prov, title, content, cat in FILLERS:
        seq += 1
        dt = datetime.combine(date.fromisoformat(day), datetime.min.time()) + timedelta(hours=11)
        code = PROVIDERS[prov]
        nid = news_id(code, dt, seq % 1000)
        articles.append(
            {
                "_fixture": True,
                "news_id": nid,
                "title": title,
                "content": content,
                "published_at": iso_kst(dt.replace(hour=0)),
                "enveloped_at": iso_kst(dt),
                "dateline": iso_kst(dt),
                "provider": prov,
                "category": cat,
                "category_incident": [],
                "byline": BYLINES[seq % len(BYLINES)],
                "provider_news_id": f"F{seq:06d}",
                "publisher_code": f"F{seq:06d}",
                "provider_link_page": f"https://example.invalid/news/{nid}",
                "tms_raw_stream": keywords_of(title, content),
            }
        )
        issues.setdefault(day, []).append(
            {
                "topic": title,
                "topic_rank": 1 + len(issues[day]),
                "topic_keyword": ",".join(keywords_of(title, content).split("\n")[:10]),
                "topic_content": content,
                "issue_category": "004000000",
                "news_cluster": [nid],
            }
        )

    # 변경이력: 사건2 완료 기사 Update, 필러 1건 Cancelled
    done_article = next(a for a in articles if "해맞이터널 보강공사 완료" in a["title"])
    filler_article = next(a for a in articles if a["title"].startswith("가온시 도서관"))
    stamp = datetime.combine(today, datetime.min.time())
    change_tracker = [
        {
            "media_id": done_article["news_id"].split(".")[0],
            "newsitem_id": done_article["news_id"],
            "news_status": "Update",
            "insert_dt": done_article["dateline"][:19].replace("T", " "),
            "update_dt": (stamp - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S"),
            "pub_date": done_article["published_at"][:10] + " 00:00:00",
            "sync_dt": (stamp - timedelta(hours=11)).strftime("%Y-%m-%d %H:%M:%S"),
        },
        {
            "media_id": filler_article["news_id"].split(".")[0],
            "newsitem_id": filler_article["news_id"],
            "news_status": "Cancelled",
            "insert_dt": filler_article["dateline"][:19].replace("T", " "),
            "update_dt": (stamp - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S"),
            "pub_date": filler_article["published_at"][:10] + " 00:00:00",
            "sync_dt": (stamp - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        },
    ]

    return {
        "articles": articles,
        "quotations": quotations,
        "issues": issues,
        "change_tracker": change_tracker,
        "stub": stub,
        "seeds": seeds,
        "gold": gold,
    }


def write(out: dict, backend: Path) -> None:
    bk = backend / "fixtures" / "bigkinds"
    bk.mkdir(parents=True, exist_ok=True)
    (backend / "fixtures" / "llm").mkdir(parents=True, exist_ok=True)
    (backend / "seeds").mkdir(exist_ok=True)
    (backend / "gold").mkdir(exist_ok=True)

    def dump(path: Path, obj) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    dump(bk / "articles.json", out["articles"])
    dump(bk / "quotations.json", out["quotations"])
    dump(bk / "issues.json", out["issues"])
    dump(bk / "change_tracker.json", out["change_tracker"])
    dump(backend / "fixtures" / "llm" / "stub_answers.json", out["stub"])
    with (backend / "seeds" / "events.yaml").open("w", encoding="utf-8") as f:
        f.write("# 추적 대상 사건 시드 — 예선용 가상 사건. 본선에서는 discover 초안을 바탕으로 사람이 확정한다.\n")
        yaml.safe_dump(out["seeds"], f, allow_unicode=True, sort_keys=False)
    with (backend / "gold" / "events.yaml").open("w", encoding="utf-8") as f:
        f.write("# 정답 데이터셋 — 픽스처 기준(가상). 본선에서는 실제 사건 30건으로 교체한다.\n")
        yaml.safe_dump(out["gold"], f, allow_unicode=True, sort_keys=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--backend", default=str(BACKEND))
    args = ap.parse_args()
    out = build(date.fromisoformat(args.today))
    write(out, Path(args.backend))
    print(
        f"articles={len(out['articles'])} quotations={len(out['quotations'])} issues_days={len(out['issues'])} "
        f"stub_promises={len(out['stub']['promises'])} stub_signals={len(out['stub']['signals'])} events={len(out['seeds']['events'])}"
    )


if __name__ == "__main__":
    main()
