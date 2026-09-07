"""적대적 리뷰(2026-09-07, 에이전트 48개)가 잡은 결함의 회귀.

🔴 여기 있는 것은 **전부 내가 그날 직접 넣은 결함**이다. 단위 테스트 107건을 통과하고
   라이브 왕복까지 정상이던 코드에서 독립 리뷰가 33건을 찾아냈다.
   → '테스트가 통과하니 맞다'는 근거가 되지 못한다는 기록으로 남긴다.
"""
import pytest

from na_mcp import config as C
from na_mcp.client import NaClient, NaError
from na_mcp.models import clean_html, record_from_items


# ── [5][10] clean_html 이 데이터의 꺾쇠를 지웠다 (data corruption) ────────────
# 이 카탈로그의 표제에는 꺾쇠가 실제로 쓰인다: `<표 124> 심폐소생술 교육 경험률`.
# 초판 `<[^>]+>` 는 그것을 통째로 지웠고, 라이브 표본 1,000건 중 18건이 손상됐다.
# 도구 응답에는 raw 가 없어 복구도 불가능했다.

@pytest.mark.parametrize("text,expect", [
    ("<표 124> 심폐소생술 교육 경험률", "<표 124> 심폐소생술 교육 경험률"),
    ("<그림 3> 추이", "<그림 3> 추이"),
    ("<표 5>와 <그림 2>", "<표 5>와 <그림 2>"),
])
def test_data_angle_brackets_survive(text, expect):
    assert clean_html(text) == expect


@pytest.mark.parametrize("text,expect", [
    ('<font color="red">양연동</font>', "양연동"),
    ("<FONT COLOR=red>대문자</FONT>", "대문자"),
    ("<span class=x>스팬</span>", "스팬"),
    ("<b>강조</b>된 <표 5> 값", "강조된 <표 5> 값"),
])
def test_highlight_markup_still_stripped(text, expect):
    assert clean_html(text) == expect


def test_table_title_record_is_not_destroyed():
    """표,그림DB 는 표제가 거의 전부 `<표 …>` 형태다 — 통째로 비면 자료종 하나가 죽는다."""
    rec = record_from_items([("제어번호", "REFD1"),
                             ("표그림명", "<표 124> 심폐소생술 교육 경험률")])
    assert rec.title == "<표 124> 심폐소생술 교육 경험률"


# ── [11][12] 검증은 strip 하고 전송은 원문 그대로였다 ────────────────────────
# 그 결과 ` 전체 ,교육` 이 검증을 통과한 뒤 서버에서 검색항목이 인식되지 않아
# **전체 카탈로그 13,097,591건**이 돌아왔다.

@pytest.mark.parametrize("bad", ["|", " | ", "||", "  ", "전체,", "전체, ", "|전체,|"])
def test_empty_or_degenerate_search_is_rejected(bad):
    with pytest.raises(C.SearchFieldError):
        C.validate_search(bad)


@pytest.mark.parametrize("raw,sent", [
    (" 전체 ,교육", "전체,교육"),
    ("전체 , 교육 형평성 ", "전체,교육 형평성"),
    ("전체,교육| 자료명 ,불평등", "전체,교육|자료명,불평등"),
])
def test_validate_returns_normalized_string_to_send(raw, sent):
    """🔴 **반환값을 전송해야 한다.** 원문을 보내면 공백 때문에 전체 카탈로그가 온다."""
    assert C.validate_search(raw) == sent


def test_client_sends_the_normalized_search(monkeypatch):
    """검증기의 반환값이 실제 요청에 실리는지 — 버리면 결함이 되살아난다."""
    sent = {}
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page",
                        lambda ep, params, **kw: (sent.update(params), (0, [], {}))[1])
    client.search_meta(" 전체 ,교육", max_records=1)
    assert sent["search"] == "전체,교육"


@pytest.mark.parametrize("bad", ["", "   ", "|"])
def test_detail_search_rejects_empty(bad):
    """빈 search 는 ERR04 재시도를 태우고 원인을 오진하게 만들었다."""
    with pytest.raises(C.SearchFieldError):
        C.validate_detail_search("일반도서", bad)


def test_detail_search_normalizes_too():
    assert C.validate_detail_search("일반도서", " 자료명 , 교육 ") == "자료명,교육"


# ── [9] is_placeholder 부분일치가 정상 데이터를 비웠다 ───────────────────────

@pytest.mark.parametrize("value", [
    "전자형태로만 열람 가능함", "전자형태로만 열람가능함",
    "EB 전자형태로만 열람 가능함", "TM 해당 논문 없음", "해당자료 없음",
])
def test_placeholder_still_detected(value):
    assert C.is_placeholder(value)


@pytest.mark.parametrize("value", [
    "해당사항없음 처리 실태 연구",      # 안내문을 **포함한** 정상 표제
    "목차정보없음에 관한 고찰",
    "전자형태로만 열람 가능함에 대한 비판적 검토",
    "370.951",
])
def test_normal_values_are_not_wiped(value):
    """부분일치로 판정하면 이런 표제가 통째로 빈 문자열이 된다."""
    assert not C.is_placeholder(value)


# ── [13] extra_params 가 검증을 마친 파라미터를 덮어썼다 ────────────────────

@pytest.mark.parametrize("key", ["search", "pageno", "displaylines", "dbname",
                                 "option", "serviceKey", "SERVICEKEY"])
def test_extra_params_cannot_override_reserved(key):
    """덮어쓰면 화이트리스트가 무력해지고 meta 는 **덮어쓰기 전 값**을 보고했다."""
    with pytest.raises(NaError, match="예약 파라미터"):
        NaClient(throttle=0).search_meta("전체,교육", extra_params={key: "x"},
                                         max_records=1)


def test_extra_params_still_allows_unknown_keys(monkeypatch):
    """탐침 용도의 임의 파라미터는 계속 통해야 한다 — 과잉 차단이면 안 된다."""
    sent = {}
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page",
                        lambda ep, params, **kw: (sent.update(params), (0, [], {}))[1])
    client.search_meta("전체,교육", extra_params={"sort": "x"}, max_records=1)
    assert sent["sort"] == "x"


# ── [6] 회수 한계가 page_size 에 따라 달라지는데 상수로 판정했다 ────────────

@pytest.mark.parametrize("size,cap", [(1000, 99_000), (200, 19_800), (100, 9_900)])
def test_record_cap_follows_page_size(monkeypatch, size, cap):
    """🔴 상수 99,000 으로 판정하면 page_size 를 낮췄을 때 **조용한 절단**이 된다."""
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page",
                        lambda ep, params, **kw: (500_000, [], {}))
    _recs, meta = client.search_meta("전체,교육", max_records=1, page_size=size)
    assert meta["api_record_cap"] == cap
    assert meta["cap_hit"] is True
    assert str(cap) in meta["cap_note"].replace(",", "") or f"{cap:,}" in meta["cap_note"]


# ── [8][30] HEAVY_DBNAMES 가 호출자의 명시값까지 덮어썼다 ───────────────────

def test_heavy_dbname_lowers_only_when_unspecified(monkeypatch):
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page",
                        lambda ep, params, **kw: (0, [], {}))
    _r, auto = client.search_meta("전체항목,교육", dbname="외국법률번역DB", max_records=1)
    assert auto["page_size"] == 200          # 미지정 → 안전값으로 하향
    _r, explicit = client.search_meta("전체항목,교육", dbname="외국법률번역DB",
                                      max_records=1, page_size=500)
    assert explicit["page_size"] == 500      # 명시 → 존중(주석이 약속한 동작)


# ── [15][21] option 이 통합검색에서 조용히 무시됐다 ─────────────────────────

def test_option_on_basic_search_warns(monkeypatch):
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page", lambda ep, params, **kw: (5, [], {}))
    _r, meta = client.search_meta("전체,교육", max_records=1,
                                  option="발행년도,2000|발행년도,2010")
    assert "option_ignored_warning" in meta


def test_option_with_dbname_does_not_warn(monkeypatch):
    client = NaClient(throttle=0)
    monkeypatch.setattr(client, "_fetch_page", lambda ep, params, **kw: (5, [], {}))
    _r, meta = client.search_meta("전체항목,교육", dbname="일반도서", max_records=1,
                                  option="발행년도,2000|발행년도,2010")
    assert "option_ignored_warning" not in meta


# ── [17][22] 수집 경로에서 경고가 통째로 버려졌다 ───────────────────────────

def test_collect_propagates_warnings(monkeypatch):
    """🔴 na_collect 에서는 2차 방어선이 아예 사라져 있었다."""
    client = NaClient(throttle=0)

    def fake(search, **kw):
        return [], {"total": C.FULL_CATALOG_TOTAL, "fetched": 0, "cap_hit": False,
                    "failed_pages": [],
                    "ignored_search_warning": "전체 카탈로그로 보인다"}

    monkeypatch.setattr(client, "search_meta", fake)
    _r, meta = client.search_terms_meta(["전체,교육"], max_records=10)
    assert "ignored_search_warning" in meta


# ── [24] 조기 종료가 절단 신호 없이 조용히 끊었다 ───────────────────────────

def test_early_stop_is_reported(monkeypatch):
    """새 레코드 0으로 멈췄는데 total 에 한참 못 미치면 그것도 절단이다."""
    client = NaClient(throttle=0)
    from na_mcp.models import Record
    dup = [Record(control_no="same")]

    calls = {"n": 0}

    def fake(ep, params, **kw):
        calls["n"] += 1
        return (10_000, list(dup), {})

    monkeypatch.setattr(client, "_fetch_page", fake)
    _r, meta = client.search_meta("전체,교육", max_records=5_000)
    assert "early_stop_note" in meta


# ── [27] NAME_MAP 이 매핑하는 필드가 COLUMNS 에서 빠져 출력에서 사라졌다 ─────

def test_every_mapped_field_reaches_output():
    """🔴 11개 필드(지도교수·소관위원회·처리상태·발행국·간행빈도·별치기호…)가
    to_row() 를 쓰는 csv·xlsx·MCP 응답에서 통째로 사라져 있었다."""
    from na_mcp.models import COLUMNS, NAME_MAP
    missing = sorted(set(NAME_MAP.values()) - set(COLUMNS))
    assert not missing, f"COLUMNS 에서 빠진 매핑 필드: {missing}"


def test_placeholder_fields_reach_table_output():
    """빈 값이 '미입력'인지 '안내문'인지 csv·xlsx 에서도 구분돼야 한다."""
    rec = record_from_items([("제어번호", "c1"), ("DDC", "전자형태로만 열람 가능함")])
    row = rec.to_row()
    assert "placeholder_fields" in row
    assert "DDC" in row["placeholder_fields"]


# ── [20] 자격증명이 태그 이름 블록리스트를 우회했다 ─────────────────────────

def test_envelope_scrubs_values_not_just_tag_names(monkeypatch):
    """🔴 `requestUrl`·`echo` 처럼 다른 이름으로 요청을 에코하면 키가 그대로 샜다."""
    monkeypatch.setenv("NA_API_KEY_ENCODED", "SECRETKEYVALUE123456")
    from na_mcp.parser import parse_search_response
    xml = ('<?xml version="1.0" encoding="UTF-8"?><response>'
           '<header><resultCode>00</resultCode></header><total>0</total>'
           '<requestUrl>https://x?serviceKey=SECRETKEYVALUE123456&amp;pageno=1</requestUrl>'
           '</response>')
    _total, _recs, env = parse_search_response(xml)
    assert "SECRETKEYVALUE123456" not in str(env)


def test_record_values_are_scrubbed(monkeypatch):
    """레코드 값은 raw → xlsx/csv/json/sqlite 로 그대로 나간다."""
    monkeypatch.setenv("NA_API_KEY_ENCODED", "SECRETKEYVALUE123456")
    from na_mcp.parser import parse_search_response
    xml = ('<?xml version="1.0" encoding="UTF-8"?><response>'
           '<header><resultCode>00</resultCode></header><total>1</total>'
           '<recode><item><name>제어번호</name><value>SECRETKEYVALUE123456</value></item>'
           '</recode></response>')
    _t, recs, _e = parse_search_response(xml)
    assert "SECRETKEYVALUE123456" not in str(recs[0].raw)


# ── [19] urllib3 DEBUG 가 인증키 든 URL 을 찍었다 ───────────────────────────

def test_log_scrubber_masks_key_in_urllib3(monkeypatch):
    import io
    import logging as _lg
    monkeypatch.setenv("NA_API_KEY_ENCODED", "SECRETKEYVALUE123456")
    C.install_log_scrubber.__globals__["_SCRUB_INSTALLED"] = False
    C.install_log_scrubber()
    buf = io.StringIO()
    handler = _lg.StreamHandler(buf)
    lg = _lg.getLogger("urllib3.connectionpool")
    lg.addHandler(handler)
    lg.setLevel(_lg.DEBUG)
    try:
        lg.debug('GET /y?serviceKey=%s&pageno=1', "SECRETKEYVALUE123456")
    finally:
        lg.removeHandler(handler)
    assert "SECRETKEYVALUE123456" not in buf.getvalue()


# ── [16] 형식 오류가 수집 결과를 통째로 날렸다 ──────────────────────────────

def test_bad_format_writes_nothing(tmp_path):
    """🔴 초판은 json 을 쓴 뒤 예외를 내서 meta 가 통째로 사라지고 쿼터는 이미 썼다."""
    from na_mcp.exporters import export
    from na_mcp.models import Record
    with pytest.raises(ValueError, match="아무 파일도"):
        export([Record(control_no="c1")], ["json", "bogus"], str(tmp_path), "adv")
    assert list(tmp_path.iterdir()) == []


def test_format_string_is_not_iterated_per_character(tmp_path):
    """`formats='json'` 이 문자 단위로 순회해 '형식: j' 오류가 났다."""
    from na_mcp.exporters import export
    from na_mcp.models import Record
    paths = export([Record(control_no="c1")], "json", str(tmp_path), "x")
    assert len(paths) == 1 and paths[0].endswith(".json")


# ── [18] 도구 호출 하나가 쿼터를 무제한으로 태웠다 ──────────────────────────

def test_quota_guard_refuses_before_calling(monkeypatch):
    """⚠️ **인증키 유무에 의존하면 안 된다.** 초판은 키가 있는 로컬에서만 통과하고
       CI(키 없음)에서는 `_NO_KEY` 가 먼저 반환돼 실패했다 — CI 가 잡아준 결함이다.
       도구는 키 확인 → 쿼터 가드 순서이므로 키가 있는 상태를 만들어 가드까지 보낸다.
    """
    from na_mcp import server as srv
    monkeypatch.setattr(srv, "get_api_key", lambda: "dummy")
    out = srv.na_collect(terms=["전체,교육"] * 50, max_records=10 ** 9)
    assert "error" in out and out["estimated_calls"] > out["limit"]


def test_quota_guard_allows_normal_requests():
    from na_mcp import server as srv
    assert srv._quota_guard(1000, None, 1) is None


# ── [2] 수락되지만 항상 0건인 검색항목 ──────────────────────────────────────

def test_zero_yield_field_is_warned():
    """🔴 ERR04 가 아니라 정상 200+total=0 이라 화이트리스트가 '유효'로 기록했다."""
    assert C.zero_yield_fields("일반도서", "목차,교육") == ["목차"]
    assert C.zero_yield_fields("학위논문", "목차,교육") == []   # 여기선 실제로 동작한다
    assert C.zero_yield_fields(None, "전체,교육") == []


# ── [1] 소표본으로 '전건 빈값'을 단정했다 ───────────────────────────────────

def test_gosuh_isbn_no_longer_claimed_empty():
    """795건 재측정에서 10건이 나와 반증됐다 — 192건 표본의 한계."""
    assert "ISBN" not in C.EMPTY_FIELDS_BY_DB["고서"]
