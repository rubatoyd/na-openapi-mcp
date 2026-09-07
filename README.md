# na-openapi-mcp

**국회도서관(National Assembly Library of Korea) OpenAPI** 검색·수집기 — MCP 서버 + CLI.

자매 프로젝트 [nl-openapi-mcp](https://github.com/rubatoyd/nl-openapi-mcp)(국립중앙도서관) ·
[kci-openapi-mcp](https://github.com/rubatoyd/KCI_openAPI)(KCI) ·
[scienceON-mcp](https://github.com/rubatoyd/scienceON-mcp) 와 동일한 아키텍처를 쓴다.

> 🚧 **개발 중** — 대상 API 의 파라미터·응답 스키마를 라이브 실측으로 확정하는 단계다.
> 확정 전까지 도구 표면은 바뀔 수 있다.

## 설치 · 등록

```json
{
  "mcpServers": {
    "na": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rubatoyd/na-openapi-mcp", "na-mcp"],
      "env": { "NA_API_KEY": "발급받은_Decoding_키" }
    }
  }
}
```

## 인증키

공공데이터포털 [data.go.kr](https://www.data.go.kr) 에서 국회도서관 API 활용신청 후 발급.

⚠️ data.go.kr 은 인증키를 **Encoding / Decoding 두 벌**로 준다.
`NA_API_KEY` 에는 **Decoding(원문)** 을 넣는다 — 라이브러리가 인코딩하므로,
Encoding 키를 넣으면 `%` 가 `%25` 로 이중 인코딩되어 **조용히 인증 실패**한다.
Encoding 키만 있다면 `NA_API_KEY_ENCODED` 에 넣으면 코드가 변환해 쓴다.

## 라이선스

MIT
