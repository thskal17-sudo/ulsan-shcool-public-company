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


def judge(
    title: str, rules: Rules, keyword_filter: bool, label: str = "", *, keep_results: bool | None = None
) -> str | None:
    """강사 공고면 상태('모집중' 또는 '결과공고'), 아니면 None.

    포함 키워드는 제목과 게시판 구분값(label, 예: '방과후강사(관련)')에서 찾고,
    제외·결과 키워드는 제목에서만 찾는다. 공백은 무시한다 ('회원 모집' == '회원모집').
    keep_results 를 주면 rules.keep_result_notices 대신 그 값으로 결과공고를 남길지 정한다.
    """
    keep = rules.keep_result_notices if keep_results is None else keep_results
    if _has_any(title, rules.exclude):
        return None
    if keyword_filter and not _has_any(f"{title} {label}", rules.include):
        return None
    if _has_any(title, rules.result_notice):
        return "결과공고" if keep else None
    return "모집중"


def categorize(title: str, org_name: str, rules: Rules) -> str:
    """위에서부터 먼저 맞는 분야. '!' 로 시작하는 단어가 있으면 그 분야는 건너뛴다."""
    text = f"{title} {org_name}"
    for category, terms in rules.categories.items():
        negatives = [t[1:] for t in terms if t.startswith("!")]
        positives = [t for t in terms if not t.startswith("!")]
        if _has_any(text, negatives):
            continue
        if _has_any(text, positives):
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
