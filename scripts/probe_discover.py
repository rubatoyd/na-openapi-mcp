"""검색항목 **발견** — 목록에 없는데 동작하는 이름을 찾는다.

🔴 기존 `probe_limits.py categories` 의 구조적 한계:
   후보 집합이 `DB_CATEGORIES`(=자기 출력)와 문서 목록이라, **목록에 없는 이름은
   어떤 실행에서도 시험되지 않는다.** 적대적 리뷰가 `국회회의록/발행자` 를 우연히
   찾아내며 지적한 문제다. "DB_CATEGORIES 는 전수 실측값" 이라는 문구가 실제로
   보증하는 범위는 '문서 목록 중 살아남은 것'까지였다.

🔑 **해법: 레코드의 실제 필드명을 후보로 쓴다.**
   이 API 는 응답 `<item><name>` 에 자료종별 필드명을 준다(census 로 확보). 검색항목
   어휘가 그 필드명과 상당 부분 겹친다는 것이 이미 관측됐다(`기사명`·`논문명`·
   `수록지명/신문명`·`지도교수` 전부 필드명이자 검색항목). 즉 **관측된 어휘**를
   후보로 쓰면 추측 없이 목록 밖을 탐색할 수 있다.

⚠️ 판정은 3단계다 — '거부'와 '수락하지만 쓸모없음'을 구분해야 한다:
     ERR04(재시도 후에도)      → 거부
     total=0 (모든 검색어에서) → 수락하지만 **항상 0건**(색인 비어 있음)
     total>0                   → 실제로 동작
   ERR04 는 간헐적이므로 **반드시 재시도**한다(1회만 태우면 오판한다).

사용: python scripts/probe_discover.py [dbname …]
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
THROTTLE = 0.45
ATTEMPTS = 3
# 실제로 매칭될 가능성이 높은 검색어들 — '항상 0건'을 단정하려면 여러 개를 태워야 한다.
PROBE_WORDS = ("교육", "한국", "2020")


def _get(dbname: str, search: str, rows: int = 1):
    """(상태, total). 상태는 'ok' | 'ERRxx' | 'net'."""
    time.sleep(THROTTLE)
    r = fetch(DETAIL, {"pageno": 1, "displaylines": rows,
                       "dbname": dbname, "search": search}, key_mode="encoded")
    if not r.get("ok") or r["status"] != 200:
        return "net", -1
    try:
        root = ET.fromstring(r["body"].encode("utf-8"))
    except ET.ParseError:
        return "parse", -1
    if root.tag != "response":
        return "ERR" + (root.findtext(".//returnReasonCode") or "?"), -1
    return "ok", int(re.sub(r"\D", "", root.findtext("./total") or "0") or 0)


def observed_field_names(dbname: str) -> list[str]:
    """이 자료종 레코드에 **실제로 등장하는** 필드명 (= 후보 어휘)."""
    cats = DB_CATEGORIES.get(dbname, ())
    if "전체항목" not in cats:
        return []
    names: collections.Counter = collections.Counter()
    for word in ("교육", "한국"):
        st, _ = "ok", 0
        time.sleep(THROTTLE)
        r = fetch(DETAIL, {"pageno": 1, "displaylines": 100, "dbname": dbname,
                           "search": f"전체항목,{word}"}, key_mode="encoded")
        if not r.get("ok") or r["status"] != 200:
            continue
        try:
            root = ET.fromstring(r["body"].encode("utf-8"))
        except ET.ParseError:
            continue
        if root.tag != "response":
            continue
        for rec in root.findall("./recode"):
            for it in rec.findall("./item"):
                nm = (it.findtext("name") or "").strip()
                if nm:
                    names[nm] += 1
        if names:
            break
    return [n for n, _ in names.most_common()]


def classify(dbname: str, field: str) -> tuple[str, int]:
    """검색항목 한 개를 3단계로 판정. (판정, 최대 total)"""
    best = -1
    rejected_every_time = True
    for word in PROBE_WORDS:
        for _ in range(ATTEMPTS):
            st, total = _get(dbname, f"{field},{word}")
            if st == "ok":
                rejected_every_time = False
                best = max(best, total)
                break
        if best > 0:
            break                      # 하나라도 결과가 나오면 '동작'으로 확정
    if rejected_every_time:
        return "거부", -1
    return ("동작" if best > 0 else "항상0건"), max(best, 0)


def discover(dbname: str) -> None:
    listed = set(DB_CATEGORIES.get(dbname, ()))
    if not listed:
        p(f"\n■ {dbname} — 유효 검색항목이 없어 건너뜀")
        return
    observed = observed_field_names(dbname)
    if not observed:
        p(f"\n■ {dbname} — 레코드를 못 받아 건너뜀")
        return
    unlisted = [n for n in observed if n not in listed]
    p(f"\n■ {dbname}")
    p(f"   목록 {len(listed)}개 / 관측 필드명 {len(observed)}개 / **목록 밖 후보 {len(unlisted)}개**")
    if not unlisted:
        p("   → 목록이 관측 어휘를 모두 덮는다")
        return
    found = []
    for field in unlisted:
        verdict, total = classify(dbname, field)
        mark = {"동작": "🔴 동작", "항상0건": "· 항상0건", "거부": "  거부"}[verdict]
        p(f"   {mark:<10} {field:<18} " + (f"최대 total={total:,}" if total > 0 else ""))
        if verdict == "동작":
            found.append((field, total))
    if found:
        p(f"   ⚠️ **목록에 없는데 동작하는 항목 {len(found)}개** — DB_CATEGORIES 에 추가할 것:")
        p(f"      {', '.join(f for f, _ in found)}")


def main() -> None:
    targets = sys.argv[1:] or [d for d in DB_CATEGORIES if DB_CATEGORIES[d]]
    p(f"검색항목 발견 — 자료종 {len(targets)}종")
    p("판정: 거부(ERR04) / 항상0건(수락하나 색인 없음) / 동작(total>0)")
    for db in targets:
        try:
            discover(db)
        except Exception as e:  # noqa: BLE001
            p(f"   ✗ {db}: {type(e).__name__}")


if __name__ == "__main__":
    main()
