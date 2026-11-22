from datetime import date
from ulsan_jobs.collectors.board import parse_board


def test_row_must_not_match_skips_reposted_rows():
    html = """<table><tr><th>번호</th><th>제목</th><th>날짜</th></tr>
    <tr><td>2</td><td><a href="view.asp?sn=2">[채용] 본 센터 프로그램 강사 채용</a></td><td>2026-11-21</td></tr>
    <tr><td>1</td><td><a href="view.asp?sn=1">[채용공고] 울산 남구육아종합지원센터 강사 채용</a></td><td>2026-11-21</td></tr>
    </table>"""
    rows = parse_board(html, "https://x.or.kr/board/list.asp", {"row_must_not_match": r"남구\s*육아종합"}, date(2026, 11, 22))
    assert [r.title for r in rows] == ["[채용] 본 센터 프로그램 강사 채용"]
