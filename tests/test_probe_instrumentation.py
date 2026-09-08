"""탐침 계측기 자체의 결함 회귀 (2026-09-08).

🔴 이 파일이 존재하는 이유: **측정 도구가 조용히 틀리면 그 산출물이 코드가 된다.**
   `config.DB_CATEGORIES`(화이트리스트)는 `scripts/probe_limits.py categories` 의
   출력을 그대로 옮긴 것이고, `probe_discover.py` 는 거기 빠진 이름을 찾는 도구다.
   두 도구가 틀리면 화이트리스트가 틀리고, 화이트리스트가 틀리면
   ① 동작하는 항목을 막고 ② 안 되는 항목을 통과시켜 ERR04 재시도를 낭비한다.

여기 고정한 결함 셋 — 전부 오프라인으로 재현된다:
  ① `_candidate_pool` 이 `DB_CATEGORIES` 를 임포트하지 않아 **NameError 로 죽어 있었다.**
     즉 '후보 풀 광역화'는 한 번도 실행된 적이 없는데 CLAUDE.md 는 "전수 실측, 재현 가능"
     이라고 적고 있었다.
  ② `_category_ok` 가 종결코드(쿼터 22·키 30)를 **'항목 거부'로 기록**했다.
     쿼터가 끊긴 시점 이후의 후보가 전부 거부로 확정된다.
  ③ `classify` 가 망·파싱 실패까지 '거부'로 접었다. '측정 못 함'과 '거부됨'은 처방이 다르다.
"""
import importlib.util
import pathlib

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    """스크립트를 파일 경로로 직접 적재한다 — 패키지가 아니라서 일반 import 가 안 된다."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gateway_error(code: str) -> str:
    """게이트웨이 오류 봉투 — 루트가 <response> 가 아니다."""
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><OpenAPI_ServiceResponse>"
            f"<cmmMsgHeader><returnReasonCode>{code}</returnReasonCode>"
            "<errMsg>ERR</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")


# ── ① 후보 풀이 NameError 로 죽어 있었다 ──────────────────────────────────────

def test_candidate_pool_does_not_raise():
    """호출만으로 NameError 가 났다. 이 테스트가 그 재발을 막는다."""
    pl = _load("probe_limits")
    pool = pl._candidate_pool("국회회의록")
    assert isinstance(pool, list) and pool


def test_candidate_pool_includes_other_dbname_vocabulary():
    """풀 광역화의 요점 — 타 자료종 어휘가 실제로 들어와야 한다.

    ⚠️ CLAUDE.md 는 오랫동안 "'목록에 없는데 동작하는 이름'(국회회의록/발행자)은
       원리상 못 찾는다"고 적어 두었지만 **그 예는 틀렸다.** `발행자` 는 `_BOOKISH`
       (일반도서 등)에 있어 이 광역화로 이미 후보에 들어온다.
    """
    pl = _load("probe_limits")
    pool = pl._candidate_pool("국회회의록")
    from na_mcp.config import DB_CATEGORIES
    assert "발행자" not in DB_CATEGORIES["국회회의록"]   # 국회회의록 목록에는 없고
    assert "발행자" in pool                              # 그래도 후보에는 들어온다


def test_candidate_pool_covers_display_only_fields():
    """문서에만 있고 거부되는 이름도 후보여야 '문서가 틀렸다'를 재관측할 수 있다."""
    pl = _load("probe_limits")
    from na_mcp.config import DISPLAY_ONLY_FIELDS
    pool = pl._candidate_pool("일반도서")
    assert set(DISPLAY_ONLY_FIELDS) <= set(pool)


# ── ② 종결코드를 '항목 거부'로 기록했다 ───────────────────────────────────────

@pytest.mark.parametrize("code", ["22", "30", "31"])
def test_category_ok_aborts_on_terminal_code(monkeypatch, code):
    """쿼터·키 문제는 항목의 성질과 무관하다 — 거부로 기록하면 화이트리스트가 오염된다."""
    pl = _load("probe_limits")
    monkeypatch.setattr(pl, "THROTTLE", 0)
    monkeypatch.setattr(pl, "fetch",
                        lambda *a, **k: {"ok": True, "status": 200, "body": _gateway_error(code)})
    with pytest.raises(pl.ProbeAborted):
        pl._category_ok("일반도서", "자료명")


def test_category_ok_still_reports_err04_as_rejected(monkeypatch):
    """ERR04 는 재시도 대상이자 **진짜 거부 신호**다 — 중단시키면 측정이 성립하지 않는다."""
    pl = _load("probe_limits")
    monkeypatch.setattr(pl, "THROTTLE", 0)
    monkeypatch.setattr(pl, "fetch",
                        lambda *a, **k: {"ok": True, "status": 200, "body": _gateway_error("04")})
    assert pl._category_ok("일반도서", "없는항목") is False


# ── ③ 망·파싱 실패를 '거부'로 접었다 ─────────────────────────────────────────

def test_classify_reports_unmeasured_not_rejected(monkeypatch):
    """네트워크가 죽어 한 번도 못 물어본 항목을 '거부'라고 적으면 안 된다."""
    pd = _load("probe_discover")
    monkeypatch.setattr(pd, "_get", lambda *a, **k: ("net", -1))
    verdict, total = pd.classify("일반도서", "어떤항목")
    assert verdict == "측정실패" and total == -1


def test_classify_reports_err04_as_rejected(monkeypatch):
    pd = _load("probe_discover")
    monkeypatch.setattr(pd, "_get", lambda *a, **k: ("ERR04", -1))
    assert pd.classify("일반도서", "없는항목")[0] == "거부"


def test_classify_aborts_on_terminal_code(monkeypatch):
    pd = _load("probe_discover")
    monkeypatch.setattr(pd, "_get", lambda *a, **k: ("ERR22", -1))
    with pytest.raises(pd.ProbeAborted):
        pd.classify("일반도서", "어떤항목")


def test_classify_zero_hit_is_not_rejection(monkeypatch):
    """수락되지만 0건인 항목과 거부된 항목은 다르다(기존 구분을 깨지 않았는지 확인)."""
    pd = _load("probe_discover")
    monkeypatch.setattr(pd, "_get", lambda *a, **k: ("ok", 0))
    assert pd.classify("일반도서", "청구기호")[0] == "항상0건"


def test_discover_verdict_marks_cover_every_verdict():
    """판정 범주를 늘리고 표시 표를 안 늘리면 KeyError 로 죽는다 — 실제로 그럴 뻔했다."""
    src = (SCRIPTS / "probe_discover.py").read_text(encoding="utf-8")
    for verdict in ("동작", "항상0건", "거부", "측정실패"):
        assert f'"{verdict}"' in src
