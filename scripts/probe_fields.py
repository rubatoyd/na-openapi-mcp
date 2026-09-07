"""자료종(dbname)별 출력 필드 실태 전수 census — 채움률·값 프로파일·함정.

🔴 **설계 의도: 소표본 금지.** 앞서 6갈래 표집이 각 30건씩 재고 결론을 냈다가,
   1,300건 재측정에서 10건이 뒤집혔다(저작권허락 '상수 N' → 실제 10.5% Y,
   목차 보유율 16.7% → 60.8% 등). 원인은 하나였다 —
   **무필터 검색은 깊이 500건까지 전부 최신 발행년도로 채워진다.**
   그래서 여기서는 (a) 자료종마다 500건씩 받고 (b) **연도 층화**를 반드시 건다.

사용: python scripts/probe_fields.py [dbname …]   (생략 시 전체)
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys
import time
import xml.etree.ElementTree as ET

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "src"))

from probe_api import fetch, p  # noqa: E402

from na_mcp.config import DB_CATEGORIES  # noqa: E402

DETAIL = "https://apis.data.go.kr/9720000/searchservice/detail"
THROTTLE = 0.5
import os
# ⚠️ 자료종에 따라 500건 요청이 ConnectionError 로 끊긴다(외국법률번역DB·표,그림DB 실측).
#    레코드가 길어 응답이 무거운 자료종으로 보인다 — 그때는 이 값을 낮춘다.
ROWS = int(os.environ.get("NA_PROBE_ROWS", "500"))

# 자료종마다 '전체항목'이 유효한지는 config.DB_CATEGORIES 로 확인된다.
# 검색어는 주제 편향을 줄이려 둘을 쓴다.
TERMS = ("교육", "한국")

# 연도 층화 — ⚠️ 이걸 안 걸면 표본이 최신 연도 한 해로 쏠린다(실측).
#   option 은 `발행년도,A|발행년도,B` 형식. 자료종에 따라 무시될 수 있으므로 결과로 판정한다.
STRATA = [
    ("최신(무필터)", None),
    ("2000-2010", "발행년도,2000|발행년도,2010"),
    ("1960-1989", "발행년도,1960|발행년도,1989"),
]

# 값이 실제 데이터가 아니라 안내문인 경우 — 채움으로 세면 안 된다.
# ⚠️ **표기가 흔들린다** — 웹자료에서 `전자형태로만 열람가능함`(공백 없음)과
#    `전자형태로만 열람 가능함`(공백 있음)이 섞여 나온다. 한쪽만 넣으면 계수가 어긋난다.
#    그래서 공백을 지우고 비교한다.
PLACEHOLDERS = ("전자형태로만열람가능함", "해당논문없음", "목차정보없음",
                "해당사항없음", "해당자료없음")


def _is_placeholder(s: str) -> bool:
    flat = re.sub(r"\s+", "", s)
    return any(ph in flat for ph in PLACEHOLDERS)
_YEAR4 = re.compile(r"^\d{4}$")


def _records(dbname: str, term: str, option: str | None):
    """한 번 호출해 레코드(dict 목록)를 돌려준다. 실패는 빈 목록."""
    time.sleep(THROTTLE)
    params = {"pageno": 1, "displaylines": ROWS, "dbname": dbname,
              "search": f"전체항목,{term}"}
    if option:
        params["option"] = option
    r = fetch(DETAIL, params, key_mode="encoded")
    if not r.get("ok") or r["status"] != 200:
        return [], f"HTTP/{r.get('error') or r.get('status')}"
    try:
        root = ET.fromstring(r["body"].encode("utf-8"))
    except ET.ParseError:
        return [], "PARSE_FAIL"
    if root.tag != "response":
        return [], "ERR" + (root.findtext(".//returnReasonCode") or "?")
    out = []
    for rec in root.findall("./recode"):
        out.append({(i.findtext("name") or "").strip(): (i.findtext("value") or "")
                    for i in rec.findall("./item")})
    return out, f"total={root.findtext('./total') or '?'}"


def census(dbname: str) -> None:
    cats = DB_CATEGORIES.get(dbname, ())
    if not cats:
        p(f"\n{'='*72}\n■ {dbname} — 유효 검색항목이 없어 건너뜀(dbname 자체가 무효)")
        return
    if "전체항목" not in cats:
        p(f"\n{'='*72}\n■ {dbname} — '전체항목' 미지원, 건너뜀")
        return

    p(f"\n{'='*72}")
    p(f"■ {dbname}")

    all_recs: list[dict] = []
    per_stratum: dict[str, int] = {}
    years_by_stratum: dict[str, collections.Counter] = {}

    for label, option in STRATA:
        got: list[dict] = []
        note = ""
        for term in TERMS:
            recs, note = _records(dbname, term, option)
            got += recs
            if not recs:
                break
        per_stratum[label] = len(got)
        years_by_stratum[label] = collections.Counter(
            (r.get("발행년도") or r.get("학위년도") or "").strip()[:4] for r in got)
        all_recs += got
        p(f"   {label:<12} {len(got):>5}건  ({note})")

    if not all_recs:
        p("   ✗ 표본 없음")
        return

    n = len(all_recs)
    # 층화가 실제로 먹혔는지 — 최신 층과 구자료 층의 연도 분포가 같으면 option 이 무시된 것이다
    top_recent = years_by_stratum.get("최신(무필터)", collections.Counter()).most_common(1)
    top_old = years_by_stratum.get("2000-2010", collections.Counter()).most_common(1)
    if top_recent and top_old and top_recent[0][0] == top_old[0][0]:
        p(f"   ⚠️ 연도 층화가 먹지 않았다 — 두 층 모두 최빈 연도가 {top_recent[0][0]}. "
          f"이 자료종은 option 이 무시되거나 발행년도 필드명이 다르다.")

    keys = collections.Counter()
    filled = collections.Counter()
    placeholder = collections.Counter()
    values: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in all_recs:
        for k, v in r.items():
            keys[k] += 1
            s = (v or "").strip()
            if s:
                if _is_placeholder(s):
                    placeholder[k] += 1
                else:
                    filled[k] += 1
                values[k][s] += 1

    p(f"\n   표본 {n}건 · 필드 {len(keys)}종")
    p(f"   {'필드':<16}{'키':>6}{'실채움':>7}{'안내문':>7}  {'고유값':>7}  예시")
    for k, kc in keys.most_common():
        f, ph = filled[k], placeholder[k]
        uniq = len(values[k])
        ex = "; ".join(v for v, _ in values[k].most_common(2))[:46]
        flag = ""
        if kc < n:
            flag = "  ←일부 레코드에만 존재"
        if ph:
            flag += f"  ←안내문 {ph}건"
        if uniq == 1 and f == n:
            flag += "  ←상수(변별력 0)"
        p(f"   {k:<16}{kc:>6}{f:>7}{ph:>7}  {uniq:>7}  {ex}{flag}")

    # 연도 형식 함정 — 고서에서 34% 가 4자리가 아니었다
    yk = "학위년도" if "학위년도" in keys else "발행년도"
    if yk in keys:
        ys = [(r.get(yk) or "").strip() for r in all_recs]
        bad = [y for y in ys if not _YEAR4.match(y)]
        if bad:
            p(f"\n   ⚠️ {yk} 가 4자리 숫자가 아닌 건: {len(bad)}/{n} "
              f"({len(bad)*100//n}%) 예: {collections.Counter(bad).most_common(3)}")


def main() -> None:
    targets = sys.argv[1:] or [d for d in DB_CATEGORIES if DB_CATEGORIES[d]]
    p(f"자료종 {len(targets)}종 census — 자료종당 최대 {len(STRATA)*len(TERMS)}회 호출, "
      f"건당 {ROWS}건")
    for db in targets:
        try:
            census(db)
        except Exception as e:  # noqa: BLE001
            p(f"   ✗ {db}: {type(e).__name__}")


if __name__ == "__main__":
    main()
