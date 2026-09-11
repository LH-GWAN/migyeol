"""빅카인즈 검색 연산자(AND / OR / NOT / "구문" / 괄호)를 해석하는 소형 파서.

Mock 클라이언트가 픽스처 코퍼스를 실제 API 와 비슷하게 검색하기 위해 쓴다.
매칭은 소문자 부분 문자열 기준이다(한국어 형태소 분석 없음). 연산자 없이 나열된 항목은 AND 로 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r'\s*(\(|\)|"[^"]*"|\S+)')


@dataclass
class _Term:
    text: str

    def matches(self, haystack: str) -> bool:
        return self.text in haystack

    def terms(self) -> list[str]:
        return [self.text]


@dataclass
class _Not:
    node: "_Node"

    def matches(self, haystack: str) -> bool:
        return not self.node.matches(haystack)

    def terms(self) -> list[str]:
        return []


@dataclass
class _And:
    nodes: list["_Node"]

    def matches(self, haystack: str) -> bool:
        return all(n.matches(haystack) for n in self.nodes)

    def terms(self) -> list[str]:
        return [t for n in self.nodes for t in n.terms()]


@dataclass
class _Or:
    nodes: list["_Node"]

    def matches(self, haystack: str) -> bool:
        return any(n.matches(haystack) for n in self.nodes)

    def terms(self) -> list[str]:
        return [t for n in self.nodes for t in n.terms()]


_Node = _Term | _Not | _And | _Or


class _MatchAll:
    def matches(self, haystack: str) -> bool:
        return True

    def terms(self) -> list[str]:
        return []


class Query:
    def __init__(self, raw: str):
        self.raw = raw
        tokens = [m.group(1) for m in _TOKEN_RE.finditer(raw or "")]
        self._tokens = [t for t in tokens if t.strip()]
        self._pos = 0
        self._root: _Node | _MatchAll = self._parse_or() if self._tokens else _MatchAll()

    # -- grammar -------------------------------------------------------------
    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _parse_or(self) -> _Node:
        nodes = [self._parse_and()]
        while self._peek() is not None and self._peek().upper() == "OR":
            self._next()
            nodes.append(self._parse_and())
        return nodes[0] if len(nodes) == 1 else _Or(nodes)

    def _parse_and(self) -> _Node:
        nodes = [self._parse_factor()]
        while True:
            tok = self._peek()
            if tok is None or tok == ")" or tok.upper() == "OR":
                break
            if tok.upper() == "AND":
                self._next()
                if self._peek() is None or self._peek() == ")":
                    break
            nodes.append(self._parse_factor())
        return nodes[0] if len(nodes) == 1 else _And(nodes)

    def _parse_factor(self) -> _Node:
        tok = self._next()
        if tok.upper() == "NOT":
            return _Not(self._parse_factor())
        if tok == "(":
            node = self._parse_or()
            if self._peek() == ")":
                self._next()
            return node
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            return _Term(tok[1:-1].lower())
        return _Term(tok.lower())

    # -- api -----------------------------------------------------------------
    def matches(self, text: str) -> bool:
        return self._root.matches((text or "").lower())

    def score(self, text: str) -> int:
        hay = (text or "").lower()
        return sum(hay.count(t) for t in self.terms())

    def terms(self) -> list[str]:
        return [t for t in self._root.terms() if t]
