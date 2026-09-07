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
