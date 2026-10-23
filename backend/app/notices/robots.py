"""robots.txt 해석 — RFC 9309 (2022) 의미론.

표준 라이브러리 urllib.robotparser 는 규칙을 파일 순서대로 첫 일치로 보고 401/403 을 전면 금지로 다뤄 RFC 와 다르다
(예: 서울시 robots.txt 는 'Disallow: /' 다음에 'Allow: /news' 를 둔다 — RFC 로는 /news 허용, robotparser 로는 금지).
그래서 여기서는 RFC 9309 를 그대로 구현한다.

- 그룹 선택: user-agent 제품 토큰(대소문자 무시)이 일치하는 그룹들을 합친다. 없으면 '*' 그룹들. 둘 다 없으면 제한 없음.
- 규칙 일치: 경로 접두 일치, '*' 는 임의 문자열, 끝의 '$' 는 경로 끝. **가장 긴 패턴**이 이기고 같으면 Allow 가 이긴다.
- /robots.txt 자체는 항상 허용.
- 상태 코드(호출자 처리): 2xx 파싱 / 4xx(401·403 포함) 제한 없음 / 5xx·연결 실패 전면 금지(접근 불가로 본다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class RobotsRules:
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    disallow_all: bool = False  # 5xx·연결 실패 → 접근하지 않는다

    def can_fetch(self, url: str) -> bool:
        if self.disallow_all:
            return False
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = unquote(path) + (f"?{unquote(parts.query)}" if parts.query else "")
        best_len, best_allow = -1, True
        for allow, pattern in self.rules:
            if _matches(pattern, target):
                n = len(pattern)
                if n > best_len or (n == best_len and allow):
                    best_len, best_allow = n, allow
        return best_allow


def _matches(pattern: str, target: str) -> bool:
    if not pattern:
        return False  # 빈 Disallow 는 아무것도 막지 않는다
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in unquote(body))
    return re.match(regex + ("$" if anchored else ""), target) is not None


def parse_robots(text: str, user_agent: str) -> RobotsRules:
    """robots.txt 본문 → 이 user-agent 에 적용되는 규칙."""
    token = user_agent.split("/")[0].split(" ")[0].strip().lower()
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # 규칙 뒤에 새 user-agent 가 오면 새 그룹
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            if not agents:
                continue  # 그룹 밖 규칙은 무시
            in_rules = True
            rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))

    mine = [r for a, rs in groups if any(x != "*" and x == token for x in a) for r in rs]
    if not any(any(x != "*" and x == token for x in a) for a, _ in groups):
        mine = [r for a, rs in groups if "*" in a for r in rs]
    return RobotsRules(rules=mine)


def rules_for_status(status: int, text: str, user_agent: str) -> RobotsRules:
    if 200 <= status < 300:
        return parse_robots(text, user_agent)
    if 400 <= status < 500:
        return RobotsRules()  # RFC 9309 2.3.1.3: unavailable → 제한 없음
    return RobotsRules(disallow_all=True)  # 2.3.1.4: unreachable → 전면 금지
