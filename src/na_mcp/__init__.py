"""na-openapi-mcp — 국회도서관 OpenAPI MCP 서버 + CLI."""
from importlib.metadata import PackageNotFoundError, version

try:  # 하드코딩하면 릴리스마다 pyproject 와 어긋난다(자매 프로젝트에서 실제로 방치됐다).
    __version__ = version("na-openapi-mcp")
except PackageNotFoundError:  # 소스 트리에서 직접 임포트한 경우
    __version__ = "0.0.0+local"
