"""강사 공고 판별과 분야·기관유형 분류."""
from __future__ import annotations

import re

from .config import Rules


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _has_any(text: str, terms: list[str]) -> str | None:
    t = _squash(text)
    for term in terms:
        if _squash(term) and _squash(term) in t:
            return term
    return None


def judge(title: str, rules: Rules, keyword_filter: bool) -> str | None:
    """강사 공고면 상태('모집중' 또는 '결과공고'), 아니면 None.

    공백을 무시하고 비교한다 ('회원 모집' == '회원모집').
    """
    if _has_any(title, rules.exclude):
        return None
    if keyword_filter and not _has_any(title, rules.include):
        return None
    if _has_any(title, rules.result_notice):
        return "결과공고" if rules.keep_result_notices else None
    return "모집중"


def categorize(title: str, org_name: str, rules: Rules) -> str:
    text = f"{title} {org_name}"
    for category, terms in rules.categories.items():
        if _has_any(text, terms):
            return category
    return "기타"


_ORG_TYPES = [
    ("교육청·학교", r"학교|교육청|교육지원청|유치원|교육원"),
    ("공단(체육시설)", r"공단|체육회|체육센터|스포츠센터|수영장"),
    ("여성·가족", r"여성|새일|가족"),
    ("청소년", r"청소년"),
    ("평생교육·도서관", r"도서관|평생|문화원|문화의집|박물관"),
    ("지자체", r"구청|군청|시청|행정복지센터|주민센터|보건소|울산광역시"),
]


def infer_org_type(org_name: str, default: str = "") -> str:
    for org_type, pattern in _ORG_TYPES:
        if re.search(pattern, org_name or ""):
            return org_type
    return default


_DISTRICTS = ["중구", "남구", "동구", "북구", "울주군"]


def infer_district(text: str, default: str = "") -> str:
    for d in _DISTRICTS:
        if d in (text or ""):
            return d
    return default
