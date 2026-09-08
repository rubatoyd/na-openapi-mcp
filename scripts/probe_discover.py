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

from probe_api import ProbeAborted, fetch, p  # noqa: E402

from na_mcp.parser import TERMINAL_CODES  # noqa: E402

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
    """검색항목 한 개를 4단계로 판정. (판정, 최대 total)

    🔴 **'거부'와 '측정실패'는 다르다.** 초판은 `ok` 가 아닌 결과를 전부 '거부'로 접었다.
       쿼터 소진(22)·키 오류(30·31)·네트워크 실패는 **이 이름의 성질과 무관**한데도
       '거부'로 기록되어, 그 표를 보고 화이트리스트를 고치면 동작하는 항목이 영구히 막힌다.
       → 종결코드는 즉시 중단(ProbeAborted), 망·파싱 실패는 '측정실패'로 남긴다. 처방이 셋 다 다르다.
    """
    best = -1
    saw_rejection = False      # ERR04 처럼 '이 이름이 거부됐다'는 신호를 실제로 봤는가
    for word in PROBE_WORDS:
        for _ in range(ATTEMPTS):
            st, total = _get(dbname, f"{field},{word}")
            if st == "ok":
                best = max(best, total)
                break
            if st.startswith("ERR"):
                code = st[3:].strip().zfill(2)
                if code in TERMINAL_CODES:
                    raise ProbeAborted(
                        f"종결코드 {code} 를 만나 발견법을 중단한다 "
                        f"(dbname={dbname}, 항목={field}). 남은 후보를 '거부'로 "
                        f"기록하면 화이트리스트가 오염된다.")
                saw_rejection = True
        if best > 0:
            break                      # 하나라도 결과가 나오면 '동작'으로 확정
        if saw_rejection:
            # 🔴 **항목 거부는 검색어와 무관하다**(config.py 의 실측 주석). 그런데 초판은
            #    검색어 3개 × 재시도 3회 = 거부 1건당 최대 9회를 태웠고, 그것이 전수 스윕
            #    비용의 절반 이상이었다. 첫 검색어가 재시도 끝에 거부되면 거기서 확정한다.
            break
    if best < 0:
        return ("거부" if saw_rejection else "측정실패"), -1
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
    # ⚠️ 표시 전용 이름(`발행년도`·`학위구분` 등)은 **이미 거부로 실측된** 것들이다.
    #    후보에 남겨 두면 자료종마다 3회씩 헛호출이 나간다. 다만 **조용히 빼지는 않는다** —
    #    몇 개를 왜 뺐는지 출력한다(측정 범위를 줄인 것을 숨기면 다음 사람이 전수로 오해한다).
    from na_mcp.config import DISPLAY_ONLY_FIELDS
    display_only = [n for n in observed if n not in listed and n in DISPLAY_ONLY_FIELDS]
    unlisted = [n for n in observed if n not in listed and n not in DISPLAY_ONLY_FIELDS]
    p(f"\n■ {dbname}")
    p(f"   목록 {len(listed)}개 / 관측 필드명 {len(observed)}개 / **목록 밖 후보 {len(unlisted)}개**")
    if display_only:
        p(f"   (표시 전용이라 시험에서 제외 {len(display_only)}개: {', '.join(display_only)})")
    if not unlisted:
        p("   → 목록이 관측 어휘를 모두 덮는다")
        return
    found, unmeasured = [], []
    for field in unlisted:
        verdict, total = classify(dbname, field)
        # ⚠️ '측정실패'가 빠져 있으면 여기서 KeyError 로 죽는다 — 판정 범주를 늘릴 때
        #    같이 늘릴 것. get() 이 아니라 명시적으로 둔다(빠뜨림을 조용히 넘기지 않는다).
        mark = {"동작": "🔴 동작", "항상0건": "· 항상0건",
                "거부": "  거부", "측정실패": "?? 측정실패"}[verdict]
        p(f"   {mark:<10} {field:<18} " + (f"최대 total={total:,}" if total > 0 else ""))
        if verdict == "동작":
            found.append((field, total))
        elif verdict == "측정실패":
            unmeasured.append(field)
    if unmeasured:
        # 조용한 절단 금지 — '거부'로 뭉개면 다음 사람이 측정된 사실로 읽는다.
        p(f"   ⚠️ 측정하지 못한 항목 {len(unmeasured)}개(망·파싱 실패) — "
          f"거부가 아니다: {', '.join(unmeasured)}")
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
