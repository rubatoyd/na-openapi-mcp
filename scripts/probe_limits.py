"""국회도서관 자료검색 API 한계 실측 — displaylines 상한 · 페이징 상한 · 검색항목 폴백 · dbname.

문서(.hwp)만 믿으면 안 된다는 것이 이미 확인됐다(`<record>` 로 적혀 있으나 실제는 `<recode>`).
따라서 상한·유효값도 전부 왕복으로 확인한다.

⚠️ 개발계정 일일 트래픽 10,000 건. 이 스크립트는 약 40회를 쓴다. throttle 로 정중하게 호출한다.
   (문서상 초당 최대 30 tps 이지만 그 근처로 갈 이유가 없다.)

사용: python scripts/probe_limits.py [displaylines|paging|fields|dbname|observed|markup|categories|all]
⚠️ `categories` 는 후보 수에 따라 170~520회를 쓴다(ATTEMPTS=3). 나머지는 합쳐 약 40회.
"""
from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))

from probe_api import fetch, p  # noqa: E402

BASIC = "https://apis.data.go.kr/9720000/searchservice/basic"
DETAIL = "https://apis.data.go.kr/9720000/searchservice/detail"
DETAIL_INFO = "https://apis.data.go.kr/9720000/detailinfoservice/detail"
THROTTLE = 0.5


def parse(body: str) -> tuple[str, int, int]:
    """(resultCode, total, 회수된 recode 수). 실패해도 예외를 밖으로 내지 않는다."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return ("PARSE_FAIL", -1, -1)
    if root.tag != "response":                     # 오류 봉투(OpenAPI_ServiceResponse)
        code = root.findtext(".//returnReasonCode") or "?"
        msg = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or ""
        return (f"ERR{code}:{msg}", -1, -1)
    code = root.findtext("./header/resultCode") or "?"
    total = int(re.sub(r"\D", "", root.findtext("./total") or "0") or 0)
    # ✅ 실측: 실제 태그는 `recode` 다(문서의 `record` 는 오기). 둘 다 세어 변화를 감지한다.
    n = len(root.findall("./recode")) + len(root.findall("./record"))
    return (code, total, n)


def call(url: str, **params):
    time.sleep(THROTTLE)
    r = fetch(url, params, key_mode="encoded")
    if not r.get("ok"):
        return (f"NET:{r.get('error')}", -1, -1)
    if r["status"] != 200:
        return (f"HTTP{r['status']}", -1, -1)
    return parse(r["body"])


# ── 1. displaylines 상한 ──────────────────────────────────────────────────────
def probe_displaylines():
    p("\n=== displaylines 상한 (search=전체,교육 / pageno=1) ===")
    p(f"{'요청':>8} | {'code':<24} | {'total':>10} | {'회수':>5}")
    for n in (1, 10, 100, 200, 500, 1000, 2000, 5000):
        code, total, got = call(BASIC, pageno=1, displaylines=n, search="전체,교육")
        p(f"{n:>8} | {code:<24} | {total:>10,} | {got:>5}")


# ── 2. 페이징 상한 (총 회수 가능 건수) ────────────────────────────────────────
def probe_paging():
    p("\n=== 페이징 상한 (displaylines=100 고정, pageno 이동) ===")
    p("자매 API(국립중앙도서관)는 레코드 오프셋 500에서 막혔다. 여기도 있는지 본다.")
    p(f"{'pageno':>8} | {'오프셋':>9} | {'code':<24} | {'total':>10} | {'회수':>5}")
    for page in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        code, total, got = call(BASIC, pageno=page, displaylines=100, search="전체,교육")
        p(f"{page:>8} | {(page-1)*100:>9,} | {code:<24} | {total:>10,} | {got:>5}")
        if got == 0 and code == "00":
            p(f"   ↑ 빈 응답 — 오프셋 {(page-1)*100:,} 부터 회수 불가로 보인다.")
            break


# ── 3. 검색항목 — 미지원 값이 오류인가 조용한 폴백인가 ────────────────────────
def probe_fields():
    p("\n=== 검색항목 검증 (대조군 설계) ===")
    p("문서상 유효: 기본검색·전체·자료명·저자·발행자·키워드·청구기호")
    p("🔑 판별법: **저자명을 엉뚱한 필드에 넣는다.** 필드가 실제로 해석되면 0건이어야 하고,")
    p("   전 필드 검색으로 조용히 폴백하면 '전체' 와 같은 건수가 나온다(자매 API 의 실제 함정).")
    probe_word = "오욱환"          # 교육사회학자 — 저자로는 잡히고 청구기호로는 안 잡혀야 정상
    p(f"\n{'검색항목':<12} | {'code':<20} | {'total':>10}   (kwd={probe_word})")
    baseline = None
    for f in ("전체", "기본검색", "자료명", "저자", "저자명", "발행자", "키워드",
              "청구기호", "ISBN", "발행년도", "존재하지않는필드", "zzz"):
        code, total, _ = call(BASIC, pageno=1, displaylines=1, search=f"{f},{probe_word}")
        if f == "전체":
            baseline = total
        flag = ""
        if baseline is not None and total == baseline and f != "전체":
            flag = "  ← ⚠️ '전체'와 동일 — 폴백 의심"
        p(f"{f:<12} | {code:<20} | {total:>10,}{flag}")


# ── 4. dbname 유효값 (/detail 전용) ───────────────────────────────────────────
def probe_dbname():
    p("\n=== dbname 유효값 (/detail) ===")
    p("문서에는 샘플 '일반도서' 하나뿐이다. 응답의 DB 항목에서 관측된 이름들을 후보로 시험한다.")
    p(f"{'dbname':<16} | {'code':<24} | {'total':>10} | {'회수':>5}")
    for db in ("일반도서", "학위논문", "국내학술기사", "해외학술기사", "전자자료",
               "연속간행물", "정부간행물", "인터넷자원", "멀티미디어", "고서",
               "단행본", "전체", "없는DB이름"):
        code, total, got = call(DETAIL, pageno=1, displaylines=1,
                                dbname=db, search="자료명,교육")
        p(f"{db:<16} | {code:<24} | {total:>10,} | {got:>5}")


def probe_db_observed():
    p("\n=== 응답에 실제로 등장하는 DB 값 (basic, 100건 표본) ===")
    time.sleep(THROTTLE)
    r = fetch(BASIC, {"pageno": 1, "displaylines": 100, "search": "전체,교육"},
              key_mode="encoded")
    if not r.get("ok") or r["status"] != 200:
        p("   조회 실패")
        return
    try:
        root = ET.fromstring(r["body"])
    except ET.ParseError:
        p("   파싱 실패")
        return
    from collections import Counter
    dbs, names = Counter(), Counter()
    for rec in list(root.findall("./recode")) + list(root.findall("./record")):
        for it in rec.findall("./item"):
            nm = (it.findtext("name") or "").strip()
            names[nm] += 1
            if nm == "DB":
                dbs[(it.findtext("value") or "").strip()] += 1
    p(f"   DB 값 분포: {dict(dbs)}")
    p(f"   ⚠️ item name 종류({len(names)}개) — 레코드마다 다르다:")
    for nm, c in names.most_common():
        p(f"      {c:>4}/100  {nm}")




# ── 5. 하이라이트 마크업 — **어느 검색항목이 어느 필드를 오염시키는가** ──────
# 🔴 이 탐침이 없어서 결함을 놓쳤다. 초판은 `전체,교육` 처럼 **제목에 매칭되는 검색어만**
#    돌렸고, 그래서 저자명 매칭 시 `<font color="red">…</font>` 가 붙는 것을 못 봤다.
#    마크업은 정규화 필드·중복제거·xlsx/csv 로 그대로 흘렀다.
# → 교훈: **매칭되는 필드를 바꿔가며** 확인해야 한다. 검색어 하나로는 한 필드만 매칭된다.

_TAG_IN_VALUE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")

# (검색항목, 그 항목에 **실제로 매칭되는** 검색어) — 매칭이 없으면 마크업도 안 생겨 무의미하다.
BASIC_PROBES = [
    ("전체", "양연동"), ("기본검색", "양연동"), ("자료명", "재생산"),
    ("저자", "양연동"), ("발행자", "산지니"), ("키워드", "교육불평등"),
    ("청구기호", "370.951"),
]
DETAIL_PROBES = [
    ("일반도서", "자료명", "재생산"), ("일반도서", "저자명", "알튀세르"),
    ("일반도서", "발행자", "산지니"), ("일반도서", "키워드", "교육불평등"),
    ("일반도서", "ISBN", "9788980386161"), ("일반도서", "청구기호", "370.951"),
    ("학위논문", "논문명", "경계선지능"), ("학위논문", "저자명", "양연동"),
    ("학위논문", "지도교수(2009~)", "손준종"), ("학위논문", "발행자", "한국교원대학교"),
    ("국내기사", "기사명", "교육불평등"), ("국내기사", "저자명", "양연동"),
    ("국내기사", "수록지명", "교육학연구"),
]


def _markup_fields(url, params) -> tuple[str, dict[str, str]]:
    """(상태, {필드명: 발견된 태그}) — 응답 전 필드를 훑어 마크업을 찾는다."""
    time.sleep(THROTTLE)
    r = fetch(url, params, key_mode="encoded")
    if not r.get("ok"):
        return (f"NET:{r.get('error')}", {})
    if r["status"] != 200:
        return (f"HTTP{r['status']}", {})
    try:
        root = ET.fromstring(r["body"])
    except ET.ParseError:
        return ("PARSE_FAIL", {})
    if root.tag != "response":
        code = root.findtext(".//returnReasonCode") or "?"
        return (f"ERR{code}", {})
    found: dict[str, str] = {}
    recs = root.findall("./recode") or root.findall("./record")
    holders = recs or [root]          # 상세정보조회는 item 이 최상위에 온다
    for rec in holders:
        for it in rec.findall("./item"):
            name = (it.findtext("name") or "").strip()
            value = it.findtext("value") or ""
            m = _TAG_IN_VALUE.search(value)
            if m and name not in found:
                found[name] = m.group(1)
    total = int(re.sub(r"\D", "", root.findtext("./total") or "0") or 0)
    return (f"total={total:,}", found)


def probe_markup():
    p("")
    p("=== 하이라이트 마크업 — 검색항목별 오염 필드 ===")
    p("🔴 검색어 하나로는 한 필드만 매칭된다 — **매칭 필드를 바꿔가며** 돌려야 보인다.")
    p("   (초판 탐침이 제목 매칭 검색어만 써서 저자명 마크업을 놓쳤다.)")
    p("")
    p("── /basic (통합검색)")
    for field, kwd in BASIC_PROBES:
        status, found = _markup_fields(BASIC, {"pageno": 1, "displaylines": 5,
                                               "search": f"{field},{kwd}"})
        flag = f"⚠️ {found}" if found else "깨끗"
        p(f"   {field:<8},{kwd:<16} {status:<14} {flag}")

    p("")
    p("── /detail (상세검색)")
    for db, field, kwd in DETAIL_PROBES:
        status, found = _markup_fields(DETAIL, {"pageno": 1, "displaylines": 5,
                                                "dbname": db, "search": f"{field},{kwd}"})
        flag = f"⚠️ {found}" if found else "깨끗"
        p(f"   {db:<6} {field:<14},{kwd:<16} {status:<14} {flag}")

    p("")
    p("── /detailinfoservice/detail (제어번호 조회 — 검색이 아니므로 마크업이 없어야 정상)")
    for cn in ("KDMT12025000021882", "MONO1200732849"):
        status, found = _markup_fields(DETAIL_INFO, {"controlno": cn})
        flag = f"⚠️ {found}" if found else "깨끗(기대대로)"
        p(f"   controlno={cn:<20} {status:<10} {flag}")

    p("")
    p("※ ⚠️ 가 나온 필드는 `models.clean_html()` 이 반드시 정제해야 한다.")
    p("  새 태그 종류(font 외)가 보이면 clean_html 정규식과 tests/samples.py 표본을 갱신할 것.")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = {"displaylines": probe_displaylines, "paging": probe_paging,
            "fields": probe_fields, "dbname": probe_dbname,
            "observed": probe_db_observed, "markup": probe_markup,
            "categories": probe_categories}
    if which == "all":
        for fn in jobs.values():
            fn()
    elif which in jobs:
        jobs[which]()
    else:
        p(f"알 수 없는 항목: {which} (가능: {list(jobs)} 또는 all)")




# ── 6. dbname × 검색항목 전수 실측 ───────────────────────────────────────────
# 🔴 문서(openapi.nanet.go.kr/S3002_03.html)의 카테고리 목록에 **동작하지 않는 이름이 섞여 있고**,
#    반대로 문서에 없는 이름이 동작하기도 한다. 그래서 전수로 태운다.
# 🔑 판별법: **ERR04 = 항목 거부 / total=N (0 포함) = 항목 유효.**
#    매칭이 없어 0건인 것과 항목이 거부된 것은 이렇게 구분된다.
#
# 🔴 **ERR04 는 간헐적이다** — 재시도 없이 1회만 태우면 멀쩡한 항목이 거부로 기록된다.
#    실제로 그렇게 만들었다가 `세미나자료/전체항목` 이 1차 ERR04 → 재시험 total=5,432 로
#    뒤집히는 것을 보고 잡았다. **이 파일 §(E) 에 적어둔 사실을 정작 탐침이 안 지켰다.**
#    → 실패는 반드시 ATTEMPTS 회 재시도한 뒤에만 '거부'로 판정한다.
CATEGORY_PROBE_WORD = "교육"
ATTEMPTS = 3

# 문서 목록 + 실측으로 발견한 변형(문서에 없으나 동작). 측정이 판정한다.
# 🔴 **후보 집합이 자기 출력이면 재현이 성립하지 않는다.** `DB_CATEGORIES` 만 후보로 쓰면
#    이미 걸러진 목록을 다시 확인할 뿐이라, 문서가 말하는 '문서에 있으나 거부되는 25개'를
#    재관측할 수 없고 `지식공유`(후보 0개)는 **호출 0회**로 '없음' 판정이 난다.
#    → 문서 목록(DISPLAY_ONLY_FIELDS 포함)과 타 dbname 어휘까지 후보에 넣는다.
#    ⚠️ 그래도 '목록에 없는데 동작하는 이름'은 이 방법으로 못 찾는다(국회회의록/발행자 실례).
#       완전한 발견에는 어휘 사전이 필요하다 — 한계를 알고 쓸 것.
def _candidate_pool(db: str) -> list[str]:
    from na_mcp.config import DISPLAY_ONLY_FIELDS
    pool = list(DB_CATEGORIES.get(db, ())) + list(EXTRA_CANDIDATES.get(db, ()))
    pool += list(DISPLAY_ONLY_FIELDS)
    for other in DB_CATEGORIES.values():      # 타 자료종 어휘도 넣어 대칭 가정을 깬다
        pool += list(other)
    return list(dict.fromkeys(pool))


EXTRA_CANDIDATES = {
    "학위논문": ("지도교수",),                    # 문서의 `지도교수(2009~)` 는 ERR04
    "학술지,잡지": ("수록지명/신문명",),            # 슬래시 포함 **한 덩어리** 이름
    "신문": ("수록지명/신문명",),
}


def _category_ok(db: str, cat: str) -> bool:
    """항목이 유효한가. ERR04 는 간헐적이므로 ATTEMPTS 회 시도해 한 번이라도 통과하면 유효."""
    for _ in range(ATTEMPTS):
        time.sleep(THROTTLE)
        r = fetch(DETAIL, {"pageno": 1, "displaylines": 1, "dbname": db,
                           "search": f"{cat},{CATEGORY_PROBE_WORD}"}, key_mode="encoded")
        if r.get("ok") and r["status"] == 200:
            try:
                if ET.fromstring(r["body"]).tag == "response":
                    return True
            except ET.ParseError:
                pass
    return False


def probe_categories(only=None):
    """DB_CATEGORIES + 후보 변형을 전수로 태워 **실제 유효한 것만** 추린다.

    결과를 그대로 `config.DB_CATEGORIES` 에 반영할 것 — 화이트리스트가 틀리면
    ① 동작하는 항목을 막고 ② 안 되는 항목을 통과시켜 ERR04 재시도를 낭비한다.
    """
    from na_mcp.config import DB_CATEGORIES

    dbs = [only] if only else list(DB_CATEGORIES)
    p("")
    p(f"=== dbname × 검색항목 전수 실측 (실패 시 {ATTEMPTS}회 재시도, kwd={CATEGORY_PROBE_WORD}) ===")
    p("🔑 ERR04(재시도 후에도) = 항목 거부 / total=N(0 포함) = 항목 유효")
    verified = {}
    for db in dbs:
        cands = _candidate_pool(db)
        ok = [c for c in cands if _category_ok(db, c)]
        bad = [c for c in cands if c not in ok]
        verified[db] = ok
        p(f"  {db}")
        p(f"     OK  {', '.join(ok) if ok else '(없음 — dbname 자체가 무효일 수 있다)'}")
        if bad:
            p(f"     NO  {', '.join(bad)}")
    p("")
    p("-- config.DB_CATEGORIES 에 넣을 실측 결과 --")
    for db, cats in verified.items():
        p(f'    "{db}": {tuple(cats)!r},')
    return verified


if __name__ == "__main__":
    main()
