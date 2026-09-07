"""PyInstaller 진입점 — 자체완결 바이너리(.mcpb binary 타입)용.

frozen exe 가 stdio MCP 서버를 기동한다. (uv·시스템 Python 불필요)
빌드: packaging/binary/manifest.json + 이 entry → na-mcp[.exe] → mcpb pack
"""
from na_mcp.server import main

if __name__ == "__main__":
    main()
