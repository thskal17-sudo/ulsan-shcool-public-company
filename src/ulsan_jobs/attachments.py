"""첨부파일(공고문)에서 글자 꺼내기: 마감일이 첨부 HWP/HWPX/PDF 에만 있는 공고용.

공단·수련관·구청 공고는 본문에 '붙임 공고문 참조' 만 있고 접수기간은 첨부 공고문에 있는 경우가 많다.
상세 페이지에서 공고문 첨부를 찾아 내려받고, 파일 앞머리(시그니처)로 형식을 판단해 글자를 꺼낸다.
    HWPX  zip 안의 Contents/section*.xml
    HWP   OLE 복합문서의 BodyText/Section* (zlib 압축된 레코드 중 문단 글자 레코드)
    PDF   pypdf 로 앞쪽 몇 쪽
배포용(암호화) HWP 처럼 읽을 수 없는 파일은 조용히 건너뛴다 (마감일은 '원문확인' 으로 남음).

첨부 링크 모양 (sources.yaml 의 attachment_template 로 사이트별 주소 틀 지정 가능)
    <a href="/cmm/fms/FileDown.do?atchFileId=…&fileSn=0">공고문.hwp</a>          그대로 사용
    <a href="javascript:fn_egov_downFile('FILE_…','0')">공고문.hwpx</a>          틀에 인자를 넣음
    onclick="previewAjax('https://…/FileDown.do?…','공고문.hwp')"               안의 주소 사용
"""
from __future__ import annotations

import html
import io
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_EXT = re.compile(r"\.(hwpx|hwp|pdf)(?![a-z])", re.I)
_SKIP = re.compile(r"서식|양식|신청서|원서|동의서|계획서|이력서|자기소개서|확인서|서약서|개인정보|공통서류|제출서류")
_JS_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")
_URL_IN_JS = re.compile(r"""https?://[^'"\s)]+""")
DEFAULT_TEMPLATES = {
    # 전자정부 표준프레임워크 기본 내려받기 함수
    "fn_egov_downFile": "/cmm/fms/FileDown.do?atchFileId={0}&fileSn={1}",
}


@dataclass
class Attachment:
    name: str
    url: str


def _js_args(code: str) -> tuple[str, list[str]] | None:
    m = _JS_CALL.search(code or "")
    if not m:
        return None
    return m.group(1), [a.strip().strip("'\"") for a in m.group(2).split(",") if a.strip()]


def _resolve(a, page_url: str, template: str | None) -> str | None:
    href = (a.get("href") or "").strip()
    onclick = a.get("onclick") or ""
    if href and not href.lower().startswith(("javascript", "#")):
        return urljoin(page_url, href)
    for code in (onclick, href):
        m = _URL_IN_JS.search(code)
        if m:
            return html.unescape(m.group(0))
    call = _js_args(onclick) or _js_args(href)
    if call:
        name, args = call
        tpl = template or DEFAULT_TEMPLATES.get(name)
        if tpl:
            try:
                return urljoin(page_url, tpl.format(*args))
            except IndexError:
                return None
    return None


def find_attachments(soup: BeautifulSoup, page_url: str, template: str | None = None) -> list[Attachment]:
    """상세 페이지의 첨부 중 공고문으로 보이는 것 (공고문 먼저, 서식·신청서 제외).

    template: 첨부 링크가 스크립트 호출일 때 쓸 주소 틀 ({0}, {1} 에 호출 인자). 없으면 표준 함수만 처리.
    """
    found: list[Attachment] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        text = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        label = text or (a.get("title") or "").strip()
        # 파일 이름이 링크 글자에 있거나, 글자가 있는 링크의 주소가 파일일 때만 (글자 없는 PDF 안내 링크 제외)
        if not _EXT.search(label) and not (text and _EXT.search(a.get("href") or "")):
            continue
        name = re.split(r"\s*[\[(]\s*\d+(?:\.\d+)?\s*(?:byte|kb|k|mb)\b", label, flags=re.I)[0].strip() or label
        if _SKIP.search(name):
            continue
        url = _resolve(a, page_url, template)
        if url and url not in seen:
            seen.add(url)
            found.append(Attachment(name, url))
    found.sort(key=lambda att: 0 if "공고" in att.name else 1)
    return found


# ─────────────────────────────── 글자 꺼내기

def extract_text(content: bytes, max_chars: int = 200_000) -> str | None:
    """파일 앞머리로 형식을 판단해 글자를 꺼낸다. 모르는 형식·읽기 실패면 None."""
    try:
        if content.startswith(b"PK"):
            text = _hwpx_text(content)
        elif content.startswith(b"\xd0\xcf\x11\xe0"):
            text = _hwp_text(content)
        elif content.startswith(b"%PDF"):
            text = _pdf_text(content)
        else:
            return None
    except Exception:  # noqa: BLE001 - 깨진 파일·배포용 문서 등은 건너뛴다
        return None
    return text[:max_chars] if text else None


def _hwpx_text(content: bytes) -> str:
    parts = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = sorted(n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        for name in names:
            xml = zf.read(name).decode("utf-8", "ignore")
            xml = re.sub(r"</\w+:(p|tc|tr)>", "\n", xml)
            xml = re.sub(r"<\w+:tab\b[^>]*/>", "\t", xml)
            parts.append(html.unescape(re.sub(r"<[^>]+>", "", xml)))
    return "\n".join(parts)


HWPTAG_PARA_TEXT = 67
# 1칸짜리 제어 문자. 나머지 제어 문자(0~31)는 8칸(16바이트)을 차지한다
_CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _hwp_text(content: bytes) -> str:
    import olefile

    with olefile.OleFileIO(io.BytesIO(content)) as ole:
        header = ole.openstream("FileHeader").read()
        props = struct.unpack("<I", header[36:40])[0]
        if props & 0b110:  # 암호 설정(2) 또는 배포용 문서(4): 본문이 암호화돼 있음
            return ""
        compressed = bool(props & 1)
        sections = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
            key=lambda e: int(e[1][7:] or 0),
        )
        parts = []
        for entry in sections:
            data = ole.openstream(entry).read()
            if compressed:
                data = zlib.decompress(data, -15)
            parts.append(hwp_records_text(data))
    return "\n".join(parts)


def hwp_records_text(data: bytes) -> str:
    """HWP 본문 레코드 묶음에서 문단 글자만 모은다."""
    out = []
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack("<I", data[i: i + 4])[0]
        i += 4
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack("<I", data[i: i + 4])[0]
            i += 4
        if tag == HWPTAG_PARA_TEXT:
            out.append(_para_text(data[i: i + size]))
        i += size
    return "\n".join(out)


def _para_text(payload: bytes) -> str:
    units = struct.unpack(f"<{len(payload) // 2}H", payload[: len(payload) // 2 * 2])
    keep = []
    j = 0
    while j < len(units):
        c = units[j]
        if c >= 32:
            keep.append(c)
            j += 1
        elif c in _CHAR_CONTROLS:
            if c in (10, 13):
                keep.append(10)
            j += 1
        else:
            if c == 9:
                keep.append(9)
            j += 8
    return struct.pack(f"<{len(keep)}H", *keep).decode("utf-16-le", "ignore")


def _pdf_text(content: bytes, max_pages: int = 5) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
