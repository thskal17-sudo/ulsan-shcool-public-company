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


def test_link_base_for_relative_links():
    html = """<table><tr><th>번호</th><th>제목</th><th>등록일</th></tr>
    <tr><td>1</td><td><a href="emwp/gov/ofr/OfrAction.do?not_ancmt_mgt_no=54580&method=selectOfrNotAncmt">강사 채용</a></td>
    <td>2026-09-23</td></tr></table>"""
    page = "https://eminwon.example/emwp/gov/ofr/OfrAction.do?method=selectListOfrNotAncmt"
    opts = {"link_base": "https://eminwon.example/", "key_param": "not_ancmt_mgt_no"}
    rows = parse_board(html, page, opts, TODAY)
    assert rows[0].url == "https://eminwon.example/emwp/gov/ofr/OfrAction.do?not_ancmt_mgt_no=54580&method=selectOfrNotAncmt"
    assert rows[0].key == "54580"


def test_submit_button_titles():
    # 울주군시설관리공단: 제목이 <input type=submit> 이고 글마다 작은 POST 폼이 있다
    html = """<table class="board_list"><thead><tr><th>번호</th><th>제목</th><th>등록자</th><th>등록일</th></tr></thead>
    <tbody><tr><td class="num"><img alt="notice" src="x.png"></td><td class="subject">
    <form name="subForm" method="post" action="/portal/bbs/selectArticleDetail.do;jsessionid=ABC">
    <input name="nttId" type="hidden" value="8979"><input name="bbsId" type="hidden" value="BBSMSTR_000000000032">
    <input name="pageIndex" type="hidden" value="1"/>
    <span class="link"><input type="submit" value="[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"></span>
    </form></td><td class="writer">관리자</td><td class="date">2026-09-18</td></tr></tbody></table>"""
    rows = parse_board(html, "https://www.uljusiseol.or.kr/portal/bbs/selectArticleList.do?bbsId=X", {"key_param": "nttId"}, TODAY)
    assert len(rows) == 1
    r = rows[0]
    assert r.title == "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"
    assert r.key == "8979"
    assert r.url == ("https://www.uljusiseol.or.kr/portal/bbs/selectArticleDetail.do"
                     "?nttId=8979&bbsId=BBSMSTR_000000000032&pageIndex=1")
    assert r.posted == date(2026, 9, 18)
    assert r.org == ""


def test_template_key_param_with_code_argument():
    # 나라일터: fn_apmView('020', '303834') - 첫 인자는 구분 코드, 글 번호는 두 번째
    html = """<table><tr><th>번호</th><th>공고명</th><th>기관명</th><th>공고게시일</th></tr>
    <tr><td>1</td><td><a href="javascript:fn_apmView('020', '303834')">강사 채용 공고 A</a></td><td>울산광역시</td><td>2026-09-18</td></tr>
    <tr><td>2</td><td><a href="javascript:fn_apmView('020', '303775')">강사 채용 공고 B</a></td><td>울산광역시 동구</td><td>2026-09-16</td></tr>
    </table>"""
    opts = {
        "link_template": "https://www.gojobs.go.kr/apmView.do?empmnsn={1}&searchJobsecode={0}",
        "key_param": "empmnsn",
    }
    rows = parse_board(html.encode(), "https://www.gojobs.go.kr/apmList.do", opts, date(2026, 9, 27))
    assert [r.key for r in rows] == ["303834", "303775"]
    assert rows[0].url == "https://www.gojobs.go.kr/apmView.do?empmnsn=303834&searchJobsecode=020"
    # key_param 이 없어도 긴 숫자 인자를 글 번호로
    rows = parse_board(html.encode(), "https://www.gojobs.go.kr/apmList.do", {"link_template": opts["link_template"]},
                       date(2026, 9, 27))
    assert [r.key for r in rows] == ["303834", "303775"]


def test_template_wins_over_plain_href_when_onclick_has_args():
    # 남구 평생학습: href 는 번호 없는 view.do, 실제 글 번호는 onclick 에만 있다
    html = """<table><tr><th>번호</th><th>제목</th><th>작성일</th></tr>
    <tr><td>1</td><td><a href="/edu/board/eduBoard/view.do" onclick="goBoardArticle('534458'); return false;">배달강사 등록 안내</a></td><td>2026.09.17</td></tr>
    </table>"""
    opts = {"link_template": "https://www.ulsannamgu.go.kr/edu/board/eduBoard/view.do?nttId={0}"}
    [row] = parse_board(html.encode(), "https://www.ulsannamgu.go.kr/edu/board/eduBoard/list.do", opts, date(2026, 9, 27))
    assert row.url == "https://www.ulsannamgu.go.kr/edu/board/eduBoard/view.do?nttId=534458"
    assert row.key == "534458"


def test_empty_anchor_title_in_cell_and_row_filter():
    # 잡알리오: <a href="/recruitview.do?idx=1"/>제목</a> - 링크는 비어 있고 제목은 칸에
    html = """<table><tr><th></th><th>번호</th><th>채용제목</th><th>기관명</th><th>근무지</th><th>등록일</th><th>마감일</th></tr>
    <tr><td><input type="checkbox"/></td><td>2</td><td class="left"><a href="/recruitview.do?idx=305344" target="_blank"/>체육 강사 채용 공고</a></td>
        <td>울산항만공사</td><td> 울산 </td><td>2026.09.24</td><td> 26.10.08</td></tr>
    <tr><td><input type="checkbox"/></td><td>1</td><td class="left"><a href="/recruitview.do?idx=305278" target="_blank"/>수영 강사 채용</a></td>
        <td>국립공원공단</td><td> 충북 </td><td>2026.09.23</td><td> 26.10.08</td></tr>
    </table>"""
    opts = {"key_param": "idx", "row_must_contain": "울산"}
    rows = parse_board(html.encode(), "https://job.alio.go.kr/recruit.do", opts, date(2026, 9, 27))
    assert [(r.title, r.key, r.org, r.posted, r.deadline) for r in rows] == [
        ("체육 강사 채용 공고", "305344", "울산항만공사", date(2026, 9, 24), date(2026, 10, 8)),
    ]
    assert rows[0].url == "https://job.alio.go.kr/recruitview.do?idx=305344"
