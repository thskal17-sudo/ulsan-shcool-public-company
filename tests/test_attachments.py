import io
import struct
import zipfile
from datetime import date

from ulsan_jobs.attachments import extract_text, find_attachments, hwp_records_text
from ulsan_jobs.dates import extract_deadline
from ulsan_jobs.detail import page_soup
from ulsan_jobs.jobinfo import extract_info

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



def test_hwpx_text_drops_field_parameters():
    # 동천국민체육센터 공고문: 이메일 하이퍼링크의 설정값이 본문 글자에 섞여
    # 'HWPHYPERLINK_TYPE_HWP…shoot777@uic.or.kr' 가 이메일로 들어갔다
    link = (
        '<hp:ctrl><hp:fieldBegin type="HYPERLINK"><hp:parameters cnt="5">'
        '<hp:integerParam name="Prop">0</hp:integerParam><hp:stringParam name="Command">;0;0;0;</hp:stringParam>'
        '<hp:stringParam name="Category">HWPHYPERLINK_TYPE_HWP</hp:stringParam>'
        '<hp:stringParam name="TargetType">HWPHYPERLINK_TARGET_BOOKMARK</hp:stringParam>'
        '<hp:stringParam name="DocOpenType">HWPHYPERLINK_JUMP_CURRENTTAB</hp:stringParam>'
        "</hp:parameters></hp:fieldBegin></hp:ctrl>"
    )
    empty = '<hp:ctrl><hp:fieldBegin type="CLICKHERE"><hp:parameters cnt="0"/></hp:fieldBegin></hp:ctrl>'
    body = (
        "<hp:p><hp:run><hp:t>라. 접수방법 : 이메일(e-mail) 접수</hp:t></hp:run></hp:p>"
        f"<hp:p><hp:run>{empty}<hp:t>- 이메일(e-mail) 주소 : </hp:t>{link}<hp:t>shoot777@uic.or.kr</hp:t>"
        "<hp:ctrl><hp:fieldEnd/></hp:ctrl></hp:run></hp:p>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Contents/section0.xml", f'<hs:sec xmlns:hp="p" xmlns:hs="s">{body}</hs:sec>')
    text = extract_text(buf.getvalue())
    assert "- 이메일(e-mail) 주소 : shoot777@uic.or.kr" in text  # 빈 설정값(<…/>) 뒤 글자도 그대로
    assert "HWPHYPERLINK" not in text
    assert extract_info(text)["email"] == "shoot777@uic.or.kr"


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
