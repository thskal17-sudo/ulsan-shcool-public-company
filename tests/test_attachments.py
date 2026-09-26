import io
import struct
import zipfile
from datetime import date

from ulsan_jobs.attachments import extract_text, find_attachments, hwp_records_text
from ulsan_jobs.dates import extract_deadline
from ulsan_jobs.detail import page_soup

DETAIL = """
<div class="file">
  <a href="javascript:fn_egov_downFile('FILE_000000000012854','0')">2026년 스포츠 위수탁 모집공고(안)_.hwpx [81789 byte]</a>
  <a href="#LINK" onclick="javascript:fn_egov_downFile('abc%2F','1')">2. 응시원서 및 개인정보 수집·이용동의서.hwpx [57407 byte]</a>
</div>
<a href="/board/down.jsp?code=employ&fname=e1.hwp&rename=중구수영장 시간강사 위촉 공고.hwp">중구수영장 시간강사 위촉 공고.hwp(103k)다운로드</a>
<a href="javascript:void(0);" onclick="previewAjax('https://www.example.go.kr/cmm/fms/FileDown.do?atchFileId=F1&amp;fileSn=0','채용 공고.hwp')">바로보기 채용 공고.hwp</a>
<a href="/file/wa2026.pdf" title="PDF파일 새창열림"></a>
"""


def test_find_attachments_link_styles():
    soup = page_soup(DETAIL.encode())
    found = find_attachments(soup, "https://www.example.or.kr/portal/bbs/view.do?nttId=1")
    urls = [a.url for a in found]
    # 표준 내려받기 함수 → 기본 주소 틀, 일반 링크, 스크립트 안의 전체 주소. 신청서·동의서와 글자 없는 PDF 링크는 제외
    assert urls == [
        "https://www.example.or.kr/cmm/fms/FileDown.do?atchFileId=FILE_000000000012854&fileSn=0",
        "https://www.example.or.kr/board/down.jsp?code=employ&fname=e1.hwp&rename=중구수영장 시간강사 위촉 공고.hwp",
        "https://www.example.go.kr/cmm/fms/FileDown.do?atchFileId=F1&fileSn=0",
    ]
    assert found[0].name == "2026년 스포츠 위수탁 모집공고(안)_.hwpx"


def test_find_attachments_site_template():
    soup = page_soup(DETAIL.encode())
    found = find_attachments(soup, "https://www.example.or.kr/portal/bbs/view.do", "/portal/cmm/fms/FileDown.do?atchFileId={0}&fileSn={1}")
    assert found[0].url == "https://www.example.or.kr/portal/cmm/fms/FileDown.do?atchFileId=FILE_000000000012854&fileSn=0"


NOTICE = "울주군시설관리공단 공고 제2026-45호\n1. 모집분야: 주말수영\n2. 원서접수 : 2026. 9. 21.(월) 09:00 ~ 9. 25.(금) 18:00\n3. 문의: 052-000-0000"


def _hwpx(text: str) -> bytes:
    buf = io.BytesIO()
    paras = "".join(f"<hp:p><hp:run><hp:t>{line}</hp:t></hp:run></hp:p>" for line in text.split("\n"))
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", f'<hs:sec xmlns:hp="p" xmlns:hs="s">{paras}</hs:sec>')
    return buf.getvalue()


def _hwp_record(tag: int, payload: bytes) -> bytes:
    return struct.pack("<I", tag | (len(payload) << 20)) + payload


def test_hwpx_text_and_deadline():
    text = extract_text(_hwpx(NOTICE))
    assert "원서접수 : 2026. 9. 21.(월) 09:00 ~ 9. 25.(금) 18:00" in text
    assert extract_deadline(text, date(2026, 9, 22), anywhere=False) == date(2026, 9, 25)


def test_hwp_records_text_skips_controls():
    # 문단 글자 레코드(67): 앞쪽 8칸짜리 제어 문자(구역 정의 등)는 건너뛰고, 탭(9)은 탭으로
    ctrl = struct.pack("<8H", 2, 0, 0, 0, 0, 0, 0, 2)
    tab = struct.pack("<8H", 9, 0, 0, 0, 0, 0, 0, 9)
    payload = ctrl + "접수기간".encode("utf-16-le") + tab + "9. 25.까지".encode("utf-16-le") + struct.pack("<H", 13)
    data = _hwp_record(66, b"\x00" * 10) + _hwp_record(67, payload)
    assert hwp_records_text(data) == "접수기간\t9. 25.까지\n"


def test_extract_text_rejects_unknown():
    assert extract_text(b"<html>error</html>") is None
    assert extract_text(b"PK\x03\x04broken") is None
