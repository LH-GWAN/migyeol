# 공공기관 게시판 확인 (v2 §3) — 등록부와 확인 기록

> 트랙 C. 코드 `backend/app/notices/`, 등록부 `backend/app/notices/sites.yaml`, 실행 `backend/scripts/run_notices.py`, API `/api/notices/*`.

## 1. 규칙 요약

| 항목 | 내용 |
|---|---|
| 트리거 | 상태가 `후속보도 부족`·`판단 불가`(판정된 사건만)이고 `max(마지막 후속 기사일, 가장 늦은 약속 발언일, 발생일)`로부터 `NOTICE_TRIGGER_DAYS`(90)일 이상. **트리거되지 않은 사건은 robots.txt 를 포함해 어떤 요청도 보내지 않는다**(테스트 고정) |
| 대상 | 사건 `responsible_org` → `rules/orgs.canonical_org()` → 등록부에서 그 기관을 맡은 게시판만 |
| 읽는 범위 | 목록 페이지의 제목·게시일·URL(사이트당 최대 `NOTICE_MAX_PAGES`=5쪽, 발생일 이전 글만 남으면 멈춤). 게시판 자체 제목 검색이 있으면 시설명·핵심어로 검색 목록을 읽는다(같은 쪽수 예산). 제목이 매칭된 항목만 상세 본문 첫 300자(또는 목록 요약). 첨부·이미지·전체 본문 저장 없음 |
| 요청 예절 | 같은 호스트 요청 간격 `NOTICE_REQUEST_INTERVAL_SEC`(2초) 이상, User-Agent `NOTICE_USER_AGENT`, robots.txt 준수(RFC 9309: 최장 일치·Allow 우선, 4xx=제한 없음, 5xx·연결 실패=접근 안 함), 리다이렉트는 한 단계씩 다시 검사, HTML 아닌 응답·3MB 초과 거부 |
| 도메인 | 허용 = `NOTICE_ALLOWED_DOMAINS`(.go.kr, .or.kr, korea.kr) + 등록부 도메인. **뉴스 도메인(언론사·방송·포털 뉴스·빅카인즈 웹)은 허용 접미에 걸려도 코드에서 거부**(`guard.NEWS_DOMAINS`, 등록부 로드 시에도 거부) |
| 매칭 | 제목 토큰 겹침: 시설명 0.6, 사건 핵심어 0.3, 약속 행동 키워드(`promise_terms`) 0.3, 합 상한 1.0. `≥ NOTICE_MATCH_THRESHOLD`(0.5)만 `org_notices` 에 저장하고 `Event.org_notice_found=true` |
| 의미 | 보조 근거. 5개 상태를 바꾸지 않는다. 근거 화면 「기관 발표 확인」 패널·보도자료 초안에 제목·날짜·링크로 표시 |
| 기록 | 실행마다 `notice_checks`(트리거 여부·사유·결과 `not_triggered/no_site/found/not_found/error`·게시판별 쪽수·항목 수·오류·요청 수) |

## 2. 등록 게시판 (12곳) — 실제 목록 파싱 확인 2026-10-22

확인 명령: `cd backend && .venv/bin/python scripts/run_notices.py --check-sites --query 안전 --detail` (robots.txt·간격·UA 동일 경로, 요청 58회). 결과 원본은 `sites.yaml` 각 항목의 `verified`.

| key | 기관(canonical) | 목록 1·2쪽 항목 수 | 1~2쪽 게시일 범위 | 검색 '안전' 제목 일치 | 상세 본문 300자 |
|---|---|---|---|---|---|
| `seoul-press` 서울특별시 보도자료 | 서울시 | 10 / 10 | 2026-10-21 ~ 10-22 | 10/10 | 추출됨(제목·부서 메타 섞임) |
| `busan-press` 부산광역시 보도자료 | 부산시 | 12 / 12 | 2026-10-20 ~ 10-22 | 12/12 | 목록 요약(◈ 부제) 사용 |
| `incheon-press` 인천광역시 보도자료 | 인천시 | 10 / 10 | 2026-10-21 ~ 10-22 | 10/10 | 목록 요약 + 본문 |
| `gwangju-press-archive` (구)광주광역시 보도자료 | 광주시 | 15 / 15 | 2026-06-23 ~ 06-30 | 15/15 | 본문 |
| `jeonnam-gwangju-press` 전남광주통합특별시 보도자료 | 광주시, 전남 | 10 / 10 | 2026-10-21 ~ 10-22 | 10/10 | 본문 |
| `jeonnam-press-archive` (구)전라남도 보도자료 | 전남 | 10 / 10 | 2026-06-27 ~ 07-01 | 10/10 | 본문 |
| `ulsan-press` 울산광역시 보도자료 | 울산시 | 10 / 10 | 2026-10-20 ~ 10-22 | 10/10 | 추출됨(메타 섞임) |
| `gyeonggi-press` 경기도 보도자료(경기도뉴스포털) | 경기도 | 11 / 11 | 2026-10-21 ~ 10-22 | 10/11 | 본문 |
| `gangwon-press` 강원특별자치도 보도자료 | 강원도 | 15 / 15 | 2026-10-20 ~ 10-22 | 15/15 | 선택자 불일치(제목만 저장) |
| `chungnam-press` 충청남도 보도자료 | 충남 | 10 / 10 | 2026-10-19 ~ 10-20 | 5/10 (제목+내용 검색) | 목록 요약 사용 |
| `daejeon-press` 대전광역시 보도자료 | 대전시 | 11 / 11 | 2026-10-19 ~ 10-22 | 10/10 | 첨부형 게시판(메타만) |
| `korea-press` 정책브리핑 보도자료 | 국토교통부·행정안전부·고용노동부·환경부·해양수산부·산업통상자원부·농림축산식품부·소방청·경찰청·해양경찰청·산림청·기상청·교육부·보건복지부 | 20 / 20 | 2026-10-22 | 3/20 (본문 검색) | 목록 요약 사용 |

광역 지자체 11곳(서울·부산·인천·광주·대전·울산·경기·강원·충남·전남 + 통합특별시) + 중앙부처 창구 1곳. 매칭은 제목으로만 하므로 검색이 본문까지 맞추는 사이트(충남·정책브리핑)도 저장 기준은 같다.

사이트별 메모
- **서울시**: robots.txt 가 `Disallow: /` 뒤에 `Allow: /news` — RFC 9309 최장 일치로 `/news/news_report.do` 허용(표준 라이브러리 robotparser 는 첫 일치라 거부하므로 직접 구현). 목록 링크는 `javascript:fnTbbsView('번호')` → `?bbsNo=158&nttNo=번호` 로 조립.
- **부산시**: robots.txt 가 401 → RFC 9309 2.3.1.3(4xx 는 제한 없음). 기본 httpx UA 로는 연결이 끊기는 경우가 있었다(서비스 UA 로는 정상).
- **광주시·전남**: 2026-07-01 전남광주통합특별시 출범으로 두 누리집 보도자료는 2026-06-30 무렵에서 멈춘 기록 게시판이다. 두 기관 모두 통합특별시 임시 누리집(`jeonnam-gwangju.go.kr`)도 함께 본다. `rules/orgs.py` 에 '전남광주통합특별시' 별칭이 없어 통합 이후 사건의 책임기관 정규화는 트랙 A 확인 필요(통합 요청).
- **대전시**: robots.txt `Disallow: /` + `Allow: /drh` → 허용.
- **경기도**: 쪽 넘김에 `BS_CODE=s017` 이 필요(없으면 1쪽 반복). 검색 구분 `search=8`(제목).
- **정책브리핑**: RSS 는 2026-07-01 제공 중단(정책브리핑 공지 "정책브리핑 RSS 서비스 제공 중단 안내") → 보도자료 목록 페이지(`pressReleaseList.do`)를 같은 방식으로 읽는다. 기본 목록은 최근 1년이라 검색 URL 에 `startDate=2023-01-01` 을 넣었다.

## 3. 등록하지 않은 게시판 (robots.txt 불허·구조 문제)

| 기관 | 게시판 | 사유 |
|---|---|---|
| 세종시 | `sejong.go.kr/bbs/R0071/list.do` | robots.txt `User-agent: * / Disallow: /` |
| 제주도 | `jeju.go.kr/news/bodo/list.htm` | robots.txt `Disallow: /` (허용 경로에 /news 없음) |
| 대구시 | `info.daegu.go.kr/newshome/...` (보도자료) | robots.txt `User-agent: * / Disallow: /` |
| 충북 | `chungbuk.go.kr/www/selectBbsNttList.do?bbsNo=65` | robots.txt `User-agent: * / Disallow: /` (검색엔진에만 허용) |
| 경북 | `gb.go.kr/page/10107/10004.do` | 목록이 스크립트로 그려져 정적 HTML 에 항목 없음 |
| 전북·경남 | — | 보도자료 목록 URL 을 이번 확인에서 찾지 못함(다음 보강 대상) |

## 4. 실행

```bash
cd backend
.venv/bin/python scripts/run_notices.py --all                # 활성 사건 전체 — 트리거된 사건만 요청
.venv/bin/python scripts/run_notices.py --event-id 3
.venv/bin/python scripts/run_notices.py --check-sites --query 안전 --detail   # 등록부 점검 (이 문서의 표)
```

관리자 API: `POST /api/notices/run {"event_id": 3}` (`X-Admin-Token`). 조회: `GET /api/notices/events/{id}` → `{triggered, reason, state, state_label, checked_at, sites[], items[]}`.

가상 픽스처(가온시·누리군)는 등록부에 없으므로 mock DB 에서는 트리거된 2건(달빛지하차도·청솔산)이 「등록된 게시판 없음」, 나머지 6건이 「트리거 조건 미충족」이고 요청 수는 0이다. 「기관 발표 확인」 경로는 `tests/test_notices_api.py` 가 고정 HTML(가상 가온시 게시판)로 검증한다.

## 5. 한계

- 목록 제목 매칭이라 제목에 시설명·핵심어가 없는 발표는 놓친다(검색 지원 게시판은 시설명 검색으로 보완). 보도자료가 첨부파일(hwp/pdf)뿐인 게시판은 본문 요약이 비거나 메타데이터만 남는다.
- 목록은 최신순 5쪽까지만 본다. 검색이 없는 게시판(없음 — 12곳 모두 검색 URL 등록)에서는 오래된 사건의 발표가 5쪽 밖일 수 있다.
- 게시판 구조가 바뀌면 선택자가 깨진다 → `--check-sites` 를 시연 전에 다시 돌려 이 표를 갱신한다(항목 0건이면 종료 코드 1).
