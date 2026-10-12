"""비밀값 마스킹. 빅카인즈는 키가 틀리면 오류 메시지에 키를 그대로 돌려주므로(`Invalid Access Key!:<키>`),
예외 메시지·실행 기록·API 응답에 들어가기 전에 여기서 가린다.

- register_secret(): 설정에서 읽은 키를 등록한다 (클라이언트 생성 시).
- mask(): 등록된 비밀값과 UUID 형태 문자열을 `<access_key>` 로 바꾼다.
"""

from __future__ import annotations

import re

_SECRETS: set[str] = set()
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")  # 경계 없이 — key_<uuid>, <uuid>abc 도 가린다
MASK = "<access_key>"


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        _SECRETS.add(value)


def mask(text: object) -> str:
    s = str(text)
    for secret in _SECRETS:
        s = s.replace(secret, MASK)
    return _UUID_RE.sub(MASK, s)
