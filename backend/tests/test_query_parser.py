from app.bigkinds.query import Query


def test_and_or_not_and_phrase():
    q = Query('("달빛지하차도" OR "지하차도 침수") AND ("가온시" OR "가온시청") NOT 축제')
    assert q.matches("가온시 달빛지하차도 침수 사고")
    assert q.matches("가온시청, 지하차도 침수 대책")
    assert not q.matches("가온시 별빛축제 개막 달빛지하차도 인근 축제")  # NOT
    assert not q.matches("누리군 지하차도 침수")  # 지역 절 불일치


def test_phrase_requires_exact_substring():
    q = Query('"터널 붕괴"')
    assert q.matches("해맞이터널 붕괴 사고")
    assert not q.matches("터널이 붕괴됐다")


def test_implicit_and_between_terms():
    q = Query("서비스 출시")
    assert q.matches("새 서비스가 출시된다")
    assert not q.matches("새 서비스가 나온다")


def test_empty_query_matches_everything():
    assert Query("").matches("anything")
    assert Query("").terms() == []


def test_terms_and_score():
    q = Query('"배수펌프" OR 증설')
    assert set(q.terms()) == {"배수펌프", "증설"}
    assert q.score("배수펌프 증설, 배수펌프") == 3
