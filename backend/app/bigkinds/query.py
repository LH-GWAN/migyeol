"""빅카인즈 검색 연산자(AND / OR / NOT / "구문" / 괄호)를 해석하는 소형 파서.

Mock 클라이언트가 픽스처 코퍼스를 실제 API 와 비슷하게 검색하기 위해 쓴다.
매칭은 소문자 부분 문자열 기준이다(한국어 형태소 분석 없음). 연산자 없이 나열된 항목은 AND 로 본다.
괄호는 따옴표 밖에서 항상 별도 토큰이다 (과일) 처럼 붙어 있어도 분리된다). 둥근 따옴표(“ ”)도 구문으로 본다.
문법 오류(짝 안 맞는 괄호, 끝에 남은 연산자)는 QueryError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r'\s*(\(|\)|"[^"]*"|“[^”]*”|[^\s()"“”]+)')


class QueryError(ValueError):
    pass


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


_OPS = {"AND", "OR", "NOT"}


class Query:
    def __init__(self, raw: str):
        self.raw = raw
        tokens = [m.group(1) for m in _TOKEN_RE.finditer(raw or "")]
        self._tokens = [t for t in tokens if t.strip()]
        self._pos = 0
        if not self._tokens:
            self._root: _Node | _MatchAll = _MatchAll()
            return
        self._root = self._parse_or()
        if self._pos < len(self._tokens):
            raise QueryError(f"질의 문법 오류: 처리되지 않은 토큰 {self._tokens[self._pos]!r} in {raw!r}")

    # -- grammar -------------------------------------------------------------
    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        if self._pos >= len(self._tokens):
            raise QueryError(f"질의 문법 오류: 끝에 연산자가 남음 in {self.raw!r}")
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
            nodes.append(self._parse_factor())
        return nodes[0] if len(nodes) == 1 else _And(nodes)

    def _parse_factor(self) -> _Node:
        tok = self._next()
        if tok.upper() == "NOT":
            return _Not(self._parse_factor())
        if tok == "(":
            node = self._parse_or()
            if self._peek() != ")":
                raise QueryError(f"질의 문법 오류: ')' 누락 in {self.raw!r}")
            self._next()
            return node
        if tok == ")":
            raise QueryError(f"질의 문법 오류: 짝 없는 ')' in {self.raw!r}")
        if (tok.startswith('"') and tok.endswith('"') or tok.startswith("“") and tok.endswith("”")) and len(tok) >= 2:
            return _Term(tok[1:-1].lower())
        if tok.upper() in _OPS:
            raise QueryError(f"질의 문법 오류: 피연산자 자리에 {tok} in {self.raw!r}")
        return _Term(tok.lower())

    # -- api -----------------------------------------------------------------
    def matches(self, text: str) -> bool:
        return self._root.matches((text or "").lower())

    def score(self, text: str) -> int:
        hay = (text or "").lower()
        return sum(hay.count(t) for t in self.terms())

    def terms(self) -> list[str]:
        return [t for t in self._root.terms() if t]
