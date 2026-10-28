"""민간 지원 언급 추출 규칙 (docs/SUPPORT.md) — 가상 문장만 쓴다 (실기사 문장은 넣지 않는다).

어휘 목록 고정, 어휘 경계('경기부지사' 의 '기부'), 주체 판별(기업·단체 접미·상호 접두), 공공 주체·공직자 모임·모금 창구 제외,
수혜·경유 주체 제외, 사건 설명 속 이름 제외, 개인 익명화, 당사자 표시, 같은 기사 안의 부분 이름 중복 제거.
"""

from __future__ import annotations

from app.rules import support_terms as T
from app.support.extractor import actors_in, assign_roles, event_text, extract_article, individual_in, support_terms_in

TITLE = "가온은행, 달빛지하차도 침수 피해 복구 성금 1억원 기탁"
HILIGHT = "가온은행은 20일 달빛지하차도 침수로 피해를 본 상인들을 위해 성금 1억원을 가온시에 기탁했다고 밝혔다. ..전달식에는 홍길동 가온은행장이 참석했다.."


# ---------------------------------------------------------------------------
# 어휘 (상수 고정)
# ---------------------------------------------------------------------------


def test_support_terms_are_fixed_and_factual():
    for w in ("성금", "기부", "후원금", "지원금", "위로금", "구호물품", "구호품", "긴급구호", "복구 지원", "의료 지원", "무상 지원", "물품 전달", "자원봉사", "봉사활동", "성금 전달", "기탁"):
        assert w in T.SUPPORT_TERMS
    assert len(T.SUPPORT_TERMS) == len(set(T.SUPPORT_TERMS))
    # 평가 단어는 어휘·상수 어디에도 없다 (사실 표현만)
    for w in ("선" + "행", "미" + "담", "착" + "한", "훈" + "훈"):
        assert not any(w in t for t in T.SUPPORT_TERMS)
    assert "기부채납" in T.SUPPORT_MASKS and "재난지원금" in T.SUPPORT_MASKS
    assert "은행" in T.CORPORATE_SUFFIXES and "재단" in T.ASSOCIATION_SUFFIXES and "적십자" in T.ASSOCIATION_PREFIXES
    assert "화재" not in T.CORPORATE_SUFFIXES  # '삼성화재' 는 접두 목록으로만 — 이 프로젝트에서 화재는 사고다


def test_term_matching_boundaries():
    assert "성금" in support_terms_in("가온은행이 성금 1억원을 기탁했다")
    assert support_terms_in("오병권 경기부지사가 야탑점을 방문했다") == []  # '경기부지사' 안의 '기부' 는 아니다
    assert support_terms_in("중기부는 현장대응반을 꾸렸다") == []
    assert support_terms_in("재능기부에 동참했다") == ["기부"]
    assert support_terms_in("공원 부지를 기부채납 받았다") == []
    assert support_terms_in("재난지원금을 지급했다") == []
    assert support_terms_in("재해구호기금을 투입했다") == []
    assert support_terms_in("자원봉사센터를 열었다") == []  # 기관명 안의 어휘는 아니다
    assert set(support_terms_in("구호물품과 생필품을 전달했다")) >= {"구호물품", "생필품"}


# ---------------------------------------------------------------------------
# 주체
# ---------------------------------------------------------------------------


def _names(sentence: str, is_title: bool = False, blob: str = "") -> list[str]:
    return [a for a, _k, _c in actors_in(sentence, is_title=is_title, party_blob=blob)]


def test_corporate_and_association_actors():
    assert actors_in(TITLE, is_title=True) == [("가온은행", "기업", "가온은행")]
    assert _names("누리그룹은 수해 피해 복구 성금 10억원을 기탁했다") == ["누리그룹"]
    assert _names("한빛재단과 별빛교회가 피해 복구를 위해 성금을 전달했다") == ["한빛재단", "별빛교회"]
    assert actors_in("KT가온, 수해 지역에 생필품 지원", is_title=True)[0][1] == "기업"  # 상호 접두
    assert actors_in("㈜누리상사는 희생자 유가족 지원을 위해 1억원을 기탁했다")[0] == ("㈜누리상사", "기업", "㈜누리상사")
    assert _names("인천 새마을금고, 피해 복구 성금 5800만원 전달", is_title=True) == ["인천 새마을금고"]  # 앞의 지역명을 붙인다
    assert _names("다솜 봉사단, 피해 상인 위해 350만원 기탁", is_title=True) == ["다솜 봉사단"]  # 접미만 있는 토큰은 앞 토큰과
    assert _names("가온 농·축협과 NH다솜 가온군지부가 성금 3000만원을 전달했다") == ["농·축협", "NH다솜 가온군지부"]


def test_subject_chains_and_branches():
    assert _names("누리은행 가온본부가 성금 1억원을 기탁했다") == ["누리은행 가온본부"]
    assert _names("가온은행 임직원 50여명이 수해 현장에서 자원봉사를 했다") == ["가온은행"]
    assert _names("가온은행, 누리카드 등 기업들은 구호물품을 기부했다") == ["가온은행", "누리카드"]
    assert _names("한빛건설㈜ 이은주 대표, 누리건설㈜ 장현준 대표가 성금 3500만원을 기탁하는 모습") == ["한빛건설㈜", "누리건설㈜"]
    # 주어가 아닌 수식어('가온은행의 전폭적인 지지')는 주체가 아니다
    assert _names("임직원들의 봉사 활동의 배경엔 가온은행의 전폭적인 지지가 있었다") == []


def test_public_actors_and_public_sphere_are_excluded():
    assert _names("가온시는 피해 상인들에게 성금 7억원을 전달했다") == []
    assert _names("가온시설관리공단은 구호물품을 전달했다") == []
    assert _names("행정안전부, 호우 피해 지역에 재난구호지원금 2억 교부", is_title=True) == []
    assert _names("가온소방서 의용소방대와 가온군청 직원들이 봉사활동을 펼쳤다") == []
    assert _names("가온시장군수협의회는 성금 1000만원을 전달했다") == []  # 공직자 모임
    assert _names("가온시의회 누리당 교섭단체는 복구 성금을 전달했다") == []
    assert _names("누리당 당원 성금 전달식", is_title=True) == []
    assert _names("가온의료원 누리병원이 의료지원을 했다") == []  # 공공 조직 산하 단위
    assert _names("분당서울대병원 재난의료지원팀이 의료지원을 했다") == []


def test_recipients_channels_and_event_phrases_are_excluded():
    # 모금 창구·수혜자('…에', '…를 통해', '회장에게')는 주체가 아니다
    assert _names("누리그룹은 성금 1억원을 가온사회복지공동모금회에 기탁했다") == ["누리그룹"]
    assert _names("누리그룹은 성금을 희망나루 전국재해구호협회에 전달했다") == ["누리그룹"]
    assert _names("가온은행은 성금을 대한적십자사 가온지사에 기탁했다") == ["가온은행"]
    assert _names("한동훈 위원장이 김철수 대한적십자사 회장에게 성금을 전달했다") == []
    assert _names("성금 모금은 가온사회복지공동모금회(모금 계좌: 농협 123-45)를 통해 진행된다") == []
    # 사건 설명의 일부('한빛건설 공사장 붕괴', '누리항공 참사')는 주체가 아니다
    assert _names("한빛건설 공사장 붕괴 피해 주민에게 가온은행이 위로금을 전달했다") == ["가온은행"]
    assert _names("가온공항공사는 누리항공 여객기 참사 유가족 지원을 위해 성금 2억원을 냈다") == []
    assert _names("가온은행과 지역농축협 계좌에 지원금을 지급했다") == []  # 'A과 B 계좌에' 는 공동 주어가 아니다


def test_individual_is_anonymised():
    assert individual_in("김모씨는 익명으로 성금 500만원을 기탁했다")
    assert individual_in("이름을 밝히지 않은 독지가가 성금을 보냈다")
    assert not individual_in("온정의 불씨는 꺼지지 않았다")  # '불씨' 는 사람이 아니다
    assert not individual_in("유가족 김모씨에게 위로금이 전달됐다")  # 수혜자
    out = extract_article("익명의 기부", "김모씨는 피해 상인들을 위해 성금 500만원을 익명으로 기탁했다", None)
    assert [(m.actor, m.actor_kind) for m in out] == [("개인", "개인")]
    assert "김모" not in out[0].actor


# ---------------------------------------------------------------------------
# 기사 단위
# ---------------------------------------------------------------------------


def test_extract_article_merges_subnames_and_keeps_evidence_short():
    out = extract_article(TITLE, HILIGHT, None, keywords=["가온은행", "성금", "기탁"])
    assert [m.actor for m in out] == ["가온은행"]
    m = out[0]
    assert m.actor_kind == "기업" and set(m.support_terms) >= {"성금", "기탁"}
    assert m.evidence_span.startswith("가온은행은 20일") and len(m.evidence_span) <= 200
    # 'KB가온은행'(발췌) 과 '가온은행'(제목) 은 한 주체
    out2 = extract_article("가온은행, 피해 복구 성금 1억원 기탁", "KB가온은행은 피해 복구 성금 1억원을 기탁했다", None)
    assert [m.actor for m in out2] == ["KB가온은행"]
    long = "누리그룹은 " + "피해 복구 성금을 전달했다 " * 20
    assert len(extract_article("누리그룹 성금", long, None)[0].evidence_span) <= 200


def test_extract_article_skips_irrelevant_or_negative_articles():
    # 사건 관련어(피해·복구·이재민 …)가 없는 기업 소식은 보지 않는다
    assert extract_article("가온은행, 연말 사랑의 연탄 봉사활동", "가온은행 임직원들이 봉사활동을 펼쳤다", None) == []
    # 촉구·수사·표창 문장은 지원 행위가 아니다
    assert extract_article("성금 촉구", "누리당 의원이 피해 학교에 대한 복구 지원을 촉구했다", None) == []
    assert extract_article("표창", "가온봉사센터가 수해복구 자원봉사 공로로 표창을 받았다", None) == []
    assert extract_article("지원금", "한빛건설 협력업체들도 피해를 입어 고용유지지원금을 지원받을 수 있다", None, party_blob="한빛건설") == []
    # 발생 이전·무관 기사: 어휘가 없으면 빈 목록
    assert extract_article("가온시 달빛지하차도 침수 사고 원인 조사", "가온시는 피해 원인을 조사한다", None) == []


def test_party_role_from_event_description():
    blob = event_text("가온 한빛건설 공사장 붕괴", summary="시공사 한빛건설 현장에서 작업자가 숨졌다", facility="한빛건설 공사장", responsible_org="가온시")
    out = extract_article(
        "한빛건설·가온은행, 붕괴 사고 유가족에 위로금 전달",
        "한빛건설은 유가족에게 위로금을 전달했다. ..가온은행도 피해 복구 성금 1억원을 기탁했다..",
        None,
        party_blob=blob,
    )
    roles = {m.actor: m.actor_role for m in assign_roles(out, blob)}
    assert roles == {"한빛건설": "당사자", "가온은행": "제3자"}
    # 당사자 이름이라도 사건 설명의 일부로만 나오면('한빛건설 공사장 붕괴') 주체가 아니다
    only_desc = extract_article("한빛건설 공사장 붕괴 피해 주민에 성금", "가온은행은 한빛건설 공사장 붕괴 피해 주민을 위해 성금을 전달했다", None, party_blob=blob)
    assert [m.actor for m in only_desc] == ["가온은행"]
