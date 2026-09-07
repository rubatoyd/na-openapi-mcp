"""MCP stdio 프로토콜 스모크 — 실제 서버 프로세스를 띄워 핸드셰이크·도구목록을 검증.

pytest 는 함수를 직접 호출할 뿐 **프로토콜 경로를 타지 않는다.** 클라이언트가 실제로
띄울 수 있는지는 이 스크립트(와 CI 의 콜드 스타트 단계)로만 확인된다.

사용:  uv run python scripts/mcp_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

EXPECTED_TOOLS = {"na_status", "na_search", "na_collect", "na_detail", "na_toc", "na_fields"}

REQUESTS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


def main() -> int:
    payload = "\n".join(json.dumps(r) for r in REQUESTS) + "\n"
    # ⚠️ Windows 는 기본 로케일이 cp949 라 UTF-8 인 도구 설명(한글)에서 디코딩이 깨진다.
    #    부모·자식 양쪽에서 UTF-8 을 명시해야 한다.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    # ⚠️ `subprocess.run(input=…)` 은 쓰기 직후 stdin 을 닫는다. 서버가 마지막 요청의
    #    응답을 내보내기 **전에** EOF 로 종료해 버리는 경합이 생겨 tools/list 가 간헐적으로
    #    비었다(CI 에서 먼저 관측된 것과 같은 현상). stdin 을 잠깐 열어둔 뒤 닫는다.
    proc = subprocess.Popen(
        [sys.executable, "-m", "na_mcp.server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    proc.stdin.write(payload)
    proc.stdin.flush()
    time.sleep(3)                 # 응답을 받아낼 여유
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        out, err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    proc = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    responses = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg:
            responses[msg["id"]] = msg

    if 1 not in responses or "result" not in responses[1]:
        print("initialize 실패", file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1
    print(f"initialize OK — serverInfo={responses[1]['result']['serverInfo']}")

    if 2 not in responses:
        print("tools/list 응답 없음", file=sys.stderr)
        return 1
    tools = {t["name"]: t for t in responses[2]["result"]["tools"]}
    print(f"tools/list OK — {len(tools)}종: {', '.join(sorted(tools))}")

    missing = EXPECTED_TOOLS - set(tools)
    if missing:
        print(f"누락된 도구: {sorted(missing)}", file=sys.stderr)
        return 1

    for name, t in sorted(tools.items()):
        ann = t.get("annotations") or {}
        if not ann.get("openWorldHint"):
            print(f"{name}: openWorldHint 미선언", file=sys.stderr)
            return 1
        print(f"  {name:12} annotations={ann}")

    print("\n스모크 통과.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
