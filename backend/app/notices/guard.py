"""요청 URL 검사 — v2 §2-1·§3. 기관 게시판 확인 코드가 보내는 모든 요청(robots.txt·리다이렉트 포함)은 여기를 통과해야 한다.

1. http/https 만.
2. 뉴스 도메인(언론사·포털 뉴스·빅카인즈 웹)은 허용 목록과 무관하게 거부한다 — 언론사 원문 크롤링 금지(§2-1)는 그대로다.
3. 허용 = `NOTICE_ALLOWED_DOMAINS`(기본 .go.kr, .or.kr, korea.kr) 접미 일치 또는 sites.yaml 에 등록된 도메인.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

# 언론사·통신사·방송·포털 뉴스·뉴스 데이터 서비스의 등록 도메인. 하위 도메인까지 거부한다.
NEWS_DOMAINS: frozenset[str] = frozenset(
    {
        # 통신사·종합일간지
        "yna.co.kr", "yonhapnews.co.kr", "yonhapnewstv.co.kr", "newsis.com", "news1.kr", "chosun.com", "joongang.co.kr",
        "joins.com", "donga.com", "hani.co.kr", "khan.co.kr", "kmib.co.kr", "seoul.co.kr", "segye.com", "munhwa.com",
        "hankookilbo.com", "naeil.com", "asiae.co.kr", "mk.co.kr", "hankyung.com", "sedaily.com", "fnnews.com",
        "edaily.co.kr", "mt.co.kr", "heraldcorp.com", "ajunews.com", "nocutnews.co.kr", "ohmynews.com", "pressian.com",
        "newspim.com", "etnews.com", "dt.co.kr", "inews24.com", "zdnet.co.kr", "bloter.net", "mediatoday.co.kr",
        "sisain.co.kr", "hankyoreh21.co.kr", "koreaherald.com", "koreatimes.co.kr", "koreajoongangdaily.joins.com",
        # 방송
        "kbs.co.kr", "imbc.com", "mbc.co.kr", "sbs.co.kr", "ytn.co.kr", "jtbc.co.kr", "jtbc.joins.com", "ichannela.com",
        "mbn.co.kr", "tvchosun.com", "ebs.co.kr", "obsnews.co.kr", "tbs.seoul.kr", "cbs.co.kr", "bbsi.co.kr",
        "cpbc.co.kr", "febc.net", "arirang.com", "kfm.co.kr", "knn.co.kr", "tjb.co.kr", "kbc.co.kr", "jibstv.com",
        "ubc.co.kr", "tbc.co.kr", "cjb.co.kr", "g1tv.co.kr", "jtv.co.kr", "jejusori.net",
        # 지역 일간지
        "busan.com", "kookje.co.kr", "imaeil.com", "yeongnam.com", "idaegu.com", "kyeongin.com", "kihoilbo.co.kr",
        "incheonilbo.com", "joongboo.com", "kwnews.co.kr", "kado.net", "ccdailynews.com", "jbnews.com", "inews365.com",
        "daejonilbo.com", "joongdo.co.kr", "cctoday.co.kr", "jjan.kr", "jeonmin.co.kr", "domin.co.kr", "kjdaily.com",
        "kwangju.co.kr", "jnilbo.com", "namdonews.com", "gnnews.co.kr", "knnews.co.kr", "idomin.com", "ksilbo.co.kr",
        "iusm.co.kr", "jemin.com", "ihalla.com", "jejunews.com", "kbmaeil.com", "kyongbuk.co.kr", "dkilbo.com",
        # 포털 뉴스·뉴스 데이터
        "news.naver.com", "n.news.naver.com", "news.daum.net", "v.daum.net", "news.nate.com", "news.google.com",
        "kinds.or.kr", "bigkinds.or.kr", "tools.kinds.or.kr",
    }
)
# 뉴스 하위 도메인이 별도인 포털 (naver.com·daum.net 전체를 막지는 않지만 news.* 는 막는다)
_NEWS_LABEL_PREFIXES = ("news.", "n.news.", "m.news.")


class BlockedURL(ValueError):
    """허용되지 않은 요청 대상 (뉴스 도메인·허용 목록 밖·비 http)."""


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _under(host: str, domain: str) -> bool:
    domain = domain.lower().strip().lstrip("*").rstrip(".")
    if not domain:
        return False
    if domain.startswith("."):
        return host.endswith(domain)
    return host == domain or host.endswith("." + domain)


def is_news_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if any(_under(host, d) for d in NEWS_DOMAINS):
        return True
    # 공공 도메인이 아닌데 'news' 라벨로 시작하는 호스트(news.xxx.com 등)는 뉴스로 본다
    if not (host.endswith(".go.kr") or _under(host, "korea.kr")) and host.startswith(_NEWS_LABEL_PREFIXES):
        return True
    return False


def allowed_suffixes(allowed_csv: str) -> list[str]:
    return [s.strip() for s in allowed_csv.split(",") if s.strip()]


def check_url(url: str, allowed_csv: str, extra_domains: Iterable[str] = ()) -> str:
    """허용되면 호스트를 돌려주고, 아니면 BlockedURL. 순서: 스킴 → 뉴스 도메인 거부 → 허용 목록."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"http/https 가 아닌 URL: {url[:80]}")
    host = host_of(url)
    if not host:
        raise BlockedURL(f"호스트 없음: {url[:80]}")
    if is_news_host(host):
        raise BlockedURL(f"뉴스 도메인 요청 금지: {host}")
    if any(_under(host, s) for s in allowed_suffixes(allowed_csv)) or any(_under(host, d) for d in extra_domains):
        return host
    raise BlockedURL(f"허용 도메인 밖: {host}")
