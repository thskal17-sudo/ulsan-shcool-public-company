from datetime import date
from urllib.parse import parse_qs, urlsplit

from ulsan_jobs.collectors.afschool import parse_afschool
from ulsan_jobs.collectors.board import parse_board, set_query, stable_key
from ulsan_jobs.collectors.work24_api import DEFAULT_ORG_EXCLUDE, DEFAULT_ORG_INCLUDE, parse_work24_xml

TODAY = date(2026, 9, 26)


def test_use_cms_board(fixture_bytes):
    base = "https://use.go.kr/job/user/bbs/BD_selectBbsList.do?q_bbsSn=2249"
    rows = parse_board(fixture_bytes("use_bbs_2249.html"), base, {"key_param": "q_bbsDocNo"}, TODAY)
    assert len(rows) == 4
    r = rows[2]
    assert r.title == "2026학년도 척과초 초등방과후 프로그램 개인위탁 독서논술 강사 모집"
    assert r.url == "https://use.go.kr/job/user/bbs/BD_selectBbs.do?q_bbsSn=2249&q_bbsDocNo=20260923132020119"
    assert r.key == "20260923132020119"
    assert r.org == "척과초등학교"
    assert r.label == "방과후강사(관련)"
    assert r.deadline == date(2026, 10, 1)
    assert r.posted == date(2026, 9, 23)  # 게시일 열이 없으면 접수 시작일
    assert rows[0].deadline == date(2026, 9, 29)
    assert rows[3].deadline == date(2026, 9, 25)
    assert all(row.detail_ok for row in rows)


def test_use_jobpost_board(fixture_bytes):
    base = "https://use.go.kr/job/user/jobpost/BD_selectJobPostList.do?q_jobOcptNm=강사"
    rows = parse_board(fixture_bytes("use_jobpost.html"), base, {"key_param": "q_jobPostSn"}, TODAY)
    assert [r.key for r in rows] == ["2727", "2726"]
    assert rows[0].org == "강동고등학교"
    assert rows[0].district == "북구"
    assert rows[0].label == "강사"
    assert rows[0].posted == date(2026, 9, 22)
    assert rows[0].deadline == date(2026, 9, 29)


def test_form_submit_board(fixture_bytes):
    # 울산시설공단: 제목 클릭 시 글마다 있는 POST 폼을 제출 → action + hidden 값으로 GET 주소를 만든다
    base = "https://www.uic.or.kr/notify/noti06.do"
    rows = parse_board(fixture_bytes("uic_noti06.html"), base, {}, TODAY)
    assert len(rows) == 3
    r = rows[0]
    assert r.title == "2026년 하반기 울산대공원 수영장 교육강사(프리랜서) 모집 공고"
    assert r.key == "subFormEMPLOY_0000000003140"
    parts = urlsplit(r.url)
    assert parts.path == "/uimc/notify/noti06/selectEmploymentArticle.do"
    query = parse_qs(parts.query)
    assert query["employmentId"] == ["EMPLOY_0000000003140"]
    assert "_csrf" not in query
    assert r.org == ""  # '관리자' 는 기관명으로 쓰지 않음
    assert r.posted == date(2026, 9, 22)
    assert r.detail_ok


def test_page_url_and_stable_key():
    assert set_query("https://x/list.do?q_bbsSn=2249", q_currPage=2) == "https://x/list.do?q_bbsSn=2249&q_currPage=2"
    a = stable_key("https://x/view.do;jsessionid=ABC?pageIndex=2&id=7")
    b = stable_key("https://x/view.do?id=7&pageIndex=3")
    assert a == b == "https://x/view.do?id=7"


def test_afschool(fixture_bytes):
    items = parse_afschool(fixture_bytes("afschool_list.html"), "https://afschool.use.go.kr/usPrivateApply", TODAY)
    assert len(items) == 3
    first = items[0]
    assert first["title"] == "2026학년도 초등방과후 프로그램 개인위탁(음악줄넘기) 외부강사 모집공고"
    assert first["state"] == "접수중"
    assert first["school"] == "중앙초등학교"
    assert first["posted"] == date(2026, 9, 25)
    assert first["deadline"] == date(2026, 10, 2)
    assert first["url"] == "https://afschool.use.go.kr/usPrivateApply/468"
    assert items[1]["title"].startswith("(6차 공고)")
    assert items[2]["school"] == "울산마이스터고등학교"


def test_work24_xml(fixture_bytes):
    import re

    items, total = parse_work24_xml(fixture_bytes("work24_list.xml"))
    assert total == 3
    assert items[0]["company"] == "울산광역시남구도시관리공단"
    include, exclude = re.compile(DEFAULT_ORG_INCLUDE), re.compile(DEFAULT_ORG_EXCLUDE)
    public = [i["company"] for i in items if include.search(i["company"]) and not exclude.search(i["company"])]
    assert public == ["울산광역시남구도시관리공단", "울산동부종합사회복지관"]


def test_json_board_items():
    from ulsan_jobs.collectors.json_board import parse_json_items

    payload = {
        "resultList": [
            {"bbsKey": "3e2f-01", "title": "2026년 하반기 요가 강사 모집 공고", "regdate": "2026-09-20", "isTop": "N"},
            {"bbsKey": "3e2f-02", "subject": "<b>테마특강</b> 수강생 모집 &amp; 안내", "regdate": "2026-09-19"},
            {"bbsKey": "", "title": "키 없는 글"},
        ]
    }
    opts = {"link_template": "http://www.w1.or.kr/womenhall/bbs/selectBoardDetailView.do?classId=NOTICE&bbsKey={key}"}
    items = parse_json_items(payload, opts, TODAY)
    assert [i["key"] for i in items] == ["3e2f-01", "3e2f-02"]
    assert items[0]["url"].endswith("bbsKey=3e2f-01")
    assert items[0]["posted"] == date(2026, 9, 20)
    assert items[1]["title"] == "테마특강 수강생 모집 & 안내"


def test_json_board_spring_page_items():
    # 울산문화관광재단 /api/notices: {content: [...]} 에 글 번호 id, 작성일 registrationDatetime
    from ulsan_jobs.collectors.json_board import parse_json_items

    payload = {
        "content": [
            {"id": "202411272958", "title": "제7차 기간제 근로자 채용 공고", "content": "<p>본문</p>",
             "state": "진행중", "registrationDatetime": "2026-10-06 09:05:49"},
        ],
        "totalElements": 1,
    }
    opts = {
        "items_key": "content",
        "key_field": "id",
        "date_fields": ["registrationDatetime"],
        "link_template": "https://www.uctf.or.kr/board/employment/view/{key}",
    }
    items = parse_json_items(payload, opts, TODAY)
    assert items[0]["key"] == "202411272958"
    assert items[0]["url"] == "https://www.uctf.or.kr/board/employment/view/202411272958"
    assert items[0]["posted"] == date(2026, 10, 6)


def test_ujcmc_style_template(fixture_bytes):
    html = """<table><tr><th>번호</th><th>제목</th><th>작성자</th><th>등록일</th></tr>
    <tr><td>528</td><td><a href="javascript:boardView('employ','528','');">중구수영장 시간강사(프리랜서) 모집 공고</a></td>
    <td>관리자</td><td>2026-09-10</td></tr></table>"""
    opts = {"link_template": "https://www.ujcmc.or.kr/bbs/{0}/boardView.do?n={1}&p=view"}
    rows = parse_board(html, "https://www.ujcmc.or.kr/bbs/employ/boardList.do", opts, TODAY)
    assert rows[0].url == "https://www.ujcmc.or.kr/bbs/employ/boardView.do?n=528&p=view"
    assert rows[0].key == "528"


class _Resp:
    def __init__(self, url, html):
        self.url, self.content = url, html.encode()


class _FormHttp:
    def __init__(self):
        self.posts = []

    def get(self, url, **kw):
        return _Resp(url, '<form id="frm" action=""><input type="hidden" name="_csrf" value="tok"/>'
                          '<input type="hidden" name="pageNo" value="1"/></form>')

    def post(self, url, data=None, **kw):
        self.posts.append((url, dict(data)))
        rows = "".join(
            f'<tr><td>{n}</td><td><a href="/recruitview.do?idx={n}">울산 강사 모집 {n}</a></td><td>2026.09.2{n}</td></tr>'
            for n in (1, 2)
        )
        return _Resp(url, f"<table><tr><th>번호</th><th>채용제목</th><th>등록일</th></tr>{rows}</table>")


def test_form_board_posts_search_form_with_token():
    from ulsan_jobs.collectors.board import FormBoardCollector
    from ulsan_jobs.config import Source

    src = Source("alio", "잡알리오", "form_board", url="https://job.example.kr/recruit.do", pages=1,
                 options={"form_selector": "form#frm", "data": {"location": "R3016"}, "page_param": "pageNo",
                          "key_param": "idx"})
    http = _FormHttp()
    items = FormBoardCollector(src, http, date(2026, 9, 27)).collect()
    assert http.posts == [("https://job.example.kr/recruit.do", {"_csrf": "tok", "pageNo": "1", "location": "R3016"})]
    assert [(p.title, p.post_key, p.url) for p in items] == [
        ("울산 강사 모집 1", "1", "https://job.example.kr/recruitview.do?idx=1"),
        ("울산 강사 모집 2", "2", "https://job.example.kr/recruitview.do?idx=2"),
    ]


def test_work24_search_rows(fixture_bytes):
    # 고용24 채용정보 상세검색 화면 (srcKeyword=방과후, region=31000) 실제 결과 표에서 3줄
    from ulsan_jobs.collectors.work24_web import parse_work24_search

    items = parse_work24_search(fixture_bytes("work24_search_afterschool.html"))
    assert [i["company"] for i in items] == ["윤슬주식회사", "주식회사처용스쿨", "아이숲어린이집"]
    school = items[1]
    assert school["title"] == "방과후 컴퓨터 강사 모집 [중부새일센터채용대행]"
    assert school["key"] == "KF10202608140001"
    assert school["url"].startswith("https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=KF10202608140001")
    assert (school["site"], school["closing"], school["registered"]) == ("울산광역시 동구 꽃바위6길", "2026-10-13", "2026-08-14")
    # 검색어 강조(<strong>방과</strong><strong>후</strong>)가 있어도 전체 제목은 체크박스 값에서 읽는다
    assert items[0]["title"] == "발달장애인주간활동서비스 / 청소년발달장애학생방과후활동서비스"


def test_work24_web_collector_filters_and_dedups(fixture_bytes):
    from ulsan_jobs.collectors.work24_web import Work24WebCollector
    from ulsan_jobs.config import Source

    class _Resp:
        url = "https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do"
        content = fixture_bytes("work24_search_afterschool.html")

    class _Http:
        def __init__(self):
            self.keywords = []

        def get(self, url, params=None):
            self.keywords.append(params["srcKeyword"])
            return _Resp()

    src = Source(
        id="work24_web_afterschool", name="고용24 검색", org_type="방과후 업체(민간)", district="울산전체",
        collector="work24_web",
        options={"keywords": ["방과후", "늘봄"], "company_exclude": "학원|요양", "title_exclude": "발달장애|활동서비스"},
    )
    http = _Http()
    postings = Work24WebCollector(src, http, TODAY).collect()
    assert http.keywords == ["방과후", "늘봄"]
    # 두 검색어 결과에 같은 공고가 나와도 한 번만, 발달장애인 활동서비스 공고는 뺀다
    assert [p.org_name for p in postings] == ["주식회사처용스쿨", "아이숲어린이집"]
    school = postings[0]
    assert school.district == "동구"
    assert (school.posted_date, school.deadline) == (date(2026, 8, 14), date(2026, 10, 13))
    assert school.org_type == "방과후 업체(민간)"
    assert school.detail_url == school.url
