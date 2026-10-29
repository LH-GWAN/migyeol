# 변경 이력 / 개발 범위 구분

공고문 유의사항에 따라 **예선(설계·목업)** 과 **본선(실 API 연동·신규 개발)** 범위를 구분해 기록한다.

## 2026-10-30 진행 단계 '발생' — 발생 직후 기사 + 사고 사실 확인 기사 ('발생' 빈 사건 8 → 1, 지표 그대로)

사용자 지적: "발생은 발생 후 3일 안 기사만이 아니라 발생을 확인한 기사도 포함해야 한다." 100건 중 8건(덱 대표 사례 영도 청동초 스쿨존 포함)이 '발생' 칸이 비어 있었다. 진단·전후: `docs/EXPERIMENT.md` §16, `docs/연구.md` 3.13.

- `pipeline/link.py`: '발생' = 발생 −1 ~ +3일(`PHASE_OCCURRENCE_DAYS`) + 14일(`PHASE_CONFIRM_DAYS`) 안에 제목이 사고 사실(사상자·신원·피해 규모·경위)을 보도하고 원인조사·대응·후속·의견 표현이 없는 기사(`occurrence_report`·`occurrence_window`·`initial_phase`). 게이트는 발생 창 안에서 범위 + 사건 키워드 1개면 주제 조건 통과(`LINK_OCCURRENCE_GATE`, 사유 `occ-window`). 시드 제외어(`exclude_terms`, 사유 `x-exclude`). 발생 창 보강 후보는 가중치 핵심 문서에서 제외.
- `pipeline/judge.py`·`extract.py`: `occ-window` 로만 들어온 연결은 후속 근거·약속 인용문으로 쓰지 않는다 — 발생 창 완화는 화면·군집용이고 판정 재료를 바꾸지 않는다.
- `api/serializers.py`: 발생 3일 안의 '대응발표'(원천) 기사를 타임라인 '발생' 에도 싣는다(단계 값·'대응발표' 칸은 그대로).
- `pipeline/collect.py`·`seeds.py`·`db/models.py`: 시드 `occurrence_query`(발생 −1 ~ +14일만 한 페이지 더, 후보 출처 `occurrence`)·`exclude_terms`. `scripts/experiment.py --events`(collect 를 고친 사건만, 비용 표 `collect_events`).
- `seeds/events.yaml`: 282 영암 HD현대삼호 발생일 07-25 → **07-28**(전남소방본부 신고 23:21~22), summary. 200·225·268 `occurrence_query`. 157 `exclude_terms: [백령도, 모터보트]` — 01-05 백령도 모터보트 전복 10건(다른 사고, 군집 라벨 X)을 뺌(사용자 요청).
- 화면(`Stepper.tsx`·`events/[id]/page.tsx`): 그래도 '발생' 이 비면 "YYYY년 M월 D일 발생 · 첫 보도 미연결". 단계 안내 문구에 '발생' 정의 한 줄.
- 실 API 42회(수집 7 · 약속 재검색 35). 재생(`BIGKINDS_REPLAY_ONLY=true`)으로 같은 결과 확인.
- 전후: '발생' 빈 사건 8 → 1(268 융건릉, 당시 보도 없음). 후속보도 68 · P/R/F1 0.971/0.868/0.917, 상태 42/17/7/32/2, 경과 창·신호 수 **그대로**. 군집 전체 라벨 0.911/0.905 → 0.912/0.907, 활성 연결 18,421 → 19,191(+791 · −21). 새 연결 표본 60건 중 55건 같은 사건 — 남은 56행을 `gold/cluster_full.csv` 에 에이전트 라벨로 추가(22,129행 기준 0.911/0.902 → 0.912/0.908). 리포트 `reports/experiment_20261030_0400.md`·`reports/eval_labels_20261030_occurrence.md`.
- 아래 '이름+직함 기관 검색'(먼저 main 에 들어감)과 병합한 뒤 이 사건 DB 에서 link → followup → report → export_dashboard 를 다시 돌렸다. 실 API 4회 추가(두 변경이 겹쳐 새로 생긴 담양 x20250719-125 약속 재검색), 재생 전용으로 다시 돌려 CSV·지표가 같음을 확인. 병합 뒤: 후속보도 68 · P/R/F1 0.971/0.868/0.917, 상태 42/17/7/32/2, 군집 전체 라벨 0.912/0.908(13,884행 유지), 표본 0.871, 기관 정규화 0.886, 대시보드 기관 65. 리포트 `reports/experiment_20261030_0423.md`·`reports/eval_labels_20261030_0423.md`.
- 처음 측정에서 F1 0.910(오탐 +1)·상태 43/16·거제 고현천 라벨 O 8건 손실이 나와 위 판정 재료 분리로 고쳤다(§16.4).
- 테스트 `tests/test_occurrence_phase.py`(phase·gate·제외어·serializer·수집·판정 분리).

## 2026-10-30 이름+직함 발언 주체를 뉴스 검색으로 기관에 잇기 (기관 정규화 0.837 → 0.886)

사용자 지시: "이름이랑 직함만 있는 건 검색 돌려서 기관 찾아야 한다." `docs/EXPERIMENT.md` §15, `docs/연구.md` 3.12.

- `rules/actor_search.py` (새 파일): '김○○ 지사' 처럼 기관명 없는 이름+직함 주체를 발언일 분기 ± 45일 빅카인즈 뉴스 검색(`"이름" AND "직함"`, 30건)으로 찾아 제목·하이라이트·본문 200자의 '이름 + 기관 + 직함' 표기를 문서 단위로 센다. 근거 ≥ 3건·1위 ≥ 2위 × 2 일 때만 해석(사건 지역 지자체 우선), 아니면 원문 유지. 캐시 `backend/fixtures/actor_orgs.json`((이름, 직함, 분기) → 후보 분포), 녹화 재생으로 같은 결과. 민간 조직 직함·'광역명 + 본부장' 은 세지 않는다.
- `rules/actors.py` `person_title()`·`PERSON_TITLES`, `pipeline/extract.py` `resolve_person_title()`(규칙 결과가 아는 기관이 아니면 캐시로 해석, 책임기관과 같으면 책임기관 표기)·`extract_event` 에서 캐시에 없는 것만 검색(`ACTOR_SEARCH=true`, record/live), `experiment/labels.py` `org_pairs` 도 같은 해석. `scripts/resolve_actors.py`(예상 호출 수 `--dry-run`, 해석·보류 목록 `--summary`).
- `rules/orgs.py`: 광역 + 기초를 함께 적은 이름은 기초 우선('부산 영도구청' → 영도구, '인천 동구' → 동구), `wide_alias()`·`is_known_org()`. `notices/sites.py` 는 기초 게시판이 없으면 광역 게시판(별칭)을 본다.
- `experiment/labels.py`: 정규화가 라벨 때와 달라진 `org_sample.csv` 행은 정답 열과 기계 대조로 O/X 재부여(`_regrade_org`), 같은 원문이 정규화만 다른 두 줄이면 정규화까지 같은 옛 줄의 라벨을 쓴다.
- 실험 DB 적용(실 API 177회 — 검색 131 · 약속 재검색 46, `followup → report → export_dashboard`): 이름+직함 인용문 421건 중 398건 해석, 기관 정규화 **0.837 → 0.886**(350쌍), 시드 100건 약속 중 이름이 기관 칸에 남은 것 264 → 15. 후속보도 P/R/F1 0.971/0.868/0.917 · 군집 0.911/0.905 · 사건 상태 그대로. 대시보드 기관 63 → 65(서울시 4/6 → 4/5, 인천시 3 → 2건 — '서울 강서구'·'인천 동구' 사건이 기초로). README·QA·시연 대본 숫자 갱신. 발표 덱 11번 슬라이드의 기관 정규화 숫자(0.82, 300쌍)는 바꾸지 않았다.
- 테스트 `tests/test_actor_search.py`(47건) — 판별·표기 집계·임계·지역 우선·캐시·녹화 재생·광역+기초·게시판 별칭·라벨 재채점. 전체 617건 통과.

## 2026-10-29 화면 정리 — 「기관」 메뉴, 대시보드 3건, 타임라인·근거 화면 「더보기」

사용자 지시(제출 전날): 화면 네 가지를 표시만 줄인다. 숫자·API 는 바꾸지 않았다. 캡처 `docs/screenshots/1029_*.png`(데스크톱 1280 · 모바일 375).

- **상단 메뉴 「기관」** (`components/SiteHeader.tsx`): 대시보드 · 사건 목록 · **기관** · 보도자료 초안 · 소개 · 관리자. 드롭다운 대신 `/orgs` 상단 탭 두 개(`components/dashboard/OrgTabs.tsx`) — ① 기관별 후속보도 현황(`/orgs`) ② **기관 발표로 확인된 사례**(`/orgs/notices`, 새 화면: 대시보드 패널로만 있던 notice-cases 를 종류별 최대 5건, 사건·기관·발표 제목·게시일·게시판 링크·근거 화면 링크). 사례 카드의 「사건 근거 보기」는 패널이 있는 근거 화면(`/events/{id}/evidence#org-notice-heading`)으로 가도록 고침.
- **대시보드** (`app/page.tsx`): 「최근 후속보도」·「지금 확인이 필요한 사건」 5 → **3건** + 「더 보기」(`/followups`·`/events`).
- **사건 타임라인** (`app/events/[id]/page.tsx`): 단계(발생 → 원인조사 → 대응발표 → 조치진행 → 결과확인)마다 기사 **3건**만, 「○○ 기사 더보기 (N건)」 버튼으로 그 단계 전부 펼침·접기(클라이언트 토글 `components/ShowMore.tsx`, 페이지 이동 없음). 기타 기사도 같음. 약속 카드는 그대로.
- **근거 화면** (`app/events/[id]/evidence/page.tsx`): 「후속 신호」 표 3행 + 「후속 신호 더보기 (N건)」, 「저신뢰 연결」 3건 + 더보기, 「기관 발표 확인」(`notices/OrgNoticePanel.tsx`)·「민간 지원 소식」(`support/SupportPanel.tsx`, 8건 접기 → 3건 + 더보기)도 같은 방식.
- 확인: `tsc --noEmit`·`eslint src` 통과, 375px 에서 가로 넘침 없음(표는 안에서 스크롤), 더보기 → 접기 토글 동작. 시연 대본 상단·§1, README 화면 목록 갱신.

## 2026-10-29 (새벽) F1 0.90 넘기기 — 구절 오독 고침·발생 직후 새로운문제 내림 (rules-v5, F1 0.898 → 0.917, 교차검증 0.914)

사용자 지시: F1 0.90 초과(재현율 우선, 정밀도 0.92 이상), 바꾼 규칙이 사건 단위 교차검증에서도 이길 것. 남은 놓침 10·오탐 5건을 근거 기사까지 따라가 규칙 후보 5개를 만들고, 조합 32개를 모두 돌려 학습 fold 에서 고른 조합을 평가 fold 에 적용했다. `docs/EXPERIMENT.md` §14.6, `docs/연구.md` 3.11 ⑤⑥.

- `llm/rules_adapter.py` (`FOLLOWUP_PHRASE_FIXES=true`, rules-v5): 요구·조건 꼬리('폐로 절차에 착수하라'), '또 화재 당시'(또한), '공사 중이던 냉동창고'(장소 설명)는 상태 표현이 아니다. `FOLLOWUP_SAME_ACTION`(제목의 같은 종류 행동, 기본 끔). 수사·조사 표현 신호에 표지.
- `pipeline/judge.py` (`FOLLOWUP_EARLY_PROBLEM_DAYS=3`): 발생 3일 안 기사의 '새로운문제' 는 사건 자체의 전개라 내린다('7년 만에 또 지진', '비바람에 항공기 지연'). `FOLLOWUP_INVESTIGATION_SOURCE`·`FOLLOWUP_RECURRENCE`(같은 시설 재발) 는 기본 끔.
- 교차검증(사건 5-fold × 나누기 6가지, 조합 선택 포함): rules-v4 0.898 → P·E 후보 **0.914 ± 0.007**(30 fold 중 29 에서 P·E 선택). 같은 행동 제목(A)은 한 사건만 고쳐 교차검증 이득이 없고, 재발(C)은 손해, 수사 출처(I)는 라벨과 어긋나(구로역) 끔.
- 실험 DB 적용(실 API 0회): 후속보도 있음 71 → 68, TP/FP/FN 66/5/10 → **66/2/10**, P/R/F1 0.930/0.868/0.898 → **0.971/0.868/0.917**, 상태 42/17/7/32/2, 군집 전체 라벨 0.911/0.905 그대로, 회귀 사례 그대로. 경주 지진·태풍 종다리·55보급창이 '후속보도 부족' 으로.
- 테스트 `tests/test_followup_v5.py`(12건), `tests/conftest.py` 는 가상 픽스처용으로 새 규칙을 끔. 전체 570건 통과.
- 못 한 것: 재현율은 0.868 그대로 — 재현율 쪽 규칙(A·C)은 교차검증으로 확인되지 않았다. 남은 오탐 2건(야탑동·반얀트리)은 원천 발언과 다른 수사 보도다.
- 문서(같은 날 오전): README 지표 표, `docs/QA.md`·`docs/DEMO_SCRIPT.md` 숫자(63→68건, 37→32건, 0.91/0.75/0.82→0.97/0.87/0.92, 기관 상위 5 국토교통부 6/7·경기도 5/6·서울시 4/6·고용노동부 3/3·한국철도공사 3/3, `rules-v3`→`rules-v5`), `docs/ARCHITECTURE.md` 규칙 모델 이름. 숫자는 건수에서 다시 셌다(66/68·66/76, 군집 12,584/13,810·12,584/13,901).

## 2026-10-29 후속보도 재현율 보강 — 제목형 표현·약속 없는 사건의 직접 후속 (rules-v4, recall 0.75 → 0.868)

사용자 지시: 맹검 전수 라벨 기준 재현율 0.85 이상, 정밀도 0.88 이상. 놓친 19건의 라벨 근거 기사를 단계별로 따라가 원인을 나눴다(상태 표현 사전에 없음 9 · 추적 약속 없음 5 · 근거 검토 대기 3 · 확률 바닥 1 · 약속 발언일 규칙 1). 진단·비교·과적합 확인·전후: `docs/EXPERIMENT.md` §14, `docs/연구.md` 3.11.

- `llm/rules_adapter.py` (rules-v4, `FOLLOWUP_HEADLINE_EXPRESSIONS=true`): 제목형 명사 표현 — 완료(○○ 완료·마무리, 전체·전면 복구, 재가동, 작업중지·통제·제한급수 해제, 조사 결과 '직접 원인·원인이었다·결론'), 진행(복구율, ○억 투입·편성, 구호금 지급, 24시간 순찰, 임시 숙소·상담소 운영, 날짜+착수·개통, 입건·송치·구속영장). 앞날의 '재개장·재개통' 은 진행. 수색·진화·감식의 마무리, 기소·재판, 집중 단속, 공사 이름('전면 복구공사')·'완료 때까지'·앞날 말 옆의 완료, 뒤에 조건·요구(해야·받아야·려면)가 붙은 표현은 넣지 않는다.
- `pipeline/judge.py` (`FOLLOWUP_EVENT_PATH=true`, `FOLLOWUP_EVENT_MIN_PROB=0.8`): 추적 약속이 없는 사건은 시설명·사건 고유어로 만든 가짜 약속(저장하지 않음)으로 재순위 확률 0.8 이상 연결 기사만 분류하고, 신호는 promise_id 없이 일치로 저장 — 상태 규칙 R1~R3 이 그대로 받는다. 조사 결과 표현 신호는 원천 발언 7일 안이면 내린다(같은 발표의 전재).
- `config.py`: 위 설정 3개(기본 켬). `tests/conftest.py` 는 가상 픽스처용으로 끔.
- 비교(실험 DB 사본, 실제 판정 코드): 제목형만 F1 0.861 · 약속 없는 경로만 0.853 · **둘 다 0.892**(확률 0.7·0.8 같음, 0.9 는 0.884). 틀린 '있음' 은 늘지 않았다. 과적합 확인: 새로 맞힌 사건마다 다른 fold 사건의 라벨 근거에도 나오는 표현 갈래로 설명됨(5-fold).
- 실험 DB 적용(백업 `data/_backup/migyeol_before_recall_20261029.db`, 실 API 6회): 후속보도 있음 63 → **71**, P/R/F1 0.905/0.750/0.820 → **0.930/0.868/0.898**(TP 57 → 66, FP 6 → 5, FN 19 → 10), 상태 29/23/7/33/8 → 42/18/8/30/2, 군집 전체 라벨 0.911/0.905 그대로, 회귀 사례(만덕 2026 19·새로운 문제, 영도구 3, 현대시장 7) 그대로. `experiment_latest.json`·`dashboard_20261022.json`·`reports/experiment_20261029_0142.md` 재생성.
- 테스트 `tests/test_followup_v4.py`(29건: 표현 목표·음성 예·앞날 재개·규칙 이름·약속 없는 경로·확률 문턱·조사 결과 표지와 원천 직후 내림). 전체 558건 통과.
- 남은 놓침 10건: 근거가 검토 대기·확률 바닥 아래 4, 같은 장소 재발 2, 관련 문장 판정 2, 지시형 원천 발언 1, '지원' 만 있는 제목 1.

## 2026-10-29 사건 페이지 「민간 지원 소식」 — 타임라인의 세 번째 주체 (기업·단체·개인)

결정: 별도 '기업 칸'이나 광고 성격의 칸은 두지 않는다(사고 당사자 기업이 홍보하는 자리로 보이면 신뢰를 잃는다). 사건 타임라인 안에 **사건 발생 → 기관 발표 → 민간 지원 소식 → 후속보도 확인** 순서로 사실만 보인다. 상태 5종·판정은 건드리지 않는 보조 근거. 평가 단어는 쓰지 않는다. 규칙·수치는 `docs/SUPPORT.md`.

- **추출 규칙** `backend/app/support/extractor.py`, 어휘·주체 상수 `backend/app/rules/support_terms.py`(테스트로 고정): 활성 연결 기사(발생일 이후)의 제목·hilight·content_200 을 문장으로 나눠 지원 어휘(성금·기부·구호물품·자원봉사·위로금·기탁 …)가 있는 문장에서 기업·단체 이름을 토큰 단위로 뽑는다. 공공 주체(`rules/actors`)·공직자 모임·정당·모금 창구·수혜자·사건 설명 속 이름('금호타이어 화재') 제외, 주어 판정(조사·쉼표·'임직원들이' 연쇄), 같은 기사 안 부분 이름 중복 제거, 개인은 '개인'으로만. 사건 설명(제목·요약·시설·책임기관)에 이름이 있으면 `actor_role=당사자`.
- **저장·실행**: 새 테이블 `support_mentions`(`backend/app/support/models.py`, (event_id, news_id, actor) 유일, `init_db` 에 등록), `scripts/run_support.py --all|--event N [--dump]`(멱등, 외부 요청 없음). 실험 DB 100건 → 16개 사건 154행(기사 138건), 무작위 30행 정밀도 29/30. 1차 규칙(23개 사건 339행)의 잡음(부분 이름 중복·당사자 이름의 사건 설명·공동모금회·정당·'경기부지사'의 '기부'·캡션 토큰·무관한 연말 봉사 기사)을 보고 조였다.
- **API** `GET /api/events/{id}/support`(공개): `{ event_id, count, actors, items[], note, disclaimer }`, `docs/API_CONTRACT.md` v2 절.
- **화면** `frontend/src/components/support/SupportPanel.tsx` — 근거 화면 `/events/[id]/evidence` 의 「기관 발표 확인」 패널 다음(앵커 `#support-heading`). 행 = 날짜·주체(종류 칩·「사건 당사자」 칩)·지원 어휘 칩·근거 문구·기사 제목 링크(`provider_link_page` 있을 때만). 8건 뒤는 접기. 없으면 "연결 기사에서 확인된 민간 지원 언급이 없습니다".
- **테스트** `tests/test_support_extractor.py`(가상 문장: 어휘 경계·주체·공공 제외·수혜 제외·사건 설명 속 이름·개인 익명화·당사자·중복 제거), `tests/test_support_api.py`(가상 기사 행으로 저장·멱등·응답 형태·404). 문서 `docs/SUPPORT.md` 신규, `README.md`·`docs/QA.md`(기업 칸이 없는 이유·추출 방법과 정확도)·`docs/DEMO_SCRIPT.md` §4 갱신.

## 예선 단계 (2026-09, API 키 없음) — Phase 0

빅카인즈 API 직접 활용이 제한되므로, 지침서 요청·응답 구조를 그대로 따르는 **가상 사건 픽스처**로 데이터 흐름과 화면을 설계·검증했다.

- 저장소 뼈대, `.env.example`, 문서(ARCHITECTURE / API_CONTRACT / CHANGELOG)
- 빅카인즈 클라이언트 3모드 (`mock` / `record` / `live`) — Live 호출 코드는 완성, 키만 없음
- 지침서 스키마 그대로의 요청·응답 모델, 코드표(언론사·통합분류·사건사고분류)
- 가상 사건 8건 픽스처 생성기 (`scripts/make_fixtures.py`) — 5개 상태가 모두 나오도록 설계
- 6단계 파이프라인이 Mock+Stub 으로 끝까지 동작 (discover → collect → link → extract → judge → tracker)
- 규칙 엔진 (공공 주체 판별·정규화, 상태 판정 R1~R5, 우선순위) + 단위 테스트
- LLM 프롬프트 2종(`promise_v1`, `followup_v1`)과 구조화 출력 스키마, `stub`/`live` 어댑터
- FastAPI 라우트 전부 (목록·상세·근거·트렌드·신고·검토 큐·연결 확정/해제·파이프라인 실행·메타)
- Next.js 3화면(목록·타임라인·근거) + 관리자 화면, Mock 데이터로 완성
- 정답셋 대비 평가 스크립트 (`scripts/evaluate.py`, 임계값 스윕)

## 2026-10-12 (저녁) 수정 검증 반영 — `코드리뷰_2026-10-12_수정검증.md`

검증 리뷰의 0장(즉시)·1장(본선 1주차)·2장(본선 기간) 항목을 코드로 재확인한 뒤 반영했다. 회귀 테스트 `tests/test_review_20261012.py`(40건) 추가, 전체 113건 통과. mock 평가: 상태 정확도 1.0, 연결 P/R 1.0/0.955(변동 없음).

- **0-1 키 노출**: `app/redact.py` — 등록된 접근 키와 UUID 형태 문자열을 `BigKindsError`·`pipeline_runs.error`·`failures`·로그에서 마스킹. `/api/meta.error_summary` 와 `/api/pipeline/run` 500 detail 은 분류 문구만.
- **0-2 관리자 토큰**: 프런트 `/api/admin/*`·`/api/pipeline/*` 는 Route Handler 프록시(`src/lib/proxy.ts`)가 서버 전용 `ADMIN_TOKEN` 을 `X-Admin-Token` 으로 붙인다(브라우저에 전달 안 함). 서버 컴포넌트도 같은 헤더. 공개 API 는 `fallback` rewrite. 401 안내 문구 추가. 백엔드 비교는 `hmac.compare_digest`.
- **0-3 마이그레이션**: 전체 사전 검사 후 실행(부분 적용 없음), JSON `default=list/dict` 는 `DEFAULT '[]'/'{}'`.
- **1-A~1-D 수집**: `time_line` 로 건수를 먼저 보고 상한 안이면 한 번에, 넘치면 날짜(초기)·월(이후) 단위 물 채우기 배분. 총상한이 최근 달을 굶기지 않고 `total_hits` 는 전 구간 합. 전재 기사는 상한 미소모. 조용한 사건은 사건당 4회 호출. **1-E** 정렬은 지침서 객체 형식만 쓰고 리스트 형식은 `smoke_live.py` 에서만 시험.
- **1-F 공공 주체**: 토큰 단위 판별로 재작성 — '시민안전과장'·'한국도로공사 사장'·'여성가족부 관계자'·'LH·한전·코레일 관계자' 통과, '김민구'·'더불어민주당 대변인'·'식당 사장' 탈락. 개인명은 기관으로 정규화하지 않는다. 시의회는 공공으로 유지(리뷰와 다른 판단 — 공공기관).
- **1-G/1-H 판정**: 분류를 못 받은 후속 기사는 `Event.pending_followups` 에 남기고 검토 큐 + "잠정 판정" 사유. 최신 기사부터 분류. 규칙 판정은 현재 약속 집합 해시의 신호만 사용(취소된 약속의 옛 신호 무시). 추적 약속이 없으면 LLM 을 부르지 않는다.
- **1-I 연결 삭제**: 추적 약속 = 근거 기사가 활성 연결인 약속. 대표 약속·우선순위·후속 재검색에 반영. `link` 단계만 돌려도 규칙 재판정.
- **1-J/1-K 연결·키워드**: 책임기관 == 지역이면 기관 일치로 치지 않음. 어미(마치고·하며·도록·…히), 복수 '들', 명사 꼬리(…차도·…수로) 보호, 일반어(안전대책·지원금·신속·…) 추가.
- **1-L 분류 코드**: `"2,9,34"` 를 쪼개지 않고 코드로 보존 → 명칭 매핑.
- **2장**: 우선순위 경과일 계산 공통화(`days_since_followup`), 스윕은 sqlite backup API, 파일 Lock(`data/pipeline.lock`), 검토 대기 연결은 사건 간 경쟁에서 제외·단일 사건 실행도 전체 사건과 비교, 시드 변경 시 옛 시드 후보 삭제, R5 새로운문제 사유는 최신 신호, 완료 강등은 과거형 완료 표현이 있으면 유지, 기한 파싱(`2026.06.30`·`20260630`·`2026년 6월 30일`, 비정상 연도 무효·예외 없음), 재발 급증은 급감 아님, 병합 대표 변경 시 근거·병합 포인터 이월, 관대 파서(TimeLine·WordCloud·Issue·ChangeTracker null), tracker 실패 격리, Retry-After 상한 60초, 관리자 해제도 평가 fp 로(`auto_decision`), `event_id` 범위 검증, PipelineRun 시각 KST.
- **프런트**: `judged`·`pending_followups`·`deadline_precision`·`warnings`·`scheduler_enabled`·`merged_news_ids` 타입, `status_confidence`/`confidence` nullable, 판정 전 사건은 "판정 전" 표시, 잠정 배지, 완료 신호 기준선 0.7, 신고 처리 뒤 새로고침, `parseId` 상한, rewrite 재빌드 안내, mock 데이터 동기화.
- **문서**: README(관리자 화면 보호·동시 실행), ARCHITECTURE 메모, API_CONTRACT(error_summary 분류, link_method 보존, 잠정 판정), DEMO_SCRIPT 수치·표현 수정, `.gitignore` 에 `events.draft.yaml`.
- 연결 recall 0.977→0.955 는 2026-10-12 오전의 pc-9(검토 큐 강화) 반영에 따른 의도된 변화다: '달빛지하차도 침수 1년…' 기사가 자동 연결에서 검토 큐로 옮겨졌다. 2026-09-11 결정("강한 규칙 일치는 자동 연결")과는 충돌하지 않는다 — 그 기사는 시설명이 제목에 있지만 지역·기관·사건분류가 없어 강한 규칙이 아니다.

## 2026-10-12 (밤) 재검증 반영 — `코드리뷰_2026-10-12_재검증.md` (커밋 5a6af66 대상)

재검증 1장 8건·2장 low 를 반영했다. 회귀 테스트 `tests/test_reverify_20261012.py`(34건) 추가, 전체 147건 통과. mock 평가 변동 없음(상태 1.0, 연결 P/R 1.0/0.955).

- **1-1 큰 달의 월말 기사**: 상한을 넘는 달은 상·중·하순으로 나눠 받고, 전재만 많아 못 쓴 예산은 잘린 (부분)구간에 이어 받는다(offset 이어받기). 특정 완료 기사가 시드 질의 표본에 반드시 들지는 않는다 — 그 기사는 약속 키워드 재검색(범위가 좁아 예산 안)이 잡는 경로이며 테스트가 둘을 함께 검증한다.
- **1-2 link 단계 재판정 범위**: judge 가 한 번도 돌지 않은 사건은 재판정하지 않는다(관리자 확정·해제도 같다). 배타 처리로 연결이 강등된 다른 사건은 함께 재판정한다.
- **1-3 이름 + 직함**: 두 음절 어간 + 한 글자 접미(시·군·구·도·부·국·과·실) 토큰 뒤에 직함이 오면 사람 이름일 수 있어 기관으로 쓰지 않는다('김민구 과장' → 기관 없음 → LLM 기관명). 뒤가 '관계자'·'대변인'·'당국' 이면 기관('청주시 관계자'). 직함만 있는 토큰은 공공이지만 기관명으로 정규화하지 않는다('본부장' → 없음). '…대표/사장' 은 기관 토큰이 **있을 때만** 공공. 노총·노조·연구소·협회, '주민대책위원장'·'감리단장' 은 민간.
- **1-4 추출 이월·영구 실패**: 추출을 못 받은 공공 인용문 수 `Event.pending_quotations` → 잠정 판정·검토 큐. 항목별 실패 횟수 `Event.llm_failures`; 2회째 실패는 후속 신호 `불명확`/약속 `해당없음` 으로 기록하고 더 부르지 않으며 검토 큐에 남는다(명세 §8).
- **1-5 병합 약속**: 대표 근거 또는 병합된 근거 중 하나라도 활성 연결이면 추적. 대표 선정은 활성 근거 우선.
- **1-6 별칭이 지역을 품을 때**: '가온시청'·'가온시장' 은 기관 일치로 치지 않는다.
- **1-7 time_line 장애**: 0건이면 search 1회로 교차 확인, 실패하면 단일 구간 수집 + 경고(stats.warnings). 수집 0건 실행은 기존 후보를 지우지 않는다.
- **1-8 CSRF**: 프록시는 상태 변경 요청에 `Sec-Fetch-Site`/`Origin` 을 검사하고, `.`/`..`(인코딩 포함) 경로 조각을 거부한다.
- **2장**: tracker 단독 실행 실패는 실행 실패, 시드를 좁히면 후보에서 빠진 자동 연결 삭제, 부분 완료('1단계·일부')와 과거+미래 혼합은 진행(예정보다·계획대로는 제외), 기한 '2026. 6. 30.', action_terms(단위·복수·관로·'해' 어미), 근거 화면 새로운문제 기준선 0.7·판정 전 헤더, 실행 중/중단 표시(`finished_at` null, 기동 시 중단 실행 닫기), 관리자 표 `deadline_precision`, 마이그레이션 단일 트랜잭션(BEGIN IMMEDIATE), evaluate `auto_decision` 미기록 DB 대체, Lock 경로 backend/data 고정, redact 경계 제거, API_CONTRACT(422/500)·DEMO_SCRIPT 수정.
- **실 API 첫 호출(키 활성 확인)**: 7개 엔드포인트 모두 result=0. 반환 키와 차이는 ARCHITECTURE "지침서와 실제 응답 차이" 에 기록. 코드 수정 1건: `provider_link_page` 가 `//www.…` 형태라 https 를 붙인다.
- 미반영(기록): mock.ts 의 데이터 수치가 실제 백엔드와 다른 점(타입만 일치), 대형 사건 30건 가정 시 호출량(스케줄러 켜기 전 실 시드로 측정 권고).

## 2026-10-13 3차 검증 반영 — `코드리뷰_2026-10-12_3차검증.md` (커밋 79d588d 대상)

3차 검증 1장 8건·2장 low 를 반영했다. 문서의 문장·사례를 그대로 고정한 회귀 테스트 `tests/test_verify3_20261012.py`(78건) 추가, 전체 225건 통과. mock 평가 변동 없음(상태 1.0, 연결 P/R 1.0/0.955), mock all 호출 32회.

- **1-1 완료 강등 회귀**: 미래 판단을 문장 끝 서술어 기준으로 바꿨다. '계획했던 공사를 모두 끝냈다'·'예정보다 앞당겨 준공했다'·'예정대로 완공했다' 등 13문장은 완료, '…완료 예정이라고 밝혔다'·'앞두고 있다'·'1단계 … 2단계 예정' 등 7문장은 진행으로 고정.
- **1-2 공공 주체**: 행정구역 사전 + 성씨 기반 이름 판정(`rules/regions.py`). '이영도 대변인'·'김민구 관계자'·'박정국 국장' 은 기관으로 정규화되지 않고, '청주시'·'보령시'·'강남구'·'중구'·'도로국' 은 기관. 규칙 결과가 LLM 변형('충청북도')으로 덮이지 않고, LLM 의 사람 이름은 버려 "기관 미상"(병합 제외).
- **1-3 action_terms**: 조사를 떼면 1음절이 되는 토큰('2배로'·'34곳을'·'안에'·'차를')은 버림, '-해' 어미는 3자 이상만('피해'·'손해' 유지), 대피로·순찰로 보호. mock promise_terms 에서 '배로' 제거.
- **1-4 수집**: 이후 구간을 조치 상태 질의 + 분기 표본으로 교체(기본 트렌드가 예산 안이면 한 번에). 완료 기사(6/20)·다른 표현('침수 대책 마무리')을 질의를 거르는 가짜 클라이언트로 검증. 3년 중형 사건 호출 ≤25회(이전 ≈130회).
- **1-5** tracker 단독 실행·단일 사건 relink 의 재판정 범위. **1-6** 활성 연결 기준 집계, 관리자 `llm-retry`/`llm-ack`, 카드 `pending_quotations`. **1-7** 기동 시 Lock 확인, 성공 시 error 비움. **1-8** 약속 재검색 후보는 지역·기관 + 약속 키워드 2개면 강한 규칙.
- **2장**: 순별 올림 배분, 이월의 월 상한, 트렌드 실패 대체 수집 3분할, 정상 0건 구간 교차 확인 생략, override 0건 경고(meta 노출), `:memory:` Lock 경로, WAL 전환 실패 무시, redact 변형, 기한 '2026 6 30', 프록시 `X-Forwarded-Host`, API_CONTRACT 분류 목록·신규 엔드포인트.
- 미반영(기록): mock.ts 데이터 수치, 실 시드 호출량 측정(스케줄러 전 권고).

## 2026-10-22 ~ 10-23 본선 v2 — 멘토 피드백 반영 (개발 프롬프트 v2)

멘토 피드백(10-22): 합성 데이터 대신 **실제 API 로 원천 기사 → 동일 사건 군집 → 후속보도 탐지 → 정량 지표**, 첫 화면은 시민용 **대시보드**(일별/재난별/기관별, 후속보도 잘한 기관 상단), 후속보도가 없으면 **공공기관 게시판**까지 확인해 **보도자료 초안**까지. 결정 표는 `개발_프롬프트_미결.md` §0. LLM 키 없이 진행(`LLM_MODE=rules`). 세 트랙을 같은 폴더에서 병렬 개발한 뒤 통합했다.

### 통합 (공용 파일)
- `config.py`: `llm_mode` 에 `rules`, 실험(`EXPERIMENT_*`)·게시판 확인(`NOTICE_*`) 설정. `main.py`: `/api/dashboard`·`/api/press`·`/api/notices` 라우터. `db/session.py`: `notices.models` 등록. `llm/client.build_llm` rules 분기. `rules/orgs.canonical_org` 공용 기관 정규화.
- `api/events.py`: `org`(정규화 기관 키)·`followup`(완료·진행 여부) 필터, `filters.orgs`. `pipeline/scheduler.py`: 05:30 KST 게시판 확인 잡. `tests/test_integration_v2.py` 3건.
- 프런트: 목록 `/` → `/events`, 첫 화면 대시보드, 내비게이션(대시보드·사건 목록·보도자료 초안·소개·관리자, 모바일 가로 스크롤), `lib/filters.ts`·`types.ts`·`api.ts` 에 `org`·`followup`.
- 문서: `API_CONTRACT.md`(v2 엔드포인트), `ARCHITECTURE.md`(v2 구조·실험에서 본 실응답 차이·질의 400건 규칙), `README.md`(v2 실행), `DEMO_SCRIPT.md`(v2 동선), `EXPERIMENT.md`, `NOTICES.md`.
- 실험 DB(`backend/data/migyeol.db`, 사건 150건)에 게시판 확인(`run_notices.py --all`)과 집계 내보내기(`export_dashboard.py`)를 실행하고 `experiment.py --stage report` 로 리포트를 갱신했다.
- `test_repo_rules.py` discover 픽스처는 임시 Lock 을 쓴다(실험이 `backend/data/pipeline.lock` 을 쥐고 있어도 테스트가 돈다). 실험 상한(질의 400건·수집 600건)은 `EXPERIMENT_QUERY_HIT_LIMIT`·`EXPERIMENT_MAX_ARTICLES_PER_EVENT` 설정으로 옮겼다.
- 남은 일: `gold/experiment_labels.csv` 사람 라벨(2인 분담) → `--stage report` 재실행으로 precision/recall·군집 정밀도 채우기, 자동 시드 150건 중 기사 제목이 사건명으로 들어간 것 다듬기, 시연용 사건 확정.

### 2026-10-23 밤 — 시드 정리·재실행·에이전트 라벨 (워크플로우 2개, 에이전트 24개)
- **시드 정리**(워크플로우 1, 6+1 에이전트): 자동 시드 150건 → `seeds/events.yaml` 90건. 사건명(기사 제목 → 지역·시설·유형), 실제 발생일, 기관 단위 책임기관, 고유어, 좁힌 질의. 제외 60건(예방·정책 28, 중복 16, 집계형·비사건 10, 범죄·형사·정치 5, 해외 1)과 사유는 `docs/EXPERIMENT.md` §4.
- **재실행**: `--seeds seeds/events.yaml --today 2026-10-22`(실 API ≈ 3,100회 추가, 2h17m). 사건 90, 연결 기사 중앙값 118, 자동 후속보도 확인 62(69%). 게시판 확인 재실행: 트리거 19, 발표 확인 4.
- **라벨**(워크플로우 2, 16 에이전트): `gold/experiment_labels.csv` 90행(후속보도 O/X·근거·군집·기관) — 후속보도 O 49건은 반박 검증자가 재검토해 13건 조정; `cluster_sample.csv` 434행; `org_sample.csv` 300행. 결과: 라벨 후속보도 42/90(47%), 자동 판정 precision 0.53 / recall 0.79, 군집 정밀도 0.58, 기관 정규화 0.82. **사람 라벨이 아님**을 CSV 메모·문서·시연 대본에 명시.
- 공용: `tests/test_repo_rules.py` discover 픽스처가 빈 시드 파일을 쓰도록(실사건 시드가 채워진 뒤에도 통과). README 결과 표, DEMO_SCRIPT 숫자·시연 사건(영도구 스쿨존 완료 / 군위 제방 기한 경과 + 기관 발표 / 통복터널·5호선 기관 발표), EXPERIMENT §4·§5.

### 트랙 A — 데이터·실험
2026-10-22 ~ . 개발 프롬프트 v2 §4·§5·§8·§11-A. 커밋 전 (통합 단계에서 한 번에).

#### 추가

- `backend/app/llm/rules_adapter.py` — `RulesLLM` (`LLM_MODE=rules`, model `rules-v1`). LLMClient 프로토콜 그대로: 약속 추출(행동 동사·상대 기한·강도·고정 신뢰도), 후속 신호(약속 키워드 겹침 문장의 완료·진행·새로운문제, `completion_adjustment` 강등 유지). 외부 호출이 없어 LLMFailure 를 내지 않는다.
- `backend/app/experiment/` — 실험 파이프라인.
  - `sources.py` 원천 기사 수집: 인용문 검색(월별 × 계열, `_score` 상위 표본, 공공 주체 + 확약·계획 발언 + 제목 재난 어휘·국내) + 이슈 랭킹 날짜 순회(재난 어휘 토픽만 상세 조회, 3계열 비율 ≥ 0.3) + 인용문 경로에 없던 이슈 사건의 보강 인용문 검색. 스레드 풀(기본 4, 시작 간격은 `BIGKINDS_RPS`).
  - `autoseed.py` 유니크화(지역·시설·±14일·기관, 전재) → 점수(사고 뒤 대응 > 예방·계절 대비) → (계열, 연도) 층별 선택 → `seeds/experiment.draft.yaml`.
    시드 질의는 (고유 시설명 / 재난어 AND 기초 지역)으로 좁히고, 후보 1.5배를 `time_line` 으로 전체 기간 건수를 본 뒤 상한(400건) 안으로 좁혀진 사건만 고른다
    (좁힐 때는 그룹 제목 2건 이상에 함께 나온 말·이슈 토픽 키워드만 쓴다). 넓은 질의는 collect 가 월·순별 상태 질의로 사건당 100회 넘게 부르기 때문.
  - `followups.py` 약속 키워드 재검색의 호출 예산판(사건당 한 질의, `time_line` 건수 ≤ 400 일 때만). 파이프라인판은 실데이터에서 약속당 100회 넘게 불러 실험에서는 쓰지 않는다.
  - `text.py` 지역·시설·재난 어휘·사망/부상자 정규식, 실기사 문장의 단정 표현 가림(`scrub`).
  - `metrics.py` §4.6 지표(키 고정), `labels.py` 라벨 CSV 3종(사람 열 보존), `report.py` 마크다운 리포트, `runner.py` 단계 실행·호출 수·비용 누적(`data/experiment/state.json`).
    followup 단계는 규칙 어댑터 산출물(약속·신호)을 지우고 다시 만든다(결정적·무료). 실험 실행에만 수집 총상한 600·초기 구간 300·LLM 호출 상한 해제(설정 사본).
- `backend/scripts/experiment.py --stage discover|seed|collect|link|followup|report|all [--dry-run] [--seeds PATH] [--today]`. 호출 계획이 예산(5,000)의 2배를 넘으면 멈춘다.
- `backend/scripts/labels_to_gold.py` — 라벨 CSV → 정답셋 YAML(evaluate.py 형식).
- DB(§8): `Event.source_news_id·source_quotation·auto_seed·dead·injured·org_notice_found`, 테이블 `experiment_runs`. 모두 nullable/기본값 → 기존 DB 는 기동 시 간이 마이그레이션.
- 테스트: `tests/test_rules_adapter.py`(stub 정답 대비 일치율 출력 + 회귀 하한 + 대표 문장), `tests/test_experiment.py`(mock 전 단계, §4.6 스키마 고정, 라벨 보존, 유니크화·선택·질의·기관 정규화·텍스트 규칙).

#### 변경

- `backend/app/rules/orgs.py` — `canonical_org()` 사전 보강(시그니처 동일): 광역·기초 지자체(시청·시장·군수·구청장·지사 꼬리), 중앙부처·청·위원회 약칭, 공사·공단(LH·코레일·한전·수공·LX …), 지역 해경·소방본부, 사람 이름이 붙은 형태. `org_aliases()`·`is_department_only()` 추가.
- `backend/app/pipeline/seeds.py` — 시드 YAML 의 `source_news_id`·`source_quotation`·`auto_seed` 를 Event 로 옮김(최소 수정, 없으면 빈 값).
- `backend/app/bigkinds/live.py` — throttle 에 Lock(여러 스레드가 불러도 시작 간격 유지). 단일 스레드 동작은 같다.

#### 실행 결과 (2026-10-22)

원천 기사 1,559건 → 유니크 사건 그룹 1,017 → 추적 150건 → 후속보도 확인 87건(58.0%), 창별 30/90/180/365일 = 35/51/57/73. 요청 5,406회(녹화 6,678 파일).
자세한 표는 `reports/experiment_20261022_2054.md`, 방법·한계는 `docs/EXPERIMENT.md`. 규칙 판정이라 정밀도는 사람 라벨로 확인해야 한다(눈대중 약 절반).

#### 규칙 어댑터 보정 (실데이터 확인 후)

- 지시·당위·가정 발언('…해야 한다', '…했어야', '…하라', '…해 달라')은 의사표명 — 추적 약속에서 빠진다.
- 신호 겹침 판정에서 재난어(화재·산불 …)·일반어(일단·향후·영업·중단·출입·합동 …)·용언꼴을 뺀다.
- 새로운문제 표현에서 '지연제'·'차질 없이'·'지연 없이' 오탐 제거. 일치율(stub 대비) 신호 89% → 94%.

#### 실응답이 지침서와 다른 점 (통합 때 ARCHITECTURE.md 로)

- `search/quotation` 은 `category_incident` 필터를 걸어도 (월, 계열)당 1만 건 이상(2023-07 산업사고 11,666 · 자연재해 13,265 · 교통 1,605) — 본문까지 매칭하고, 인용문의 분류는 기사 분류를 그대로 물려받아 해외·정치 발언도 섞인다. `return_from` 상한(1000) 때문에 전수 대신 `sort: {"_score": "desc"}` 상위 표본을 받는다(정렬 인자는 받아들여진다).
- `_score` 정렬 인용문 검색은 호출당 5~15초(가끔 30초 타임아웃 → 재시도). 동시 요청 4개로 나눠 받는다.
- 인용문 문서에는 `title` 이 없다(fields 에 넣어도 None) — 원천 기사 제목은 `news_ids` 상세 조회로 받는다.
- 이슈 랭킹 클러스터 100건 `news_ids` 상세 조회는 30초 타임아웃이 잦다 — 앞 20건만 조회해 3계열 비율을 본다.
- `search/news` 의 넓은 질의(재난어 OR + 지역 OR 중앙부처 별칭)는 3년 치 후속 구간에서 사건당 70~130회 호출로 번진다(collect 의 월·순별 상태 질의 분할). 사건 질의는 건수 400건 안으로 좁혀야 사건당 4~5회.
- 인용문 `category_incident` 는 3단계 명칭('사고>산업사고>화재')으로 온다(코드 아님). 필터는 '사고>산업사고' 명칭과 '2,9' 코드 둘 다 같은 결과.

### 트랙 B — 대시보드·집계 API
#### 2026-10-22 — v2 화면 0·1·about, §7.1 집계 API

##### 백엔드
- `backend/app/api/dashboard.py` — `/api/dashboard/*` 6개 엔드포인트.
  | 경로 | 응답 요약 |
  |---|---|
  | `GET /summary` | `events_total, with_followup, followup_rate, overdue, with_org_notice, followup_articles, orgs, by_status{5}, last_run, as_of, bigkinds_mode, experiment(요약|null), disclaimer` |
  | `GET /timeseries?interval=month\|day&from=&until=` | `[{bucket, occurred, followup_articles, confirmed_followups, dead, injured}]` — 빈 구간 0 채움. 기본 범위 = 가장 이른 발생일 ~ 기준일(일별은 최대 1500일). `from > until`·일별 1500일 초과는 422 |
  | `GET /by-incident` | `[{incident_type, label, events, with_followup, rate, subtypes}]` — 계열(앞 두 단계, 예 `재해>자연재해`) 단위 |
  | `GET /by-org?sort=followup\|rate\|events` | `[{org, events, with_followup, rate, latest_followup_at, last_article_at, confirmed_signals, event_ids, raw_names}]` — 기본 followup desc(동률 rate) |
  | `GET /recent-followups?limit=10` (1~50) | `[{event_id, event_title, event_status, org, signal, confidence, evidence_span, news_id, title, provider, published_at, link}]` 최신순 |
  | `GET /experiment` | `experiment_latest.json` 그대로. 없으면 404, 깨졌으면 503 |
- 집계 규칙(목록 숫자와 일치하도록 `judge`·`serializers` 함수 재사용)
  - 사건 = `Event.active=true`. 후속 기사 = `judge.followup_articles`(활성 연결만, 카드 `followup_count` 와 같음).
  - 유효 신호 = 현재 prompt_version · 현재 추적 약속 집합 해시 · `rules.status.is_valid`(일치 + 신뢰도 ≥ SIGNAL_MIN_CONF + 완료·진행·새로운문제) — `rejudge_event` 가 쓰는 집합 그대로.
  - **후속보도 확인 사건 = 유효 신호 ≥ 1** (§4.4). R1~R3 상태 사건은 항상 포함되고, R4·R5 사건도 유효 신호가 있으면 포함된다(기준 미달 새로운문제 등).
  - 기한 경과 = 대표 약속 기한이 지났고 상태 ≠ `조치 완료 근거 확인` (needs_check 기준).
  - 기관 키 = `canonical_org(event.responsible_org)`, 비면 `기관 미상`. `raw_names` 에 정규화 전 이름.
  - 피해 규모 `dead/injured`: 실험 JSON 의 월별 `timeseries` 값이 있으면 그것, 없으면 `Event.dead/injured`(트랙 A 컬럼, 아직 없으면 null) 합계. 추정하지 않는다.
  - `with_org_notice` = `Event.org_notice_found`(트랙 A 컬럼, 없으면 0).
  - `summary.experiment` = 실험 JSON 에서 `by_incident_type·by_org·timeseries·daily` 를 뺀 나머지(키 이름 §4.6 그대로).
- `backend/scripts/export_dashboard.py` → `reports/dashboard_<기준일 YYYYMMDD>.json` (`summary, daily, monthly, by_incident, by_org, recent_followups(50), disclaimer`). `--out-dir` 지정 가능. API 와 같은 함수(`build_dashboard`)를 쓴다.
- `backend/tests/test_dashboard_api.py` 17건 — 모듈 전용 mock DB, 실험 경로는 항상 임시 경로(저장소 `reports/` 와 무관). 6개 엔드포인트, 실험 JSON 없음/있음(§4.6 예시)/깨짐, 목록 API 와 숫자 일치, 기관 별칭 병합(서울특별시+서울시청→서울시), 해제된 연결의 신호 제외, 금지어, 내보내기 파일.

##### 프런트
- `frontend/src/app/page.tsx` — 대시보드: 머리 배너(후속보도 확인 비율 링, 실험 리포트가 있으면 "기관 대응 발언 기사 N건 → 사건 M건 → 후속보도 확인 K건"), KPI 타일(추적 사건·후속보도 확인·기한 경과·기관 발표 확인·[보도된 인명 피해: 값이 있을 때만]·마지막 갱신), 시계열(월별/일별 × 전체/2023~2025/최근 12개월, 마우스오버 툴팁, 표로 보기), 재난 유형별 막대, 사건 상태 분포, 기관별 표(확인된 기관 위·없는 기관 아래, `?org_sort=` 정렬, 기관명 → `/events?org=`), 최근 후속보도 피드, 확인 필요 상위 5. 모든 패널에 면책 문구.
- `frontend/src/components/dashboard/*` — `Hero, KpiTiles, Panel, TimeseriesChart(client, SVG), IncidentBars, StatusBreakdown, OrgTable, RecentFeed, NeedsCheckTop`. 차트 라이브러리는 추가하지 않았다(SVG 직접, `package.json` 변경 없음).
- `frontend/src/lib/dashboard.ts` — 타입 + fetch (`API_BASE`·`ApiError` 재사용, `api.ts` 수정 없음). `getEventsWithOrg` 는 `org` 를 백엔드로 넘긴다(목 모드에서는 기존 `getEvents`).
- `frontend/src/app/events/page.tsx` — 탭 `확인 필요`(기본) / `후속보도 확인됨`(`?tab=followup`, 완료·진행 두 상태를 각각 조회해 상태별 묶음으로 표시), `?org=` 칩. 백엔드가 org 를 아직 거르지 않으면 "기관별 보기는 준비 중" 안내만 띄우고 프런트에서 거르지 않는다.
- `frontend/src/app/about/page.tsx` — 서비스 설명, 다섯 단계, 다섯 상태, 데이터 출처, 다음 단계(정치인 공약·기업 공개 약속·기관별 모아보기·순위), 팀.
- 캡처: `docs/screenshots/dashboard_desktop_{1,2,3}.png`, `dashboard_mobile_{1,2,3}.png`(375px), `dashboard_experiment_desktop.png`(§4.6 예시 JSON 주입), `dashboard_events_followup_tab.png`, `dashboard_about.png`.

##### 통합 요청 (공용 파일 — 직접 고치지 않음)
1. `backend/app/api/events.py` — `GET /api/events` 에 `org` 쿼리: `canonical_org(ev.responsible_org) == org` (빈 값이면 `기관 미상`). 대시보드 기관 링크가 이걸 쓴다. 프런트는 이미 `org` 를 보낸다.
2. `backend/app/api/events.py` — `status` 다중값(`status=A&status=B`) 또는 `followup=true` 필터. 지금 `후속보도 확인됨` 탭은 두 상태를 따로 받아(각 최대 100건) 묶음으로 보여 준다 — 100건 초과 시 상태별 전체 보기 링크로 넘긴다.
3. `frontend/src/lib/filters.ts`(공용 성격) — `FilterState` 에 `org` 추가. 지금 `EventFilters` 에서 필터를 바꾸면 `org` 가 URL 에서 빠진다(페이지 이동 링크는 보존함).
4. `frontend/src/lib/types.ts` — `lib/dashboard.ts` 의 대시보드 타입을 옮길지 결정(지금은 dashboard.ts 에만 있음). `EventListParams` 에 `org?: string`.
5. `frontend/src/components/SiteHeader.tsx` — 375px 에서 내비게이션 글자가 두 줄로 꺾인다(`대시보\n드`). `whitespace-nowrap` + `overflow-x-auto` 권장.
6. `docs/API_CONTRACT.md` — 위 표의 `/api/dashboard/*` 6개와 집계 규칙을 옮긴다. 응답에 계약 외 추가 키(`followup_articles, orgs, bigkinds_mode, confirmed_followups, label, subtypes, last_article_at, confirmed_signals, raw_names, event_status, org, confidence`)가 있다.
7. `backend/app/db/models.py`(트랙 A) — `Event.dead/injured/org_notice_found` 가 생기면 대시보드가 `getattr` 로 바로 읽는다. 코드 변경 필요 없음.
8. 실데이터 전환 시 `scripts/export_dashboard.py` 를 트랙 A DB(`DATABASE_URL` 기본)로 한 번 돌려 `reports/dashboard_<date>.json` 을 만든다(이번엔 mock 데이터를 `reports/` 에 남기지 않으려고 스크래치에만 출력해 확인).

##### 알려진 한계
- 프런트 목 모드(`NEXT_PUBLIC_USE_MOCK=1`)에는 대시보드 집계가 없어 오류 상자가 뜬다(`/events` 는 동작).
- 가상 픽스처는 4개 기관이 모두 후속보도가 있어 "아직 확인되지 않은 기관" 묶음은 화면에 안 나온다(테스트로만 확인).

### 트랙 C — 보도자료 초안·공공기관 게시판 확인
2026-10-22 · v2 §3, §6 화면 4·근거 패널, §7.2·7.3, §8-C, §11-C

#### 만든 것

##### 백엔드
| 파일 | 내용 |
|---|---|
| `app/notices/models.py` | `org_notices`(사건·기관·게시판·제목·게시일·URL·본문 300자·매칭 점수·매칭 용어), `notice_checks`(트리거 여부·사유·결과·게시판별 쪽수/항목/오류·요청 수) |
| `app/notices/guard.py` | 요청 URL 검사: http(s)만 → **뉴스 도메인 거부**(언론사·방송·지역지·포털 뉴스·빅카인즈 웹, 하위 도메인 포함, `news.*` 비공공 호스트) → 허용 접미(`NOTICE_ALLOWED_DOMAINS`)·등록부 도메인 |
| `app/notices/robots.py` | robots.txt RFC 9309 구현(그룹 선택, `*`·`$`, 최장 일치·Allow 우선, 4xx 제한 없음 / 5xx·연결 실패 접근 안 함) |
| `app/notices/fetcher.py` | `HttpFetcher`(guard → robots → 호스트별 2초 간격 → UA, 리다이렉트 단계별 재검사, HTML 아닌 응답·3MB 초과 거부, 요청 수 집계), 테스트용 `FixtureFetcher` |
| `app/notices/parser.py` | bs4 목록 파서(항목·제목·링크·날짜·요약 선택자, javascript 링크 → 상세 URL 템플릿, 날짜 정규식), 상세 본문 첫 300자. **저장소에서 HTML 파서를 import 하는 유일한 곳** |
| `app/notices/matcher.py` | 시설명 0.6 · 핵심어 0.3 · 약속 행동 키워드 0.3 토큰 겹침, 포함 용어 중복 제거 |
| `app/notices/sites.py`, `sites.yaml` | 등록부 로더(뉴스 도메인 등록 거부, orgs 는 canonical_org 키) + **실제 게시판 12곳**(광역 11 + 정책브리핑), 각 항목 `verified` 기록 |
| `app/notices/service.py` | 트리거 판정(`trigger_for`), 사건별 확인(`check_event`: 검색 목록/목록 → 발생일 이전이면 멈춤 → 제목 매칭 → 매칭 항목만 본문 → 저장·`org_notice_found`), `run_checks`, API 응답(`notices_out`: `state` = found / not_triggered / no_site / not_found / not_checked / error) |
| `app/api/notices.py` | `GET /api/notices/events/{id}`(공개, 외부 요청 없음), `POST /api/notices/run`(관리자 토큰) |
| `scripts/run_notices.py` | `--all` / `--event-id N` / `--check-sites [--site --query --pages --detail]` |
| `app/press/data.py` | 초안 자료원: 활성 사건 · 활성 연결 · 현재 약속 집합으로 분류된 유효 신호(judge 와 같은 기준, `trackable_promises`·`promise_set_hash`·`followup_articles` 재사용) · 기관(canonical_org) · 게시판 결과 · `experiment_latest.json`(있으면 리드·유형별·기관별 숫자) |
| `app/press/writer.py` | `PressWriter` 프로토콜, `TemplateWriter`(string.Template 문장 틀, 근거 번호 [n] ↔ sources, 조사 처리), `LLMWriter`(LLM_MODE=live 만: 일부 절 문장 다듬기, 숫자·[n]·금지 표현 검증 실패 시 템플릿 유지), `build_writer` |
| `app/llm/prompts/press_v1.md` | 보도자료 문장 교정 시스템 프롬프트 |
| `app/api/press.py` | `GET /api/press/draft`(전체), `?event_id=`(사건별), `GET /api/press/events`(선택 목록) |
| `pyproject.toml` | `beautifulsoup4>=4.12` 1개 추가 (`uv pip install --python .venv/bin/python -e .` 또는 `beautifulsoup4` 설치) |
| `tests/test_repo_rules.py` | 크롤링 금지 검사 추가: `app/notices/` 밖 HTML 파서 import 금지, 코드의 뉴스 도메인 URL 금지(빅카인즈 API 호스트 제외) + 허용 접미(.or.kr)의 뉴스 서비스도 거부 |
| `tests/test_notices.py` (30) | 파서 3종 구조, 도메인 차단(8 + 4 파라미터), 등록부 ≥10·광역 ≥10·URL 허용, robots RFC 9309, 요청기(간격·UA·robots·뉴스 리다이렉트 차단·5xx robots·바이너리 거부), 매칭 |
| `tests/test_notices_api.py` (10) | 트리거(상태·90일 경계·기준일 계산), 미트리거/게시판 없음 = 요청 0, 매칭 저장·본문·중복 방지·쪽 예산·오류 기록, API 상태·항목·관리자 토큰 |
| `tests/test_press.py` (15) | 전체/사건별 초안 숫자 = 판정 기준, 절 구성, [n] = sources, 대표 사례 3건, 게시판 결과, 실험 리포트 반영·깨진 파일 무시, 금지어·비난어 없음, 조사, LLMWriter 검증·대체 |

##### 프런트
| 파일 | 내용 |
|---|---|
| `src/app/press/page.tsx` | 화면 4: 상단 안내문, 전체/사건별 탭 + 사건 선택, 통계 타일, 초안 본문, 근거 기사, 면책 |
| `src/components/press/DraftBody.tsx` | 문단·목록·번호 목록 렌더, `[n]` → 근거 기사 앵커, URL 링크, 근거 기사 목록(새 창) |
| `src/components/press/DraftActions.tsx` | 초안 복사(클립보드 → 막히면 textarea 대체), Markdown 다운로드(`migyeol_press_{all|eventN}_YYYYMMDD.md`) |
| `src/components/press/EventPicker.tsx` | 사건 선택 → `/press?event_id=` |
| `src/components/notices/OrgNoticePanel.tsx` | 근거 화면 「기관 발표 확인」 패널: 항목(제목·게시일·게시판·요약·매칭 용어·점수) 또는 상태 설명, 등록 게시판·마지막 확인, 사건 초안 링크 |
| `src/lib/press.ts`, `src/lib/notices.ts` | 타입 + 서버 fetch (`lib/api.ts` 의 API_BASE·ApiError·safe 패턴, api.ts 는 수정 안 함) |
| `src/app/events/[id]/evidence/page.tsx` | 패널 추가만(import 2줄, 병렬 fetch 1줄, JSX 1블록) |

##### 문서
`docs/NOTICES.md`(규칙·등록 12곳 확인표·제외 사유·한계), 이 파일, 캡처 `docs/screenshots/press_all_top.png`·`press_all_cases.png`·`press_event.png`·`press_evidence_notice_panel.png`·`press_mobile.png`.

#### 결과
- 백엔드 테스트 359 통과(기존 225 + 트랙 A·B 신규 포함). 트랙 C 신규 57 = notices 30(파라미터 포함) + notices_api 10 + press 15 + repo_rules 2. `npx tsc --noEmit`·`npx eslint src` 깨끗.
- 등록 게시판 12곳 모두 목록 1·2쪽 실제 파싱(10~20건/쪽), 검색 URL 12곳 동작(2026-10-22, 요청 58회).
- mock DB(가상 8건, 기준일 2026-09-11): 트리거 2건(달빛지하차도 190일·청솔산) → 「등록된 게시판 없음」, 6건 「트리거 조건 미충족」, 요청 0회.

#### 통합 요청 (공용 파일 — 직접 고치지 않음)
1. `pipeline/scheduler.py`: 05:00 tracker 뒤에 `notices.service.run_checks(session, settings)` 일 1회(트리거된 사건만 요청). 파이프라인 Lock 밖에서 돌려도 됨(빅카인즈·LLM 호출 없음, DB 쓰기는 `org_notices`·`notice_checks`·`events.org_notice_found` 뿐).
2. `frontend/src/lib/types.ts`·`api.ts`: `lib/press.ts`·`lib/notices.ts` 의 타입·fetch 를 옮길지 결정(현재 독립 파일, 동작에는 지장 없음). 관리자 화면에 「기관 게시판 확인 실행」 버튼을 달려면 `/api/notices/run` 을 admin 프록시 경로에 추가해야 한다(`next.config.ts` rewrite 는 지금 토큰 없이 프록시 — ADMIN_TOKEN 설정 시 401).
3. `docs/API_CONTRACT.md`: §7.2·7.3 응답에 실제 필드 반영 — press: `event_id, header_note, writer, stats, data_source, experiment_generated_at`, `sources[].ref/provider/published_at`, `GET /api/press/events`; notices: `org, basis_date, trigger_days, state, state_label, note, disclaimer, sites[].name, items[].site_name`.
4. `docs/ARCHITECTURE.md` §6 "크롤링·원문 파싱 코드는 없다(grep beautifulsoup 결과 없음)" 문장 갱신: "언론사 원문 크롤링 없음. HTML 파서(bs4)는 `app/notices/`(공공기관 공식 게시판, §3 조건)에서만 — `tests/test_repo_rules.py` 가 고정".
5. 트랙 A `rules/orgs.py`: ① '전남광주통합특별시'(2026-07-01 출범) 별칭 정리 — 통합 이후 사건의 책임기관 키를 무엇으로 둘지(현재 등록부는 광주시·전남·전남광주통합특별시 셋 다 통합 누리집에 연결). ② 가상 기관 '누리철도공사' 가 `한국철도공사` 로 정규화됨(접미 '철도공사' 별칭이 앞 단어를 무시) — 실데이터에서 '○○철도공사'(예: 부산교통공사 계열) 오정규화 가능.
6. `README.md`·`DEMO_SCRIPT.md`: PT 마지막 단계 「근거(기관 발표 확인) → 보도자료 초안」 동선, `run_notices.py --check-sites` 를 시연 전 점검 항목으로.

#### 못 한 것
- 가상 사건은 등록부에 없는 기관이라 화면에서 「기관 발표 확인(항목 있음)」 상태는 실데이터 통합 후에만 보인다(테스트로는 고정). 가짜 게시판 항목을 DB 에 넣어 캡처하지 않았다.
- 전북·경남 보도자료 목록 URL 을 찾지 못했고, 경북은 목록이 스크립트 렌더링이라 제외. 세종·제주·대구·충북은 robots.txt 불허로 제외.
- 강원도 상세 본문 선택자는 맞지 않아 본문 요약 없이 제목·날짜·링크만 저장된다. 서울·울산·대전 상세 본문 요약에는 부서·등록일 메타가 섞인다.
- `LLMWriter` 는 실 키로 호출해 보지 않았다(가짜 클라이언트로 검증·대체 경로만 테스트).

## 2026-10-24 사건별 단어·문장 가중치 (군집 오염·후속 신호 오탐 줄이기)

에이전트 라벨(2026-10-23)이 가리킨 두 문제 — 지역어+재난어만으로 게이트를 통과하는 다른 사건 기사(군집 정밀도 0.58), 무관한 문장의 '완료·추진' 을 잡는 규칙 후속 신호(precision 0.53) — 를 가중치로 다뤘다. 전후·스윕: `docs/EXPERIMENT.md` §6, `reports/eval_labels_20261024.md`.

- `app/rules/weights.py`(신규): 사건별 TF-IDF 식 단어 가중치(핵심 문서 = 시드 문장 + 원천 기사 + 발생일 ±3일 게이트 통과 후보, DF = 기사 전체). 시드 고유어 하한 0.6, 지역명·일반 재난어·행정어 상한 0.05. 조사·용언 활용꼴을 떼는 토큰화. `weighted_overlap`·`sentence_weight`.
- `events.term_weights`(JSON, nullable — 간이 마이그레이션): link 단계 시작 때 `link.refresh_term_weights` 가 재계산.
- `pipeline/link.py`: 연결 결정을 순수 함수 `decide()` 로 분리. 게이트에 `weighted_overlap ≥ WEIGHT_MIN`(강한 규칙 제외), `WEIGHT_RULE_MIN` 미만이면 강한 규칙에서 내림, 점수 = (1-α)·코사인 + α·overlap. 새 설정 `WEIGHT_MIN=0.15 · WEIGHT_ALPHA=0.5 · WEIGHT_RULE_MIN=0.25`(셋 다 0 이면 이전 동작). 기존 `LINK_AUTO·LINK_REVIEW` 이름·값 그대로.
- `llm/rules_adapter.py` rules-v2: 문장 관련도(약속 action 임베딩 코사인 + 사건 가중치 단어 점수) 상위 `FOLLOWUP_TOP_SENTENCES=3`·`FOLLOWUP_SENTENCE_MIN=0` 문장 안에서만 표현을 본다. 제목도 한 문장, 약속 키워드가 없어도 사건 고유어 ≥0.8 문장은 후보. confidence 에 관련도 반영. 모델명이 rules-v2 로 바뀌어 신호 캐시가 무효가 된다. 파이프라인이 자기 임베딩 백엔드를 어댑터에 붙인다.
- `FollowupInput.term_weights`: 규칙 어댑터 전용, LLM 입력 JSON 에서는 빠진다(exclude).
- 실험: `_clear_rules_outputs` 가 이전 규칙 버전(rules-*) 산출물도 지운다. `report` 단계가 라벨 붙은 군집 표본 행·기관 쌍을 다시 뽑지 않고 유지(빠진 연결은 `자동판단=연결해제` 로 분모 제외, 빠진 기관 쌍은 건수 0).
- `scripts/evaluate.py --labels [--before-db]`·`--label-sweep "…"`(app/experiment/label_eval.py): 라벨 기준 전후·설정 조합 표(DB 사본).
- 결과(녹화 재생 재실행, 같은 라벨): 군집 정밀도 0.568→**0.652**(O 유지 0.90, X 제거 0.35), 후속보도 precision 0.532→**0.567**, recall 0.786→**0.810**. 상태 '새로운 문제 발생' 5→13 은 별도 확인 필요.
- 테스트 `tests/test_weights.py`(13건) 추가, 전체 379건 통과(가상 픽스처 상태 정확도 1.0 유지 — `WEIGHT_RULE_MIN` 0.3 은 픽스처의 기소 기사를 떨어뜨려 0.25 로 둠).
- 못 한 것: 연결이 바뀌어 달라진 약속의 재검색 질의 26건은 녹화에 없어(실 API 호출 금지) 이전 후보로 진행. 남은 오탐은 대부분 '같은 사건이지만 약속한 조치가 아닌 진행'(수사·원인 발표 전재)과 조치가 아닌 추출 약속 — 규칙으로는 한계, LLM live 전환 대상.

## 2026-10-29 새로운 문제 판정에 이전 완료 근거 함께 표시

- **규칙** (`rules/status.py` R1): 가장 최근 유효 신호가 새로운문제라 R1 이 적용될 때, 그 전에 R2(완료 근거)를 만족하던 완료 신호를 `Judgment.prior_done_signal_ids` 와 `evidence[].role="prior_done"` 으로 남긴다. 채택(`counted`)하지 않으므로 상태·신뢰도는 바뀌지 않는다. 사유 문장에 "그 전에 완료 근거 N건이 확인됐음(마지막 날짜)" 을 덧붙인다. R2 기준 미달 완료는 붙이지 않는다.
- **API** (`/api/events/{id}/evidence`): 신호마다 `prior_done` 추가(`considered` 이기도 함). `docs/API_CONTRACT.md` 갱신.
- **화면** (`/events/[id]/evidence`): 상태가 「새로운 문제 발생」이고 이전 완료 근거가 있으면 「이전 완료 근거」 패널(날짜순 발췌·기사 링크 + 이후 새로운 문제 보도 수). 신호 표의 반영 칸은 「이전 완료 근거」.
- **DB**: 새로운 문제 사건 10건만 저장된 신호로 규칙 재판정(`rejudge_event`, API·LLM 호출 없음, 기준일 2026-10-22). 상태·신뢰도 10건 모두 그대로, 7건에 이전 완료 근거(1~13건). 재판정 전 사본 `backend/data/_backup/migyeol.before-prior-done-20261029.db`.
- 테스트 3건 추가(이전 완료 근거 기록 / R2 미달 완료는 제외 / 새로운 문제 뒤 완료가 오면 완료로 복귀). 전체 pytest 515건 통과, 프런트 `tsc`·`eslint` 통과.

## 2026-10-27 (저녁) 사건 90 → 100건 — 실제 사건 10건 추가 (코드 변경 없음)

멘토 "실제 사건 더 찾아서 넣기(필수 아님)" → 사용자 결정 "100건". `docs/EXPERIMENT.md` §13, `docs/연구.md` 3.10.

- **후보**: 10-22 discover 미선택 그룹 864개 + 사건별 인용문 검색(실 API 94회) + 빈 달 재난어 질의 20구간 → 후보 17건 중 10건 채택(산업 6·교통 2·자연 2 — 세종~안성 고속도로 청룡천교 붕괴, 서천특화시장 화재, 에어부산 여객기 화재, 명일동 땅꺼짐, 경주 규모 4.0 지진, 반얀트리 공사장 화재, 국방과학연구소 폭발, 강릉 극한가뭄 제한급수, 부천 호텔 화재, 시청역 역주행). 뺀 7건과 이유는 §13.1 표. `seeds/events.yaml` 끝에 `auto_seed: false` 로 추가.
- **수집·연결·판정**: 새 10건만 사건 단위로 record 실행(요청 844회, 녹화 11,918 → 12,758). 기존 90건은 실행 전후 스냅샷이 같다(백업 `data/_backup/migyeol_before_100_20261027.db`). 사건당 기사 600건 상한에 7건이 닿았다(질의 건수 637~3,303).
- **라벨**(에이전트, 사람 아님): 후속보도 맹검 전수 10 + 반박 검증 9(1건 X 로), 군집 2,964행(`cluster_full.csv` 19,109 → 22,073), 기관 53쌍. 기존 90건 라벨은 그대로.
- **결과**: 시스템 후속보도 있음 53/90 → 63/100, 라벨 68/90 → 76/100, precision/recall/F1 0.925/0.721/0.810 → **0.905/0.750/0.820**, 군집 전체 라벨 0.901/0.897 → 0.910/0.905, 기관 0.815 → 0.837. `reports/experiment_latest.json`·`dashboard_20261022.json` 재생성. 기관 게시판 확인은 새 10건이 모두 '있음' 상태라 트리거 0(요청 0, 발표 확인 7건 그대로).
- **문서**: README 지표 표, `docs/DEMO_SCRIPT.md`·`docs/QA.md` 숫자(90→100, 53→63, 59%→63%, 0.93/0.72/0.81→0.91/0.75/0.82, 19,109→22,073, 300→355쌍). PT 덱 숫자는 상위 세션이 고친다.
- 전체 pytest 513건 통과, 프런트 `tsc`·`eslint` 통과(코드 변경 없음 — 대시보드·보도자료는 DB 에서 읽어 100건이 그대로 나온다).

## 2026-10-27 멘토 피드백(10-27) 반영 — 실제 사례·대시보드 간략화·기사체 초안·매칭 보강

멘토 피드백: ① 실제 기관 문서(공지)를 끌어오고 기관 페이지로 이동 ② 실제 사례 2개를 있는 그대로 ③ 대시보드가 너무 길다(현황표 5개 + 더보기) ④ 정확도 개선 디테일은 발표에서 빼고 Q&A로 ⑤ 보도자료 초안을 기사처럼. 결정 표는 개발 프롬프트 v2 §0-2. 전체 pytest 513건 통과, 프런트 `tsc`·`eslint` 통과. 실험 DB 는 재점수·재수집으로 `org_notices` 52 → 22건(활성 사건 7건에서 발표 확인), 백업 `backend/data/migyeol.db.bak-before-rescore`.

- **대시보드 간략화** (`frontend/src/app/page.tsx`): 기관별 후속보도 현황은 상위 5곳만(`OrgTable limit`) + 「전체 N곳 보기」→ 새 화면 `/orgs`(정렬 버튼·확인된/아직 없는 기관 묶음은 그쪽으로); 최근 후속보도 5건 + 「더 보기」→ 새 화면 `/followups`(50건); 확인 필요 사건은 그대로 5건.
- **기관 발표로 확인된 사례** (대시보드 맨 아래, `NoticeCases.tsx`): 새 집계 `GET /api/dashboard/notice-cases?per_kind=` — ① 발생일 이후 기관 발표가 있는데 그 뒤 확인된 후속보도(유효 신호)가 없는 사건(시설명이 있으면 발표가 시설명과 겹쳐야 함) ② 기관 발표로부터 30일 이상 지나서야 다음 후속보도가 확인된 사건. 사람이 고르지 않고 규칙이 활성 사건에서 고른다. 현재 ① 인천 청라 전기차 화재(인천시 수질검사 발표 2024-08-08, 발생 7일 뒤) ② 군위 제방 유실(정책브리핑 2023-10-15 → 후속보도 2024-07-17, 276일). 카드에 발표 제목 링크·「기관 게시판으로」·사건 근거 링크. 테스트 `test_dashboard_api.py` 2건 추가.
- **기관 발표 매칭 보강** (`backend/app/notices/matcher.py`·`service.py`, 별도 세션): 짧은 일반어 시설명·핵심어 쌍만 맞은 글은 가점 없이 임계값 미달(시설명·지역명·고유어가 제목에 있거나 사건 지역 어간이 제목·요약에 있어야 가점: 조치 어휘·발생 7일 내·요약), 제목에 다른 지역명이 있으면 감점. `scripts/run_notices.py --rescore`(네트워크 없이 저장분 재점수). 정책브리핑 등록부에 국가유산청·국무조정실·해양경찰청·중앙재난안전대책본부 등 추가(`rules/orgs.py` 별칭 포함), 활성 사건 90건 재수집(트리거 30, 요청 85, 오류 0 → found 7 / not_found 11 / no_site 12). 전후 표는 `docs/NOTICES.md` §6.
- **보도자료 기사체 모드** (`backend/app/press/writer.py` `ArticleWriter`, `GET /api/press/draft?format=brief|article`, 별도 세션): `/press` 상단 「사건 브리프 | 기사체」 전환. 기사체는 제목 → 부제 → 리드 → 본문(발언 인용 → 확인된 후속보도 → 확인되지 않은 것 → 기관 발표) → 배경(방법) → 마무리(기준일·면책). 숫자·날짜·근거 번호 [n]은 브리프와 동일(`data.py` 집계만 사용), 금지 표현·"확인됐다/확인되지 않았다" 규칙 유지, LLM 다듬기는 두 형식 모두 `polish_is_safe` 검사. 내려받기 파일명 `article_` 구분. 테스트 `test_press_article.py` 추가.
- **게시판 요약 표시**: 본문 300자 끝에서 잘린 HTML 엔티티 조각(`&nbs`)을 화면에서 정리(`lib/format.ts cleanSnippet`, 사례 카드·「기관 발표 확인」 패널).
- **문서·발표**: `docs/QA.md` 신규(예상 질문·답 — 정확도 산출법·라벨·사건 선정·크롤링·LLM·상태 표현·확장), `docs/DEMO_SCRIPT.md` §1(상위 5·전체 보기·사례 2건)·§5(기사체) 갱신, `docs/API_CONTRACT.md`·`README.md`·`docs/NOTICES.md` 갱신. PT 덱: 지표 두 장을 한 장으로(개선 과정·계산식 제외), 사례 장표 신설, 대시보드·보도자료 장표 캡처·문구 교체, 기관 발표 확인 4 → 7건.

## 2026-10-26 사건 라벨 재작성 — 맹검·전수 (후속보도 F1 0.695 → 0.810, 같은 시스템)
- 10-23 사건 라벨(사건당 표본 40건 + 시스템 판정 제시)의 결함을 바로잡기 위해 연결 기사 전부(14,330건)를 시스템 판정 없이 읽는 맹검 에이전트 라벨로 `gold/experiment_labels.csv` 의 사람_ 열을 교체(옛 파일은 `experiment_labels_20261023_sample40.csv`). 라벨 후속보도 42 → 68 / 90. 현재 시스템 precision/recall/F1 0.925 / 0.721 / 0.810. `docs/EXPERIMENT.md` §12, `docs/연구.md` 3.9, README·DEMO_SCRIPT 수치 갱신. 코드 변경 없음.

## 2026-10-25 후속 신호 — 약속 추출 엄격화 (규칙 어댑터)

`docs/EXPERIMENT.md` §8. 남은 후속보도 오탐 대부분이 다짐 없는 서술 조각('해상에 설치된', '…것으로 추정된다')이 '계획' 약속으로 추적된 데서 왔다.

- `llm/rules_adapter.py` ⓐ: 약속은 다짐 문장(하겠다·할 계획·할 예정·방침·키로·하기로·「조성한다」형·문장 끝 '…할 것')이 있어야 한다. 묘사·추정·현재 진행 설명·과거 계획은 해당없음. 행동 절은 다짐이 걸린 절에서 어절 경계로. 구체 행동어 확장.
- `pipeline/extract.py`: 약속 저장을 `store_promise()` 로 분리(동작 같음). `experiment/runner.py`: `ensure_source_promises()` — 원천 기사 인용문은 항상 추출(인용문 검색 창 밖이라 다시 추출되지 않던 문제).
- 달라진 약속 키워드 재검색 질의는 실 API 로 수집(record, 약 350회).
- 결과(DB 사본, 연결은 10-24 상태): 후속보도 precision 0.567→**0.614**, recall 0.810→**0.833**, 추적 약속 1,190→819. 원천 발언만 추적·표현 근접 조건·`SIGNAL_MIN_CONF` 상향은 라벨상 이득이 없어 넣지 않았다.
- 테스트: `tests/test_rules_adapter.py` 에 다짐 문장 규칙 10건.
- (오후) 상태 '새로운 문제 발생' 정리: 배경 서술·조건·부정·예방 촉구·논란은 새로운문제가 아니다(중단은 '다시 중단'·조치의 중단만, 재발은 사고어 옆 '또다시'). '영업 재개·재개통·정상화' 는 완료. §7 과 합친 실험 DB 에서 후속보도 P/R 0.630/0.810 그대로, 상태 새문제 16→9·완료 18→26. 녹화 없던 약속 재검색 14건은 실 API 로 채움. `experiment_latest.json`·대시보드 재생성(바꾸기 전 DB `data/_backup/migyeol_before_newproblem_20261025.db`). 테스트 12건 추가.

## 2026-10-25 기간 창·지역 필수 조건 (남은 군집 오염 줄이기)

§6 뒤 남은 오염(같은 지역 다른 사고·전국 재난 일반 기사·다른 시기 같은 유형)을 기간 창과 지역 필수 조건으로 다뤘다. 오염 유형 분포·전후·규칙별 기여·스윕: `docs/EXPERIMENT.md` §7, `reports/eval_labels_20261025.md`.

- `pipeline/link.py` `scope_checks`(게이트 끝에 붙는 조건, 탈락 `x-…` / 검토 큐 상한 `cap-…` 사유를 link_reason 에 남김):
  창 뒤(max(발생일, 원천 기사일) + `LINK_EARLY_DAYS`=90) 기사는 제목에 사건 고유어(가중치 ≥ `LINK_UNIQUE_MIN`=0.6 + 시설명) — 또는 제목 지역어 + 하이라이트 고유어 — 또는 약속 키워드 ≥2 필요,
  늦은 같은 유형 새 사고 속보는 검토 큐 이하, 기초 지역 사건은 지역어(또는 시설명) 필수, 광역 사건은 고유어(또는 제목에 광역 지명 + 사건 재난어) 필수,
  제목에 다른 지역 지명만 있으면 탈락, 다른 구체 장소의 새 사고 속보는 검토 큐 이하. 조건은 가중치 핵심 문서 선정에는 쓰지 않는다.
- 새 설정: `LINK_SCOPE_RULES=true`(false 면 이전 동작) · `LINK_EARLY_DAYS=90` · `LINK_WINDOW_FROM_SOURCE=true` · `LINK_LATE_MATCH=title_region` · `LINK_UNIQUE_MIN=0.6` · `LINK_REGION_PARENT_OK=false`. 기존 `WEIGHT_*`·`LINK_AUTO`·`LINK_REVIEW` 값 그대로.
- `rules/regions.py`: 기초 → 상위 광역 사전(`PARENT`), 광역 표기 정규화(`WIDE_CANON`), `region_base`·`region_parents`·`is_wide_region`, 보통명사와 겹치는 지명 제외 목록.
- `experiment/label_eval.py`·`scripts/evaluate.py`: `gold/cluster_full.csv`(활성 연결 전체 라벨)가 있으면 정밀도와 함께 재현율·F1 을 낸다. 스윕은 설정 조합마다 `scope_checks` 를 다시 적용한다.
- 결과(녹화 재생 재실행, 같은 라벨): 군집 정밀도 표본 0.652→**0.743**, 전체 라벨 0.671→**0.756**(재현율 0.803→0.788, F1 0.731→0.772), 후속보도 precision 0.567→**0.607**, recall 0.810→0.810.
  후속 신호 변경(8a9a6e9·c88b64d)과 합친 최종 재실행: 표본 **0.744**, 전체 라벨 **0.766 / 0.782**, 후속보도 precision/recall **0.630 / 0.810**(F1 0.709). 실험 DB·`experiment_latest.json`·`dashboard_20261022.json` 재생성.
- 일반 시설어(`물류센터·물류창고·창고·선착장·시장·공사장·선로·열차`)와 그 부분 문자열('물류')은 시설 일치·사건 고유어로 보지 않는다 — 이천 물류센터 화재에 붙던 2026 인천 쿠팡 물류센터 화재 기사 41건 제거.
- 검토 큐(needs_review) 연결이 집계·후속 신호에 들어가는 경로는 없었다(모두 `is_active`) — 테스트로 고정.
- 테스트 `tests/test_link_window.py`(38건: 지역 사전·창·지역 필수·다른 지역·재발·검토 큐 제외·회귀 사례 만덕 2026·영도구 2024-12·현대시장 2024-07). 가상 픽스처 평가는 조건을 켜도 끈 것과 같다(연결 P/R 1.0/0.955, 상태 정확도 1.0).
- 못 한 것: 연결이 바뀌어 달라진 약속 재검색 질의(게이트만 30건, 합친 실행 14건)는 녹화에 없어(실 API 호출 금지) 이전 후보로 진행. 국정자원 화재의 정책 후속(제목에 사건 고유어 없음) 39건은 창 뒤 조건에 걸려 빠진다.

## 2026-10-25 학습 기반 재순위 (LINK_RERANKER — 남은 군집 오염 줄이기)

§7 뒤에도 자동 연결의 약 23%가 다른 사건 기사(대부분 '무관'·'예방·정책 일반', 절반 이상이 강한 규칙 경로)였다. 규칙 추가(A)·피처 분류기(B)·교차 인코더(C)를 사건 단위 5-fold 교차검증으로 비교하고, 가장 좋은 B+C(피처 분류기 + 교차 인코더 평균)를 link 단계에 설정으로 넣었다 — **기본값은 끔**(`LINK_RERANKER=none`, 같은 날 밤 사용자 결정으로 켬 — 아래 항목). 진단·비교·채택·전후: `docs/EXPERIMENT.md` §9, `reports/rerank_cv_20261025.md`, `reports/eval_labels_20261025_rerank.md`.

- `pipeline/link.py`: 후보 판정을 `assess()`(게이트 → 코사인·overlap → 강한 규칙·다른 시설 → `decide`)와 `EventContext` 로 분리 — link 단계와 학습 스크립트가 같은 경로를 쓴다(실험 DB 의 지금 연결 16,749건을 그대로 재현). 재순위가 꺼져 있으면 결정·link_reason·link_score 가 이전과 같다.
- `pipeline/rerank.py`(신규): 숫자 피처 53개(판정 재료·게이트 사유·날짜·제목/하이라이트/키워드의 사건 단어·제목의 보도 성격·사건 메타), 피처 분류기(로지스틱 회귀 + 그래디언트 부스팅 확률 평균, 제목·하이라이트 토큰 해싱), 교차 인코더 점수기, 모델 묶음 저장·적재, 교차 적합(학습에 쓴 사건은 그 사건을 뺀 fold 모델·교차검증 확률), 학습 데이터 수집(`collect_pairs`).
- 새 설정: `LINK_RERANKER=none|features|cross` · `RERANK_AUTO` · `RERANK_REVIEW` · `RERANK_KEEP_LATE_FACILITY` · `RERANK_CROSSFIT` · `RERANK_MODEL_PATH` · `RERANK_CROSS_DIR` · `RERANK_BLEND`. 모델이 없거나 학습 임베딩과 지금 임베딩이 다르면(가상 픽스처 hash) 경고 뒤 이전 동작.
- `scripts/train_reranker.py`(신규): DB → 쌍 CSV, 규칙(매개변수도 교차검증)·피처 분류기·교차 인코더 비교표, 정책별 후속보도 시뮬레이션, 사건 단위 부트스트랩, 라벨 잡음 상한, 모델 학습(`models/reranker_features.joblib`, 4MB). `scripts/train_cross_encoder.py`(신규): 교차 인코더 5-fold(MPS), `--save` 로 전체 모델(`models/cross_encoder/`, fp16 220MB, gitignore).
- 의존성: `pyproject.toml` 선택 extra `rerank = scikit-learn, joblib`. 교차 인코더는 `embeddings` extra(sentence-transformers → transformers·torch).
- 교차검증(사건 90건 5-fold, 전체 라벨): 지금 게이트 P/R/F1 0.765/0.783/0.774 → 규칙 A1+A2 0.794/0.865/0.828 → 피처 분류기 B + 바닥 0.873/0.881/0.877 → 교차 인코더 C + 바닥 0.899/0.885/0.892 → **B+C + 바닥 0.900/0.900/0.900**(AUC 0.939, 검토 큐에 갇힌 라벨 O 1,833건 구출). 교차 인코더 학습 시간 MPS 0.52초/step·CPU 1.78초/step → MPS 5-fold 46분(표본 없이). 라벨 잡음 상한: 두 에이전트 라벨 일치 0.957(κ 0.91).
- 학습 모델은 만덕~센텀 2026 개통·지반 침하를 대부분 X 로 본다 → 바닥 규칙 `RERANK_KEEP_LATE_FACILITY`(창 뒤 자동 연결 + 제목 시설명은 내리지 않음)로 회귀 사례(만덕 18 · 영도구 2 · 현대시장 7)를 모두 지킨다.
- 실험 DB 적용(녹화 재생, `LINK_RERANKER=cross`): 군집 정밀도 표본 0.744→**0.840**, 전체 라벨 P/R 0.766/0.782→**0.901/0.897**, 검토 대기 4,437→1,843, 회귀 사례 유지(영도구 12-23 기사는 검토 대기→자동). 그러나 사건 단위 후속보도 P/R/F1 0.630/0.810/0.709 → 0.554/0.738/0.633 — 지금 TP 34건 중 8건이 다른 사건 기사를 근거로 맞힌 것이었고(재순위 뒤 3건), 건진 같은 사건 기사에서 규칙 어댑터 오탐이 늘었다. 근거까지 맞은 탐지는 26→28. 미리 정한 기준(후속보도 F1 하락 ≤ 0.02)을 넘어 **실험 DB 는 되돌렸다**(백업과 연결 수·지표·대시보드 같음 확인). 적용 결과 DB 는 `data/_backup/migyeol_after_rerank_20261025.db`, 켜는 명령은 §9.4.
- 재순위를 끈 link → followup 재실행이 백업과 같은 DB 를 만든다(판정 함수 분리가 동작을 바꾸지 않음을 실데이터로 확인).
- 테스트 `tests/test_rerank.py`(34건: 피처 값·일반어/특정 시설명·확률 임계·모델 묶음 적재·피처 목록/형식/임베딩 불일치 거부·교차 적합·설정 끔/모델 없음 = 이전 동작·link 단계 연동(대상·결정·점수·경로 표시·바닥)·교차 인코더 캐시·섞기). `tests/conftest.py` 는 `LINK_RERANKER=none` 고정, `test_repo_rules` 금지어 검사는 로컬 교차 인코더 폴더(gitignore — 제3자 토크나이저 어휘)를 건너뛴다. 전체 474건 통과.
- 못 한 것: 후속 신호 분류기 개선(재순위를 켜도 수치가 오르려면 필요 — §8·LLM live 몫), 사람 라벨로 재학습(라벨은 에이전트 것). 재순위 실행에서 바뀐 약속의 재검색 질의 35개는 녹화에 없어(실 API 호출 금지) 이전 후보로 진행.

## 2026-10-25 (밤) 재순위 켬 + 2단계 후속 신호 판정 보강 (rules-v3)

사용자 결정으로 학습 기반 재순위를 기본값으로 켜고(`LINK_RERANKER=cross`), 그 때문에 떨어진 사건 단위 후속보도 지표를 후속 신호 판정(2단계)에서 되찾았다. 진단·비교·채택·전후: `docs/EXPERIMENT.md` §10, `reports/followup_cv_20261025.md`. 연구 전체를 쉬운 말로 모은 `docs/연구.md`(신규).

- 실 API(`BIGKINDS_REPLAY_ONLY=false`): 재순위를 켠 뒤 녹화에 없던 약속 재검색 96회, 2단계 적용 중 새로 생긴 인용문·약속 재검색 17회(녹화 11,805 → 11,918, 실패 0). 빠진 질의를 채워도 후속보도 P/R/F1 은 0.544/0.738/0.626 — 원인은 판정이었다.
- 기본값 변경(`app/config.py`):
  `LINK_RERANKER=cross`(교차 인코더가 없으면 피처 분류기, 둘 다 없거나 학습 임베딩이 다르면 끔) ·
  `FOLLOWUP_CUTOFF=source`(후속 기사 = 원천 발언 다음 날부터. 신호가 가리킨 약속의 발언일 이전·당일 기사의 신호는 '불명확') ·
  `FOLLOWUP_MIN_LINK_PROB=0.6`(재순위 확률이 낮은 자동 연결은 후속 근거에서 뺌. 관리자 확정·수동 연결은 그대로, 재순위 바닥 규칙으로 남은 늦은 기사는 완료·새로운문제 근거로만) ·
  `FOLLOWUP_EXPRESSION_FIXES=true`('추가 피해 최소화·예방' 은 새로운 문제가 아님, 앞날을 가리키는 꼬리 없는 '재개장·재개통' 은 완료가 아님 → 규칙 이름 rules-v3, v2 캐시와 섞이지 않음).
- 비교만 하고 끈 스위치: `FOLLOWUP_ACTION_MATCH`(시설 이름 겹침은 같은 종류 행동일 때만) · `FOLLOWUP_SKIP_COMMITMENT`(기사가 인용한 다짐·요구 문장 제외) · `FOLLOWUP_KW_ALONE`(키워드 겹침만으로 인정).
- `pipeline/judge.py`: `cutoff_date`·`promise_dates`·`link_prob`·`usable_followup_link`·`kept_late`·`low_prob_link`. `followup_articles` 가 확률 바닥을 쓰고, `classify_followups` 가 약속 발언일 이전·당일 신호와 바닥 규칙 늦은 기사의 '진행' 을 '불명확' 으로 내린다(사유는 rationale 에 남김).
- `llm/rules_adapter.py`: `classify_followup_detail()`(신호 + 근거 세부 — 겹침·대상어·시설 이름·행동 종류·경로·다짐/요구 문장), `action_groups()`, 표현 고침, 비교용 스위치. `llm/schemas.py` `FollowupInput.identity_terms`(직렬화 제외). `experiment/label_eval.py` 시뮬레이션이 같은 기준일을 쓰고 캐시 키에 새 설정을 넣는다.
- `pipeline/rerank.py`: `LINK_RERANKER=cross` 인데 교차 인코더 폴더가 없으면 피처 분류기로 대신한다(경고).
- `experiment/report.py`: 리포트 머리말·한계의 규칙 이름을 실제 값(rules-v2 / rules-v3)으로 — 'rules-v1' 로 고정돼 있었다. `llm/rules_adapter.py` 머리말도 같은 내용으로.
- `scripts/train_followup.py`(신규): 후보 12,269건을 약속 묶음 3 × 규칙 변형 6 으로 다시 분류해 캐시(`--extract`), 기준일·확률 바닥·규칙 조합·학습 판정(로지스틱 회귀·그래디언트 부스팅, 안쪽 교차검증 문턱)을 사건 단위 5-fold 로 비교(`--cv` → `reports/followup_cv_20261025.md`).
- 비교(사건 라벨 90건, 같은 잣대): 지금 0.626 → 규칙 조합(학습 fold 로 고름) 0.591 · 학습 판정 0.681 ± 0.017(로지스틱 회귀, 나누기 6가지) · **기준일 + 표현 고침 + 확률 바닥 0.702**(교차검증 0.702 ± 0.000, 근거까지 맞은 탐지 31).
- 실험 DB 적용(`followup → report`, 출발점 백업 `data/_backup/migyeol_rerank_live_20261025.db`): 군집 전체 라벨 P/R 0.766/0.782 → **0.901/0.894**, 표본 0.744 → 0.843, 후속보도 P/R/F1 0.630/0.810/0.709 → **0.623/0.786/0.695**, 근거까지 맞은 탐지 26 → **31**, 자동 후속보도 54 → 53, 상태(완료/진행/새문제/부족/불가) 26/17/7/36/4 → 22/20/7/33/8. 회귀 사례(만덕 2026 19건 '새로운 문제 발생' · 영도구 3건 완료 · 현대시장 7건 완료) 유지. `experiment_latest.json`·`dashboard_20261022.json` 재생성.
- 테스트 `tests/test_followup_v3.py`(15건: 표현 고침·규칙 이름·스위치·기준일·확률 바닥·가상 DB 에서 내림). `tests/conftest.py` 는 가상 픽스처를 이전 값(`LINK_RERANKER=none`·`FOLLOWUP_CUTOFF=grace`·`FOLLOWUP_MIN_LINK_PROB=0`·`FOLLOWUP_EXPRESSION_FIXES=false`)으로 고정 — 가상 정답셋은 이전 규칙으로 설계됐다. `test_rerank` 의 교차 인코더 없음 테스트는 피처 분류기 대체로 바꿈. 전체 489건 통과. 가상 픽스처 평가(`evaluate.py`, mock)는 새 기본값으로 돌려도 상태 정확도 1.0 · 연결 P/R 1.0/0.955 그대로다(재순위는 hash 임베딩이라 꺼지고, 누리군 청솔산의 규칙 이름만 R4_no_followup → R4_stale_followup, 상태는 같음).
- 문서: `docs/연구.md`(신규), EXPERIMENT §10, ARCHITECTURE §3·§4·§5, README 결과 표, DEMO_SCRIPT 수치·사례(카드 예시 군위 → 통복터널, 5호선 상태, 유형별 자연재해 25), `.env.example`.
- 못 한 것: 목표 F1 0.709 에 0.014 모자람(사건 두 건 — 적용 중 실 API 로 받은 새 인용문이 산청·가평 사건에 약속을 더해 오탐 +1, 재현 실험은 0.702). 약속이 없는 사건 14건(라벨 '있음' 3건)·미연결/검토 대기 근거 3건은 2단계로 찾을 수 없다. 공공기관 게시판 확인은 다시 돌리지 않았다(바뀐 상태로 트리거 19 → 31건). 라벨은 에이전트 것이다 — 사건 라벨의 실질 상한은 F1 0.92 언저리(1차 라벨을 따라 해도 최종 라벨 대비 0.923), 군집 라벨은 0.98 언저리.

## 2026-10-26 3단계 연구 — 후속 판정 기사 단위 학습 (목표 F1 0.80, 닿지 못함)

사용자 지시로 후속보도 목표를 F1 0.80 으로 올렸다(사건 라벨 90건, 군집 0.90·회귀 사례 유지, 라벨·잣대는 그대로). 규칙 → 기사 단위 학습 → 교차 인코더 → 에이전트 판정으로 정답 보강 → 규칙과 섞기까지 시도했다. 진단·비교·병목: `docs/EXPERIMENT.md` §11, `docs/연구.md` 3.8, `reports/followup_pairs_cv_20261026.md`.

- `scripts/train_followup_pairs.py`(신규): 지금 DB 에서 사건 90건의 후보(연결 14,172, 판정 범위 4,961)·규칙 판정 3가지(모든 약속·원천 발언 전체·원천 발언의 다짐 절)·임베딩 유사도·학습용 기사 정답을 추출하고(`--extract`, 지금 판정 '있음' 53 · TP 33 · FP 20 그대로 재현), 기사 단위 학습 → 사건 최댓값, 규칙과의 혼합, 맹검 에이전트 판정과 라벨의 일치를 사건 단위 중첩 교차검증·사건 나누기 6가지로 잰다(`--cv`).
- `scripts/train_followup_cross.py`(신규): (원천 발언, 기사) 교차 인코더, 사건 단위 중첩 교차검증. '있음' 사건의 나머지 후보를 학습에서 빼면 원천 발언만 외우는 지름길(평가 기사 AUC 0.50)이 생겨 기본은 0 으로 둔다.
- `gold/followup_article_agent.csv`(855건)·`gold/followup_event_agent.csv`(90건): 사건 라벨·메모를 보여 주지 않은 에이전트 6개의 기사 판정(엄격 = 원천 발언의 그 조치 / 넓게 = 그 사건에 대한 기관 대응) — **에이전트 라벨, 학습용**(평가에는 쓰지 않는다).
- 결과(사건 라벨 F1): 지금 규칙 0.695, 피처 학습 0.646~0.676(나누기 평균), 교차 인코더 문턱 최적 0.677~0.680, 에이전트 정답 보강 0.657~0.668, **규칙 그리고 (원천 발언이 다짐 또는 학습 점수 ≥ t) 0.709 ± 0.018**(기본 나누기 0.736). 맹검 에이전트 판정 단독 0.597~0.697, 규칙과 섞어 최대 0.713, 라벨과의 일치 κ 0.29~0.40.
- 병목: 사건 라벨의 기준이 글만으로 재현되지 않는 폭(라벨 근거 89건 중 에이전트 엄격 동의 43% · 넓게 84%), 라벨러가 본 범위와 기준 차이('없음' 48건 중 12건에 엄격 기준 후속 기사 — 표본 밖 기사와 기준이 갈린 경우가 반쯤씩). 후보 상한은 0.88~0.90 이라 병목이 아니다.
- 제품 판정은 바꾸지 않았다 — 가장 나은 혼합의 이득(+0.014)이 나누기 사이 흔들림(± 0.018)보다 작다. 실험 DB·`experiment_latest.json`·대시보드·README 수치는 그대로.
- 고침: `llm/rules_adapter.RulesLLM._vectors` 캐시 상한 초과 시 이미 있던 문장이 사라져 KeyError(큰 실행에서만) — 비우기를 먼저. 테스트 `test_followup_v3.py` 1건 추가.
- 못 한 것: 0.80. 사람이 서면 기준(엄격 또는 넓게)으로 원천 발언 뒤 후보 전체를 보며 사건 라벨을 다시 붙이는 일이 먼저로 보인다(3.8 ⑧).
- (새벽 추가) 자문 답·측정 상한 검증(`docs/EXPERIMENT.md` §11.5, `docs/연구.md` 3.8 ⑨): 라벨 근거 99건 중 62건이 라벨러가 본 10-23 규칙의 양성 신호, '없음' 메모 48건 중 31건이 "신호·표본에서 못 찾음" 근거 — 0.80 은 이 라벨의 측정 상한 밖으로 보인다. 라벨과 무관한 정밀도 위생 2가지(응급대응 '완료' 안 세기 0.695 · 무관 약속 빼기 0.651)도 넣지 않았다. 사용자 선택지: 0.70 을 측정 상한으로 마무리 / 사람 맹검 판정으로 별도 평가 열(약 30건).

## 본선 단계 (2026-10-12 ~ 10-30) — Phase 1~3 (예정)

- Phase 1: `BIGKINDS_MODE=record` 전환, 첫 실호출 응답 키 검증 후 스키마·픽스처 보정, `discover` 초안 → 시드 20~30건 확정, 연결 정확도 1차 측정
- Phase 2: 실 데이터로 `extract`·`judge`·`trend`·`tracker`, 정답셋 30건 구축, 프롬프트 v2 비교, 스케줄러 활성화
- Phase 3: 3화면 실 데이터로 완성, 시연 대본·영상·GIF·캡처, README 평가 결과 표

## 결정 기록

- 2026-09-11 연결 게이트의 주제 조건에 "시설명 일치"를 추가. 사건분류 태그가 없는 착공·완료 후속 기사가 시설명으로만 잡히기 때문.
- 2026-09-11 약속 추출 후 **행동 키워드로 후속 기사를 추가 검색·재연결**하는 단계 추가. 후속 기사가 시설명 대신 다른 표현("관내 물류창고 34곳")을 쓰는 경우 누락되던 문제 해결.
- 2026-09-11 강한 규칙 일치(시설명이 제목에 있음 / 시설명+지역·기관 / 지역·기관+약속 키워드 2개)는 임베딩 점수와 무관하게 자동 연결. 규칙이 1차, 임베딩은 경계 사례 판정용.
- 2026-09-11 뉴스 검색 질의의 범위 절에 **시설명**을 추가 `(시설·핵심어) AND (지역 OR 기관 OR 시설)`. 지역·기관명 없이 시설명만 쓰는 후속 기사("달빛지하차도 침수 1년… 주민")가 수집되지 않던 문제 해결. 시설명이 일반적인 사건은 `seeds/events.yaml` 의 `query` 로 직접 좁힌다.
- 2026-09-11 픽스처의 "같은 지역 유사 사고" 노이즈 기사(누리군 달빛터널 낙석)는 시설명 없이 지역·사건분류만 일치하도록 조정 — 규칙 게이트는 통과하되 임베딩 점수로 검토 큐/폐기가 갈리는 경계 사례를 시연하기 위함.
- 2026-10-09 가상 시드·정답셋을 `backend/fixtures/{seeds,gold}/` 로 분리. 시드·정답셋 기본 경로는 모드를 따른다(mock → 가상, record/live → `seeds/`·`gold/` 실사건). 테스트는 가상 픽스처와 그 정답셋이 설계된 임계값(conftest 고정값)으로 돌고, `make_fixtures.py` 는 `fixtures/` 아래에만 쓴다(`--today` 필수). 본선에서 실사건 시드·정답셋을 채우거나 임계값을 바꿔도 테스트가 깨지거나 생성기가 덮어쓰지 않게 하기 위함.
- 2026-10-09 discover 에 날짜 범위 입력 추가(`run_pipeline.py --stage discover --from --until`, 명세 §7.1). 실사건 시드가 비어 있을 때 빈 초안을 쓰고 조용히 성공하던 경로는 오류로 바꿈.
- 2026-10-09 명세 §14 금지어 grep 이 비도록 프롬프트·테스트에서 금지어 문자열과 단정 예시 문장을 없애고, backend·frontend 소스를 검사하는 테스트를 추가.

## 2026-10-12 코드 리뷰(코드리뷰_2026-10-09.md) 반영

P0(1-1~1-12) 전부, 2장 medium 대부분, low 다수를 고쳤다. 검증은 pytest 73건 + 가상 사건 8건 상태 정확도 1.0.

- 1-1 수집 잘림: 발생일-1~+14일(상한 150) + 월별 구간(상한 80)으로 나눠 수집. `total_hits`·잘린 구간을 stats 에 남긴다. 명세 §7.2 의 '날짜 오름차순 300건' 은 폐기.
- 1-2/bk-8/bk-9 시드 `query` 가 첫 collect 에서 덮어써지던 문제: `Event.query_override` 보존, 사람 질의면 연관어 확장 안 함. 질의는 문자열 조작 대신 subjects/scopes 목록으로 조립. 파서는 괄호를 항상 분리하고 문법 오류를 `QueryError` 로 낸다.
- 1-3/ej-4~6/ev-3 공공 주체: 민간 표현(주민·유가족·시공사·씨·회사명 꼴 …)을 먼저 거르고(정부 직함 있으면 예외), 별칭은 단어 경계에서만, '이름 직함'·직함 단독·'소방 관계자' 인정. 회귀 테스트 추가.
- 1-4/ev-12/ej-9/ej-10 LLM 실패는 `LLMFailure` 로 올리고 저장하지 않는다(다음 실행 재시도). 캐시는 model 이 같을 때만. 완료·진행·새로운문제는 빈 발췌/100자 초과/입력에 없는 발췌면 불명확, 발췌가 '예정·목표' 면 완료→진행.
- 1-5 후속 신호에 분류 당시 추적 약속 집합 해시 저장, 바뀌면 재분류.
- 1-6 `upsert_article` 이 add 직후 flush — 두 약속 질의가 같은 신규 기사를 돌려줘도 UNIQUE 위반 없음.
- 1-7 수집 기사를 `EventCandidate(event_id, news_id, source=seed|promise)` 에 기록하고 link 후보로 쓴다. 로컬 질의 재매칭 폐기.
- 1-8/ev-2 게이트·임계값을 통과하지 못한 자동 연결은 삭제(수동·확정·해제는 유지). `evaluate.py --sweep` 은 DB 사본에서 돈다.
- 1-9/pc-8 `terms._STOP` 에 일반 행동어 추가. 약속 키워드 강한 연결은 '책임기관 + 키워드 2개' 또는 '지역 + 키워드 2개 + 시설명/시드 핵심어'.
- pc-9 제목에 시설명만 있고 지역·기관·사건분류가 없으면 강한 연결이 아니라 임베딩 점수로 (검토 큐 가능).
- 1-10/api-1/ct-3 관리자 연결 처리 뒤 `rejudge_event`(LLM 없이 규칙만) 재실행. 근거·근거 수·신호 목록에서 해제·검토 대기 연결, 삭제 기사, 이전 prompt_version 신호 제외. `restore` 액션 추가.
- needs_review(ev-14/ct-4/api-5/pc-10/ej-15): `recompute_needs_review` = 검토 대기 연결 OR 미처리 신고 OR 변경이력 표시(`change_flagged`). link·신고·처리·tracker 가 모두 이 함수를 쓴다.
- tracker(ej-15): Cancelled 근거 기사의 약속은 추적 중단, 바뀐 기사의 신호는 지우고 즉시 재분류·재판정.
- record(bk-4/bk-13/ev-17): `BIGKINDS_REPLAY_ONLY`, 키 없는 재생, 원자적 저장, 깨진 녹화 재녹화, 미스 목록. 녹화 키에 `until`(내일)이 들어가므로 시연은 녹화일의 `TODAY_OVERRIDE` 로 고정한다(README).
- live(bk-2/bk-3/bk-10/bk-11): 4xx 즉시 실패, 429 Retry-After, 비JSON·envelope 오류도 `BigKindsError`, 응답 null/타입 관대 파싱, KST 변환, `size` 0 방지, `fields` 에 `news_id` 명시. 지침서 예시 JSON 계약 테스트.
- 파이프라인(ej-16/api-12/pc-15/api-13/pc-11): 사건 단위 try/except, 전역 Lock(API 409), LLM 호출은 트랜잭션 밖, WAL+busy_timeout, 다른 시설명 목록은 항상 전체 시드 기준, 사건 간 다중 연결은 최고 점수만 유지.
- 상태 규칙(ev-13/ej-17/ej-18/ej-21): 같은 날 동률이면 새로운문제 우선, R2 2건 규칙은 서로 다른 발췌·날짜만, R5 사유 세분화, `counted_signal_ids` 저장.
- 약속(ej-11/ej-12/ej-22/api-14): 병합 대표는 확약>기한 있음>이른 기한, `merged_into_id`; 기한 'YYYY-MM'·없는 날짜 보정, 발언일 이전 기한 무효; (주체, 발언) 메모이즈 + 실행당 호출 상한.
- 단계(ej-3): 채택 여부와 무관하게 모든 유효 신호로 조치진행·결과확인 단계 갱신. 원인조사 판정은 제목+하이라이트.
- 추이(ej-20): 최근 평균은 완결된 직전 3개월. 우선순위(ej-8): 후속 기사 0건도 기준일 경과로 가산점.
- API(ct-2/api-9/fe-4/ct-11/api-10/api-11/ct-8/api-15/ct-5/api-22/ct-6/api-17/api-16/api-18/api-19/api-20/ct-13/api-21/ct-15/api-8/pc-14/api-6/api-7/ct-7/api-2/api-3): 카드의 기한·경과는 같은 약속(기한 지난 약속 우선); 우선순위는 요청 시점 재계산; 판정 전 사건은 명시 사유; `/api/meta` 에 `last_run`·`today_overridden`·경고; 스케줄러 꺼지면 `next_scheduled_run` null; 500 detail 에 트레이스백 없음; id 범위·news_id 형식·사건 소속 검증; 시드에서 빠진 사건은 `active=False`; 기동 시 누락 컬럼 ALTER TABLE; 파이프라인 API 기본 꺼짐; `ADMIN_TOKEN`(선택).
- 픽스처(ev-15/ev-16): 실존 기관명(한국철도공사·국토교통부·항공철도사고조사위·산림청)을 가상 기관으로 교체. `--today` 필수.
- 평가(ev-4~ev-10/ev-18): gold 스키마 검증(news_id 문자열·deadline 정규화·상태 5종), 자동 연결만 측정, 검토 큐 집계, 1:1 약속 매칭, 누락 사건 분모 포함, 부분 라벨 허용, `as_of` 기준일 경고, stub 순환 측정 표기, 파일명에 시각.
- Mock(bk-5/pc-18): 인용문 검색은 source+quotation 만 매칭(실서버 확인 전 보수적 가정) — 파이프라인의 인용문 질의는 주제어·기관명 OR 로 넓히고 연결 기사 ID 로 거른다. 연관어 동점 순서 고정.
- 프런트(fe-1~fe-16, ct-9/14/16/17/18, fe-3): 헤더 모드 배지·기준일 고정·실패 표시, 반영/참고/제외 표시, 완료 사건 기한 경과 배지 숨김, 추이 범위 밖 기한, 사건 없음 404 구분, 해제 확인·되돌리기, 필터 로컬 상태, 판정 전 표시, 페이지 범위, 스파크라인 0건, `/api` rewrite 프록시(동일 출처), mock 데이터 계약 일치.
- 보류: ej-14(90일 이후 후속 기사의 인용문 수집), pc-13(사건별 하이라이트 — 첫 하이라이트 유지로 완화), ev-19(USER_TEST_TEMPLATE), 녹화 키의 날짜 정규화.
- 실 API 첫 호출(2026-10-12, `scripts/smoke_live.py`): 서버가 `{"result": -1, "reason": "Invalid Access Key!:<key>"}` 를 돌려줬다 — 키 활성화 확인 필요. 오류 메시지에 키가 그대로 들어오므로 로그·리포트에서 가린다.
