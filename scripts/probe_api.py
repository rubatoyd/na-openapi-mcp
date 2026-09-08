"""국회도서관 OpenAPI 라이브 탐침 — 파라미터명·응답 스키마를 **실측으로** 확정한다.

CLAUDE.md §개발원칙 "라이브 검증 우선(추정 금지)" 을 실행하는 도구.
자매 프로젝트 nl 에서 추정이 네 번 연속 사고를 냈다(sort 값 지어내기, 연도 필터 부재 오판,
srchTarget 조용한 폴백 미발견, docYn 을 불리언으로 가정). 그래서 코드에 파라미터명을 박기 전에
반드시 이 스크립트로 왕복을 태운다.

⚠️ **인증키는 절대 출력하지 않는다.** 응답이 요청을 에코하는 공공 API 가 많고(kci 에서 실제로
   인증키가 MCP 응답까지 샜다), 예외 메시지에도 URL 이 실린다. `_redact()` 가 전 출력 경로를 덮는다.

사용:
    python scripts/probe_api.py discover              # 후보 엔드포인트 생존 확인
    python scripts/probe_api.py call <url> k=v k=v    # 단건 호출 후 봉투 구조 보고
    python scripts/probe_api.py keymode <url> k=v     # Encoding/Decoding 키 어느 쪽이 맞는지 판별
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(override=False)

TIMEOUT = 20


class ProbeAborted(RuntimeError):
    """계속 측정하면 **결과가 오염되는** 상황 — 쿼터 소진(22)·키 오류(30·31) 등.

    🔴 탐침의 판정은 "이 이름이 거부됐다"인데, 종결코드는 이름의 성질과 무관하다.
       둘을 같은 False 로 접으면 쿼터가 끊긴 시점 이후의 후보가 **전부 '거부'로 확정**되고,
       그 결과가 그대로 `config.DB_CATEGORIES` 화이트리스트로 들어간다.
       측정을 멈추는 편이 오염된 표를 얻는 것보다 낫다.
    """


# ── 인증키 취급 ───────────────────────────────────────────────────────────────
# data.go.kr 은 "일반 인증키"를 Encoding/Decoding 두 벌로 준다.
#   Decoding = 원문(+ / = 포함)      → requests 의 params= 로 넘긴다(requests 가 인코딩).
#   Encoding = 퍼센트인코딩된 문자열 → URL 에 직접 이어붙인다.
# 🔴 **섞으면 조용히 실패한다**: Encoding 키를 params= 로 넘기면 `%` 가 `%25` 로 이중 인코딩되어
#    서버는 전혀 다른 키를 받는다. 인증 오류가 나면 대개 이것이 원인이다.

def decoded_key() -> str | None:
    k = (os.environ.get("NA_API_KEY") or "").strip()
    if k:
        return k
    enc = (os.environ.get("NA_API_KEY_ENCODED") or "").strip()
    return urllib.parse.unquote(enc) if enc else None


def encoded_key() -> str | None:
    k = (os.environ.get("NA_API_KEY_ENCODED") or "").strip()
    if k:
        return k
    dec = (os.environ.get("NA_API_KEY") or "").strip()
    return urllib.parse.quote(dec, safe="") if dec else None


def _secrets() -> list[str]:
    """출력에서 지워야 할 문자열 전부 — 원문·인코딩·이중인코딩·조각까지."""
    out: list[str] = []
    for k in (os.environ.get("NA_API_KEY"), os.environ.get("NA_API_KEY_ENCODED")):
        k = (k or "").strip()
        if not k:
            continue
        out += [k,
                urllib.parse.quote(k, safe=""),
                urllib.parse.quote_plus(k),
                urllib.parse.unquote(k)]
    return [s for s in dict.fromkeys(out) if len(s) > 8]


def _redact(text: Any) -> str:
    """인증키를 모든 표현형으로 지운다. 출력·예외·저장 전 **반드시** 통과시킬 것."""
    s = str(text)
    for sec in _secrets():
        s = s.replace(sec, "***KEY***")
    return s


def p(*args) -> None:
    """redact 를 강제하는 print 래퍼 — 직접 print 를 쓰지 말 것."""
    print(*[_redact(a) for a in args])


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    if (os.environ.get("NA_OS_TRUST") or "1").lower() not in ("0", "false", "no"):
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception as e:  # noqa: BLE001
            p(f"  (truststore 주입 실패: {type(e).__name__} — 교육망이면 인증서 오류 가능)")
    s = requests.Session()
    s.headers["User-Agent"] = "na-openapi-mcp-probe/0.1 (+https://github.com/rubatoyd/na-openapi-mcp)"
    return s


def fetch(url: str, params: dict | None = None, *, key_mode: str = "decoded") -> dict:
    """1회 호출. 예외를 밖으로 내보내지 않고 결과 dict 로 돌려준다.

    ⚠️ `raise_for_status()` 를 쓰지 않는다 — requests 가 **인증키가 든 전체 URL 을 예외
       메시지에 박는다**(자매 프로젝트 nl v0.1.0 의 실제 누출 경로). 상태코드만 본다.
    """
    params = dict(params or {})
    target = url
    if key_mode == "decoded":
        k = decoded_key()
        if k:
            params["serviceKey"] = k
    elif key_mode == "encoded":
        k = encoded_key()
        if k:
            # params 로 넘기면 이중 인코딩되므로 **문자열로 직접 이어붙인다**.
            sep = "&" if "?" in target else "?"
            target = f"{target}{sep}serviceKey={k}"
    elif key_mode != "none":
        raise ValueError(f"key_mode 는 decoded|encoded|none — 받은 값: {key_mode}")

    try:
        r = _session().get(target, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        # 예외 본문에 URL(=인증키)이 실리므로 **타입만** 남긴다.
        return {"ok": False, "error": type(e).__name__}

    # ⚠️ `r.text` 는 charset 없는 Content-Type 에서 chardet 추측에 의존해 한글을 깨뜨린다
    #    (실측: dbname=고서 응답이 ptcp154 로 추측됐다). 선언대로 UTF-8 로 디코드한다.
    body = (r.content or b"").decode("utf-8", "replace")
    return {
        "ok": True,
        "status": r.status_code,
        "ctype": r.headers.get("Content-Type", ""),
        "len": len(body),
        "body": body,
    }


# ── 봉투 구조 분석 ────────────────────────────────────────────────────────────

def shape(body: str, depth: int = 0, _d: int = 0) -> Any:
    """JSON 이면 키 구조만, XML 이면 태그 윤곽만 — 값은 타입/샘플로 축약."""
    t = body.strip()
    if t.startswith("{") or t.startswith("["):
        try:
            return _json_shape(json.loads(t))
        except json.JSONDecodeError:
            pass
    if t.startswith("<"):
        return _xml_shape(t)
    return {"format": "unknown", "head": t[:200]}


def _json_shape(o: Any, _d: int = 0) -> Any:
    if _d > 6:
        return "…"
    if isinstance(o, dict):
        return {k: _json_shape(v, _d + 1) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_shape(o[0], _d + 1), f"…×{len(o)}"] if o else []
    s = str(o)
    return s if len(s) <= 60 else s[:60] + "…"


def _xml_shape(text: str) -> Any:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return {"format": "xml(파싱실패)", "why": str(e), "head": text[:200]}

    def walk(el, d=0):
        if d > 6:
            return "…"
        kids = list(el)
        if not kids:
            v = (el.text or "").strip()
            return v if len(v) <= 60 else v[:60] + "…"
        seen: dict[str, Any] = {}
        for c in kids:
            if c.tag in seen:
                if not isinstance(seen[c.tag], list):
                    seen[c.tag] = [seen[c.tag], f"…반복"]
                continue
            seen[c.tag] = walk(c, d + 1)
        return seen

    return {"format": "xml", "root": root.tag, "tree": walk(root)}


def report(label: str, res: dict) -> None:
    p(f"\n── {label}")
    if not res.get("ok"):
        p(f"   ✗ 연결 실패: {res.get('error')}")
        return
    p(f"   status={res['status']}  ctype={res['ctype']}  len={res['len']:,}")
    body = res["body"]
    if res["status"] != 200:
        p(f"   본문 앞부분: {body[:300]!r}")
        return
    p("   봉투 구조:")
    p(json.dumps(shape(body), ensure_ascii=False, indent=4)[:2500])


# ── 서브커맨드 ────────────────────────────────────────────────────────────────

# ❓ **미검증 후보** — 실측 전까지 어느 것도 사실로 취급하지 않는다.
#    확인되는 대로 docs/NA_API_GUIDE.md 로 옮기고 여기서는 지운다.
CANDIDATES: list[tuple[str, str]] = []


def cmd_discover() -> None:
    if not CANDIDATES:
        p("후보 엔드포인트가 아직 없습니다 — CANDIDATES 에 (라벨, URL) 을 채우고 다시 실행하세요.")
        return
    for label, url in CANDIDATES:
        report(label, fetch(url, {"pageNo": 1, "numOfRows": 1}))


def cmd_call(argv: list[str]) -> None:
    if not argv:
        p("사용: python scripts/probe_api.py call <url> [k=v …] [--key=decoded|encoded|none]")
        return
    url, rest = argv[0], argv[1:]
    key_mode = "decoded"
    params: dict[str, str] = {}
    for a in rest:
        if a.startswith("--key="):
            key_mode = a.split("=", 1)[1]
        elif "=" in a:
            k, v = a.split("=", 1)
            params[k] = v
    report(f"{url}  params={params}  key={key_mode}", fetch(url, params, key_mode=key_mode))


def cmd_keymode(argv: list[str]) -> None:
    """Encoding/Decoding 키 중 **어느 쪽이 통하는지** 대조로 판별한다.

    data.go.kr 에서 가장 흔한 실패 원인이고, 증상이 '인증 오류'라 원인 추적이 어렵다.
    key=none 을 대조군으로 함께 태워 '키 없이도 되는 응답'과 구분한다.
    """
    if not argv:
        p("사용: python scripts/probe_api.py keymode <url> [k=v …]")
        return
    url = argv[0]
    params = dict(a.split("=", 1) for a in argv[1:] if "=" in a)
    for mode in ("decoded", "encoded", "none"):
        report(f"key_mode={mode}", fetch(url, dict(params), key_mode=mode))


def main() -> None:
    have = {"NA_API_KEY": bool(os.environ.get("NA_API_KEY")),
            "NA_API_KEY_ENCODED": bool(os.environ.get("NA_API_KEY_ENCODED"))}
    p(f"인증키 보유: {have}")   # 값이 아니라 **보유 여부만** 찍는다
    if len(sys.argv) < 2:
        p(__doc__ or "")
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "discover":
        cmd_discover()
    elif cmd == "call":
        cmd_call(rest)
    elif cmd == "keymode":
        cmd_keymode(rest)
    else:
        p(f"알 수 없는 명령: {cmd}")
        p(__doc__ or "")


if __name__ == "__main__":
    main()
