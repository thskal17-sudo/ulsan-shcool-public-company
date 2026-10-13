"""공공데이터포털(apis.data.go.kr) Open API 공통: 인증키 읽기, 오류 문구에서 키 가리기, yyyymmdd 날짜.

인증키는 공공데이터포털 개인 인증키(일반 인증키) 하나로 활용 승인받은 API 를 모두 쓴다.
"""
from __future__ import annotations

import os
import re
from datetime import date
from urllib.parse import quote, unquote

import requests

from ..config import Source
from ..http import Http
from .base import NotConfigured


def service_key(source: Source) -> str:
    """sources.yaml 의 auth_env(기본 DATA_GO_KR_KEY) 환경변수. 없으면 '설정필요'."""
    env = source.auth_env or "DATA_GO_KR_KEY"
    key = os.environ.get(env, "").strip()
    if not key:
        raise NotConfigured(f"{env} 미설정 (공공데이터포털 인증키 필요)")
    return unquote(key) if "%" in key else key  # 포털의 Encoding 키를 넣은 경우


def get(http: Http, url: str, params: dict, key: str) -> requests.Response:
    """serviceKey 를 붙여 GET. 실패하면 오류 문구에서 키를 지우고 다시 던진다."""
    try:
        return http.get(url, params={"serviceKey": key, **params})
    except requests.RequestException as exc:
        # requests 의 오류 문구에는 요청 주소(serviceKey 포함)가 들어간다. 공개 저장소의
        # Actions 로그에 '수집 실패 …: <오류>'로 찍히므로 키를 가린 뒤 다시 던진다
        scrub_secret(exc, key)
        raise


def ymd(text: str | None) -> date | None:
    """'20261018' → date. 빈 값·'1900…'·잘못된 날짜는 None."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", (text or "").strip())
    if not m or m.group(1) < "2000":
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_KEY_QUERY = re.compile(r"(?i)(servicekey=)[^&\s'\"]+")


def scrub_secret(exc: BaseException, key: str) -> None:
    """예외(와 안에 든 urllib3 예외)의 메시지에서 인증키를 지운다. 종류는 그대로 둔다."""
    forms = {f for f in (key, quote(key, safe=""), quote(key)) if f}

    def clean(value):
        if isinstance(value, str):
            for f in forms:
                value = value.replace(f, "***")
            return _KEY_QUERY.sub(r"\1***", value)
        if isinstance(value, BaseException):
            scrub_secret(value, key)
        return value

    exc.args = tuple(clean(a) for a in exc.args)
    for attr in ("url",):
        if isinstance(getattr(exc, attr, None), str):
            setattr(exc, attr, clean(getattr(exc, attr)))
