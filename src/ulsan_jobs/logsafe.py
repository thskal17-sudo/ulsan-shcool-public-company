"""로그에서 API 인증키를 가린다.

urllib3 는 연결을 다시 시도할 때 요청 주소를 WARNING 으로 남기는데, 공공데이터포털·고용24 는
인증키를 주소의 쿼리(serviceKey=, authKey=)로 받는다. Actions 로그는 공개이고, GitHub 의 비밀값
가리기는 등록한 문자열과 똑같을 때만 가리므로 '+'·'/' 가 %2B·%2F 로 바뀐 키는 그대로 보인다.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

_KEY_QUERY = re.compile(r"(?i)\b((?:service|auth)key=)[^&\s\"'<>]+")
_URL_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")  # http://아이디:비밀번호@ (국내 중계 주소)
SECRET_ENVS = ("G2B_API_KEY", "WORK24_API_KEY", "DATA_GO_KR_KEY", "KR_PROXY_URL")


def redact(text: str) -> str:
    text = _KEY_QUERY.sub(r"\1***", text)
    text = _URL_USERINFO.sub(r"\1***@", text)
    for name in SECRET_ENVS:
        v = (os.environ.get(name) or "").strip()
        if len(v) >= 8:
            for form in {v, quote(v, safe=""), quote(v)}:
                text = text.replace(form, "***")
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def install() -> None:
    """루트 핸들러 전부에 건다. urllib3 등 다른 모듈의 로그도 루트 핸들러를 거친다."""
    for h in logging.getLogger().handlers:
        if not any(isinstance(f, RedactFilter) for f in h.filters):
            h.addFilter(RedactFilter())
