from datetime import date

from ulsan_jobs.collectors.board import _pick_key, parse_board

TODAY = date(2026, 9, 27)


def test_pick_key_prefers_numeric_argument():
    assert _pick_key(["employ", "207", ""]) == "207"
    assert _pick_key(["4726", "BBSMSTR_000000000061"]) == "4726"
    assert _pick_key(["subFormEMPLOY_0000000003134"]) == "subFormEMPLOY_0000000003134"
    assert _pick_key([]) == ""


EMINWON_LIKE = """
<table class="cont_table"><thead>
<tr><td colspan="7">전체게시물:2개 페이지:1/1</td></tr>
<tr><th>번호</th><th>고시공고번호</th><th>제목</th><th>담당부서</th><th>등록일</th><th>게재기간</th><th>조회수</th></tr>
</thead><tbody>
<tr><td>2</td><td>북구 공고 제2026-1</td><td><a href="javascript:searchDetail('31001')">2026년 생활체육지도자 채용 공고</a></td>
<td>체육진흥과</td><td>2026-09-25</td><td>2026-09-25 ~ 2026-10-02</td><td>10</td></tr>
<tr><td>1</td><td>북구 공고 제2026-2</td><td><a href="javascript:searchDetail('31000')">기간제근로자 채용 공고</a></td>
<td>총무과</td><td>2026-09-24</td><td>2026-09-24 ~ 2026-09-30</td><td>5</td></tr>
</tbody></table>
"""


def test_summary_row_before_header_and_js_template():
    opts = {"link_template": "https://eminwon.example/OfrAction.do?method=selectOfrNotAncmt&not_ancmt_mgt_no={0}"}
    rows = parse_board(EMINWON_LIKE, "https://eminwon.example/list", opts, TODAY)
    assert [r.key for r in rows] == ["31001", "31000"]
    r = rows[0]
    assert r.url.endswith("not_ancmt_mgt_no=31001")
    assert r.org == "체육진흥과"
    assert r.posted == date(2026, 9, 25)
    assert r.deadline == date(2026, 10, 2)  # '게재기간' 끝나는 날
