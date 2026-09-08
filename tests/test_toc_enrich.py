"""목차 대량 보강(`na_collect(toc_max=...)`) 회귀.

🔴 이 기능의 위험은 **부분 성공이 조용히 전체 성공으로 읽히는 것**이다.
   목차가 안 붙은 행이 생기는 이유가 다섯인데 처방이 전부 다르다:
     플래그가 Y 가 아님 / 본문이 실제로 없음 / 센티널 / 조회 실패 / 예산 소진
   같은 빈칸에 섞으면 "이 자료에는 목차가 없다"는 잘못된 결론이 나온다.
   → 아래 테스트는 다섯 사유가 **서로 다른 값·다른 경고**로 나오는 것을 고정한다.

전부 오프라인이다 — `_lookup` 을 대체해 네트워크·쿼터를 쓰지 않는다.
"""
import pytest

from na_mcp import config as C
from na_mcp import server as srv
from na_mcp.client import NaClient, NaError, _coded
from na_mcp.models import COLUMNS, Record


def _rec(cn: str, has_toc: str = "Y") -> Record:
    return Record(control_no=cn, title=f"자료 {cn}", has_toc=has_toc)


def _client(responses):
    """`_lookup` 을 대체한다. responses[controlno] 가 값이면 반환, 예외면 raise."""
    c = NaClient(throttle=0)

    def fake(endpoint, controlno, parse, *, what, attempts=None):
        c._calls += 1                      # 실제 호출 회계를 흉내 낸다
        r = responses[controlno]
        if isinstance(r, Exception):
            raise r
        return r

    c._lookup = fake
    return c


# ── 0원 필터: 호출하지 않아야 할 것 ──────────────────────────────────────────

def test_flag_not_y_is_skipped_without_calling():
    """`목차` 플래그가 Y 가 아니면 **호출조차 하지 않는다** — 예산을 유효 후보에 몰아준다."""
    recs = [_rec("A", "N"), _rec("B", ""), _rec("C", "Y")]
    c = _client({"C": ("제1장 서론", {})})
    st = c.enrich_toc(recs, 10)
    assert st["skipped_flag"] == 2 and st["attempted"] == 1
    assert [r.toc_status for r in recs] == ["skipped", "skipped", "ok"]
    assert st["api_calls"] == 1


def test_missing_controlno_is_skipped():
    recs = [_rec("", "Y")]
    st = _client({}).enrich_toc(recs, 10)
    assert st["skipped_no_controlno"] == 1 and st["attempted"] == 0
    assert recs[0].toc_status == "skipped"


def test_toc_always_empty_dbname_skips_everything():
    """목차가 전건 없는 자료종은 한 건도 부르지 않는다 — 순수 쿼터 낭비다."""
    recs = [_rec("A"), _rec("B")]
    st = _client({}).enrich_toc(recs, 10, dbname="E-BOOK")
    assert st["skip_reason"] == "dbname_toc_always_empty"
    assert st["api_calls"] == 0 and all(r.toc_status == "skipped" for r in recs)


def test_always_zero_categories_is_not_confused_with_toc_empty_dbnames():
    """🔴 함정 테스트 — 두 상수를 혼동하면 일반도서·고서를 통째로 건너뛴다.

    `ALWAYS_ZERO_CATEGORIES` 는 '검색항목이 항상 0건', `TOC_ALWAYS_EMPTY_DBNAMES` 는
    '목차 본문이 항상 없음'이다. 전자에는 일반도서·고서가 들어 있다.
    """
    assert "일반도서" not in C.TOC_ALWAYS_EMPTY_DBNAMES
    assert "고서" not in C.TOC_ALWAYS_EMPTY_DBNAMES
    recs = [_rec("A")]
    st = _client({"A": ("본문", {})}).enrich_toc(recs, 10, dbname="일반도서")
    assert st["skip_reason"] is None and st["ok"] == 1


# ── 다섯 사유가 서로 다른 값으로 나오는가 ────────────────────────────────────

def test_five_outcomes_are_distinguishable():
    recs = [_rec("OK"), _rec("EMPTY"), _rec("SENT"), _rec("FAIL"), _rec("LATER")]
    c = _client({
        "OK": ("제1장 서론", {}),
        "EMPTY": ("", {}),                                   # 플래그 Y 가 거짓이었다
        "SENT": ("", {"toc_sentinel": "목차정보없음"}),        # 센티널
        "FAIL": _coded(NaError("조회 실패"), "04", terminal=False),
    })
    st = c.enrich_toc(recs, 4)          # 예산 4 → 마지막 1건은 미시도
    assert [r.toc_status for r in recs] == ["ok", "empty", "sentinel", "failed",
                                            "not_attempted"]
    assert (st["ok"], st["empty"], st["sentinel"], st["failed"],
            st["not_attempted"]) == (1, 1, 1, 1, 1)
    assert recs[0].toc_chars == len("제1장 서론") and recs[1].toc_chars == 0


def test_sentinel_is_not_counted_as_success_or_empty():
    """센티널은 '있음'도 '없음'도 아니다 — 코퍼스에 쓰레기가 쌓이는 것을 막는 구분이다."""
    recs = [_rec("S")]
    st = _client({"S": ("", {"toc_sentinel": "목차정보없음"})}).enrich_toc(recs, 5)
    assert st["sentinel"] == 1 and st["ok"] == 0 and st["empty"] == 0


# ── 예산·중단: 조용한 절단 금지 ──────────────────────────────────────────────

def test_budget_hit_marks_remaining_as_not_attempted():
    recs = [_rec(f"R{i}") for i in range(5)]
    c = _client({f"R{i}": ("목차", {}) for i in range(5)})
    st = c.enrich_toc(recs, 2)
    assert st["budget_hit"] is True and st["not_attempted"] == 3
    assert st["api_calls"] == 2                       # 예산을 넘겨 부르지 않았다
    assert [r.toc_status for r in recs[2:]] == ["not_attempted"] * 3


def test_terminal_error_aborts_without_burning_budget():
    """🔴 쿼터 소진(22)을 만나면 남은 예산을 태우지 않는다 — 전부 실패할 것이 확실하다."""
    recs = [_rec(f"R{i}") for i in range(10)]
    c = _client({"R0": ("목차", {}),
                 "R1": _coded(NaError("쿼터"), "22", terminal=True)})
    st = c.enrich_toc(recs, 10)
    assert st["aborted"] == {"reason": "terminal_error", "code": "22"}
    assert st["api_calls"] == 2                       # R2 이후는 부르지 않았다
    assert st["failed"] == 0                          # 중단은 '건별 실패'가 아니다
    assert recs[5].toc_status == "not_attempted"


def test_consecutive_failures_abort():
    """서버가 죽었는데 예산 끝까지 태우지 않는다."""
    n = C.TOC_ENRICH_CONSECUTIVE_FAIL_ABORT
    recs = [_rec(f"R{i}") for i in range(n + 5)]
    c = _client({f"R{i}": _coded(NaError("실패"), "04", terminal=False)
                 for i in range(n + 5)})
    st = c.enrich_toc(recs, n + 5)
    assert st["aborted"]["reason"] == "consecutive_failures"
    assert st["api_calls"] == n


def test_stats_are_always_present_even_when_nothing_enriched():
    """집계를 조건부로 실으면 호출자가 '보강이 안 돌았다'와 '0건이었다'를 구분 못 한다."""
    st = _client({}).enrich_toc([_rec("A", "N")], 10)
    for k in ("candidates", "attempted", "api_calls", "ok", "empty", "sentinel",
              "failed", "not_attempted", "budget_hit", "aborted"):
        assert k in st


def test_api_calls_is_measured_not_estimated():
    """재시도가 있어도 실제 호출 수가 보고돼야 한다 — 추정치는 쿼터 회계가 못 된다."""
    recs = [_rec("A")]
    c = _client({"A": ("목차", {})})
    c._calls += 7                                     # 검색이 이미 쓴 호출
    st = c.enrich_toc(recs, 5)
    assert st["api_calls"] == 1                       # 델타만 센다


# ── 출력 형태 ────────────────────────────────────────────────────────────────

def test_toc_text_stays_out_of_table_columns():
    """본문은 수천 자다 — 표에 실으면 xlsx 셀 상한·csv 비대화·컨텍스트 소모가 난다."""
    assert "toc_text" not in COLUMNS
    assert "toc_status" in COLUMNS and "toc_chars" in COLUMNS
    r = _rec("A")
    r.toc_text = "본문" * 5000
    assert "toc_text" not in r.to_row()


def test_toc_status_column_sits_next_to_has_toc():
    """`has_toc=Y` 인데 `toc_status=empty` 인 행이 가장 중요한 사실 — 나란히 보여야 한다."""
    i = COLUMNS.index("has_toc")
    assert COLUMNS[i + 1:i + 3] == ["toc_status", "toc_chars"]


def test_status_values_are_all_declared():
    """판정 값을 늘리고 상수를 안 늘리면 문서와 코드가 어긋난다."""
    used = {"", "skipped", "ok", "empty", "sentinel", "failed", "not_attempted"}
    assert used == set(C.TOC_STATUS_VALUES)


# ── 서버 가드 ────────────────────────────────────────────────────────────────

def test_toc_quota_guard_allows_zero_and_normal():
    assert srv._toc_quota_guard(0) is None
    assert srv._toc_quota_guard(C.TOC_ENRICH_SUGGESTED) is None


def test_toc_quota_guard_rejects_over_hard_cap():
    out = srv._toc_quota_guard(C.TOC_ENRICH_HARD_CAP + 1)
    assert out and "하드캡" in out["error"]


def test_toc_quota_guard_rejects_over_per_call_limit():
    out = srv._toc_quota_guard(srv.MAX_TOC_CALLS_PER_TOOL_CALL + 1)
    assert out and out["limit"] == srv.MAX_TOC_CALLS_PER_TOOL_CALL


def test_toc_budget_is_separate_from_search_budget():
    """🔴 합산하면 보강 상한을 올리는 순간 **검색 가드까지 함께 풀린다**(심사 지적)."""
    assert srv._quota_guard(1000, None, 1) is None          # 표준 수집은 그대로 통과
    assert srv._toc_quota_guard(srv.MAX_TOC_CALLS_PER_TOOL_CALL) is None


def test_cli_help_survives_cp949_stdout():
    """🔴 한국어 Windows 에서 출력을 리다이렉트하면 CLI 가 죽던 결함.

    `--toc-max` help 에 넣은 `—`(U+2014)가 cp949 에 없어 argparse 가
    UnicodeEncodeError 로 **종료코드 1**이 됐다(이 기능을 붙이며 내가 넣은 결함).
    문자를 바꾸고 `_harden_stdio()` 로 같은 부류가 도구를 죽이지 못하게 했다.
    """
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    for cmd in (["collect", "--help"], ["search", "--help"], ["--help"]):
        r = subprocess.run([sys.executable, "-m", "na_mcp.cli", *cmd],
                           capture_output=True, env=env)
        assert r.returncode == 0, f"na {' '.join(cmd)} 가 cp949 stdout 에서 죽는다"


def test_help_strings_are_cp949_encodable():
    """도구가 죽지 않더라도 help 가 물음표 범벅이 되는 것은 좋지 않다 — 애초에 넣지 말자."""
    from na_mcp import cli
    src = __import__("pathlib").Path(cli.__file__).read_text(encoding="utf-8")
    for bad, why in (("—", "EM DASH(U+2014) 는 cp949 에 없다 — U+2015 나 쉼표를 쓸 것"),):
        for line in src.splitlines():
            if "help=" in line or 'help="' in line:
                assert bad not in line, f"{why}: {line.strip()[:60]}"


def test_default_is_off():
    """기본 off 는 협상 대상이 아니다 — 켜는 것은 호출자의 명시적 결정이다."""
    import inspect
    assert inspect.signature(srv.na_collect).parameters["toc_max"].default == 0
    assert C.TOC_ENRICH_DEFAULT == 0


# ── 별건: 합집합 경로의 회수 한계 오보고 ─────────────────────────────────────

@pytest.mark.parametrize("page_size,expect", [(None, 99_000), (100, 9_900), (10, 990)])
def test_union_path_reports_computed_record_cap(monkeypatch, page_size, expect):
    """🔴 `search_meta` 는 계산값을 쓰는데 `search_terms_meta` 만 상수를 실었다.

    page_size 를 낮추면 회수 한계도 함께 낮아진다 — 상수를 보고하면 **거짓말**이다.
    같은 결함을 한 곳만 고쳐 둔 상태였다.
    """
    c = NaClient(throttle=0)
    monkeypatch.setattr(c, "search_meta",
                        lambda *a, **k: ([], {"total": 0, "fetched": 0, "cap_hit": False,
                                              "failed_pages": []}))
    _recs, meta = c.search_terms_meta(["전체,교육"], max_records=10, page_size=page_size)
    assert meta["api_record_cap"] == expect
