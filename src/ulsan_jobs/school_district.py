"""구·군을 모르는 공고에 구·군을 채운다 ('울산' → '울산 북구').

강사잇다 양식의 지역은 '울산 북구' 꼴이다(gangsaitda.region). 구청·공단 공고는 기관명·제목에 구 이름이 있어 채워지지만,
학교·유치원 공고는 '중산초등학교병설유치원'처럼 이름만 있고, 시설공단 강습 공고는 '문수경기장'처럼 시설 이름만 있어
'울산'으로 남았다 (2026-11-11: 진행 중 29건 중 22건).

1) 시설: 울산시설공단 시설은 주소가 바뀌지 않으니 이름표(VENUES)로 정한다 (공단 누리집 시설별 주소, 2026-11-12 확인).
2) 학교: 나이스(open.neis.go.kr) 학교기본정보에서 울산광역시교육청(H10) 학교 중 이름이 같은 곳의 도로명주소로 정한다.
   인증키 없이 학교 이름 하나를 조회할 수 있다 (부산판과 같은 방식). '○○초병설유치원'은 그 초등학교 주소를 쓴다.
   따로 있는 유치원은 나이스에 없어 같은 이름의 초등학교 주소를 쓴다 ('강동유치원' → 강동초등학교).
   결과는 수집 기록 DB 의 school_districts 표에 남겨 같은 학교를 다시 묻지 않는다. 같은 이름 학교가 여러 구에 있으면
   제목에 구 이름이 보일 때만 고른다. 찾지 못하면 '울산' 그대로 둔다 (틀린 구를 넣지 않는다).
"""
from __future__ import annotations

import logging
import re

from .classify import infer_district
from .models import Posting

log = logging.getLogger(__name__)

DISTRICTS = ("중구", "남구", "동구", "북구", "울주군")
NEIS_URL = "https://open.neis.go.kr/hub/schoolInfo"
NEIS_ULSAN = "H10"  # 울산광역시교육청 시도교육청코드
LOOKUP_MAX = 60  # 한 번 실행에 나이스에 새로 물어볼 학교 수 (나머지는 다음 실행에서)
KINDER_TRIES = 4  # 따로 있는 유치원 하나로 물어볼 초등학교 이름 수

# 시설 이름 → 구·군 (울산시설공단 누리집 시설별 주소, 2026-11-12)
VENUES = [
    ("동천국민체육센터", "중구"),  # 중구 염포로 85-1
    ("동천체육관", "중구"),  # 중구 염포로 85
    ("문수경기장", "남구"),  # 남구 문수로 44 (울산체육공원)
    ("문수체육", "남구"),
    ("문수테니스", "남구"),
    ("문수축구", "남구"),
    ("문수야구", "남구"),
    ("문수수영", "남구"),
    ("울산체육공원", "남구"),
    ("울산대공원", "남구"),  # 남구 대공원로 94
    ("아쿠아시스", "남구"),  # 울산대공원 물놀이장
    ("노동자종합복지회관", "남구"),  # 남구 삼산중로 144
    ("노동복지관", "남구"),
    ("종하이노베이션", "남구"),  # 남구 봉월로38번길 32
    ("종하체육관", "남구"),
    ("어린이테마파크", "동구"),  # 동구 등대로 100 (대왕암공원)
    ("하늘공원", "울주군"),  # 울주군 삼동면 보삼길 550
]

_ADDR_RE = re.compile(r"울산(?:광역시)?\s*([가-힣]+(?:구|군))(?![가-힣])")
_ORG_NOISE = re.compile(r"\((?:공립|사립|국립)\)|\s+")
_ANNEX_RE = re.compile(r"([가-힣]{2,10}?)(?:초등학교|초)\s*병설")  # '중산초병설유치원', '연암초등학교 병설유치원'
_SCHOOL_RE = re.compile(r"[가-힣]{2,12}(?:초등학교|중학교|고등학교)")


def venue_district(text: str) -> str:
    squashed = re.sub(r"\s+", "", text or "")
    return next((d for name, d in VENUES if name in squashed), "")


def school_names(org: str, title: str = "") -> list[str]:
    """나이스에 물어볼 학교 이름 후보 (앞의 것부터). 학교·유치원이 아니면 []."""
    out: list[str] = []
    name = _ORG_NOISE.sub("", org or "")
    if name.endswith("병설유치원"):
        base = name.removesuffix("병설유치원")
        out.append(base + "등학교" if base.endswith("초") else base)
    elif name.endswith("학교") and len(name) > 2:
        out.append(name)
    out += [m.group(1) + "초등학교" for m in _ANNEX_RE.finditer(f"{org} {title}")]
    out += _SCHOOL_RE.findall(title or "")
    if name.endswith("유치원") and "병설" not in name:
        stem = name.removesuffix("유치원").removeprefix("울산")
        if 2 <= len(stem) <= 6:
            out += [stem[:k] + "초등학교" for k in range(len(stem), 1, -1)][:KINDER_TRIES]
    return [n for n in dict.fromkeys(out) if n.endswith("학교") and len(n) > 4]


def pick_district(found: str, text: str) -> str:
    """저장해 둔 구·군에서 하나를 고른다. 여러 구면('남구,북구') 제목·기관명에 그중 하나만 보일 때만 쓴다."""
    parts = [d for d in (found or "").split(",") if d]
    if len(parts) == 1:
        return parts[0]
    hits = [d for d in parts if (d[:-1] if len(d) > 2 else d) in (text or "")]
    return hits[0] if len(hits) == 1 else ""


def district_from_address(address: str) -> str | None:
    """'울산광역시 북구 중산동 1' → '북구'. 울산 구·군이 아니면 None."""
    m = _ADDR_RE.search(address or "")
    return m.group(1) if m and m.group(1) in DISTRICTS else None


def neis_addresses(http, school: str) -> list[str]:
    """나이스에서 울산 학교 중 이름이 같은 곳의 도로명주소들. 접속 실패는 예외로 올린다."""
    resp = http.get(NEIS_URL, params={"Type": "json", "ATPT_OFCDC_SC_CODE": NEIS_ULSAN, "SCHUL_NM": school})
    data = resp.json()
    info = data.get("schoolInfo") or []
    rows = info[1].get("row", []) if len(info) > 1 else []
    return [r.get("ORG_RDNMA", "") for r in rows if r.get("SCHUL_NM") == school]


def fill_districts(postings: list[Posting], store, http) -> int:
    """구·군을 모르는 공고에 구·군을 채운다 (p.district, 메모리만). 채운 공고 수."""
    filled = lookups = 0
    neis_down = False
    for p in postings:
        text = f"{p.org_name} {p.title}"
        if p.district in DISTRICTS or infer_district(text):
            continue
        venue = venue_district(text)
        if venue:
            p.district = venue
            filled += 1
            continue
        for school in school_names(p.org_name, p.title):
            found = store.school_district(school)
            if found is None:
                if neis_down or lookups >= LOOKUP_MAX:
                    break
                lookups += 1
                try:
                    districts = {district_from_address(a) for a in neis_addresses(http, school)}
                except Exception as exc:  # noqa: BLE001 — 나이스가 안 되면 이번 실행에서는 더 묻지 않고 '울산' 그대로
                    log.warning("나이스 학교 주소 조회 실패 (%s): %s", school, exc)
                    neis_down = True
                    break
                # 같은 이름이 여러 구에 있으면 모두 적어 둔다 (pick_district 가 제목으로 고른다). 울산 밖 주소가 섞이면 쓰지 않는다
                found = "" if None in districts else ",".join(sorted(districts))
                store.set_school_district(school, found)
            district = pick_district(found, text)
            if district:
                p.district = district
                filled += 1
                break
    return filled
