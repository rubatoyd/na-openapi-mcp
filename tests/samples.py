"""실응답 발췌 — 추정으로 만든 표본은 없다.

전부 2026-09-07 라이브 호출에서 그대로 떠온 것이다(인증키만 제거).
🔴 특히 레코드 태그가 `recode` 인 것, `<item>` 자식 순서가 `name`→`value` 인 것은
   **공식 문서와 다르다**. 문서대로 고치고 싶어지면 docs/NA_API_GUIDE.md §0 을 볼 것.
"""

# ✅ 실응답: /basic, search=전체,교육불평등 (2건 발췌)
BASIC_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<total>5609</total>\
<recode>\
<item><name>제어번호</name><value>KINX2026037525</value></item>\
<item><name>기사명</name><value>디지털 매체와 놀이 활동을 활용한 세계시민교육 음악 프로그램</value></item>\
<item><name>저자명</name><value>최여진</value></item>\
<item><name>수록지명</name><value>한국초등교육 제37권 제1호</value></item>\
<item><name>발행자</name><value>서울교육대학교</value></item>\
<item><name>키워드</name><value>세계시민교육 디지털매체 음악교육</value></item>\
<item><name>목차</name><value>N</value></item>\
<item><name>본문언어</name><value>kor</value></item>\
<item><name>저작권허락</name><value>N</value></item>\
<item><name>초록유무</name><value>Y</value></item>\
<item><name>발행년도</name><value>2026</value></item>\
<item><name>원본DB유무</name><value>Y</value></item>\
<item><name>자료실</name><value>[본관] 정기간행물실(524호)</value></item>\
</recode>\
<recode>\
<item><name>제어번호</name><value>MONO12026000012887</value></item>\
<item><name>자료명</name><value>교육불평등과 지역불균형</value></item>\
<item><name>저자명</name><value>류장수 지음</value></item>\
<item><name>발행자</name><value>산지니</value></item>\
<item><name>청구기호</name><value>370.951 -26-7</value></item>\
<item><name>ISBN</name><value>9791168616059</value></item>\
<item><name>발행년도</name><value>2026</value></item>\
<item><name>목차</name><value>Y</value></item>\
<item><name>원본DB유무</name><value>N</value></item>\
</recode>\
</response>"""

# ✅ 실응답: 결과 0건 — `<recode>` 가 아예 없고 total 만 0 으로 온다.
EMPTY_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<total>0</total></response>"""

# ✅ 실응답: 인증키 없이 호출(HTTP 401). 루트 元소가 성공과 **다르다**.
NO_KEY_ERR = """<?xml version="1.0" encoding="UTF-8"?>
<OpenAPI_ServiceResponse>
<cmmMsgHeader>
  <errMsg>SERVICE_KEY_IS_NULL</errMsg>
  <returnAuthMsg>서비스 접근거부</returnAuthMsg>
  <returnReasonCode>20</returnReasonCode>
</cmmMsgHeader>
</OpenAPI_ServiceResponse>"""

# ✅ 실응답: Decoding 키를 params 로 넘겼을 때 / 깊은 오프셋에서 간헐 발생.
# ⚠️ 04 는 **재시도 대상**이다(일시적). 20·22·30 과 달리 종료 조건이 아니다.
HTTP_ERR_04 = """<?xml version="1.0" encoding="UTF-8"?>
<OpenAPI_ServiceResponse>
<cmmMsgHeader>
  <errMsg>HTTP_ERROR</errMsg>
  <returnAuthMsg>HTTP 에러</returnAuthMsg>
  <returnReasonCode>04</returnReasonCode>
</cmmMsgHeader>
</OpenAPI_ServiceResponse>"""

# 📄 공식 문서(.hwp)가 적어 놓은 형태 — 태그가 `record` 이고 item 자식 순서가 value→name.
# 🔴 실제 API 는 이렇게 보내지 **않는다**. 문서만 보고 만든 파서가 깨지지 않는지 확인하는 표본.
DOC_SHAPE_RECORD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<record>\
<item><value>KINX2021157581</value><name>controlno</name></item>\
<item><value>미국 연방정부의 코로나 19 백신 /이석민</value><name>자료명/저자사항</name></item>\
<item><value>2022</value><name>발행년</name></item>\
</record>\
<total>572</total></response>"""

# 🔴 조용한 절단 재현 표본: total 은 정상인데 레코드 태그가 낯설다(태그가 또 바뀐 경우).
# 첫 페이지에서 이것을 0건으로 통과시키면 안 된다.
UNKNOWN_TAG = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<total>5609</total>\
<rekord><item><name>제어번호</name><value>X1</value></item></rekord>\
</response>"""

# 오류 봉투가 요청을 에코하는 경우(자매 프로젝트 kci 의 실제 사고 패턴).
# 이 API 가 에코하는지는 ❓ 미확인 — 그래도 파서가 막는지 회귀로 고정한다.
ECHO_KEY_ERR = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>ERR</resultMsg><resultCode>10</resultCode>\
<serviceKey>SUPERSECRETKEYVALUE123</serviceKey></header>\
<total>0</total></response>"""


# ── 상세정보조회 서비스 (detailinfoservice) — ✅ 실응답 발췌 ──────────────────

# ✅ /detailinfoservice/detail — item 이 **최상위에 평탄하게** 온다(`<recode>` 래퍼 없음).
DETAIL_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<item><name>제어번호</name><value>KDMT12026000034521</value></item>\
<item><name>논문명</name><value>한국 교육불평등의 제도적 경로</value></item>\
<item><name>지도교수</name><value>유성상</value></item>\
<item><name>저자명</name><value>박소영</value></item>\
<item><name>발행자</name><value>서울대학교 대학원</value></item>\
<item><name>목차</name><value>N</value></item>\
<item><name>학위년도</name><value>2026</value></item>\
<item><name>원본DB유무</name><value>Y</value></item>\
</response>"""

# ✅ /detailinfoservice/toc — ⚠️ `<toc>` 가 `<header>` **앞**에 온다. 본문은 HTML 이스케이프.
TOC_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<toc>&lt;p&gt;추천의 글&lt;BR&gt;들어가면서&lt;BR&gt;&lt;BR&gt;서장 교육인재정책과 함께한 30년&lt;p&gt;</toc>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header></response>"""

# ✅ 목차가 없는 자료 — 정상이다(검색 결과의 `목차` 가 'N').
TOC_EMPTY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<toc></toc><header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header></response>"""

# 📄 문서(.docx)가 적어 놓은 형태 — item 자식 순서가 value→name (실제는 name→value).
DETAIL_DOC_SHAPE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<item><value>효율적인 국가기반체계 보호를 위한 연구 :</value><name>자료명/저자사항</name></item>\
<item><value>행정안전부, </value><name>발행자</name></item>\
<item><value>MONO1201027232</value><name>제어번호</name></item>\
<item><value>인터넷자료</value><name>DB</name></item>\
</response>"""


# 🔴 ✅ 실응답: 검색 서비스는 **매칭된 필드에 하이라이트 태그**를 넣는다.
#    (`/detail 학위논문 저자명,양연동` 의 실제 응답 — 인증키만 제거)
#    ⚠️ 상세정보조회(detailinfoservice)에는 없다. 검색이 아니기 때문이다.
#    지우지 않으면 저자명이 `<font color="red">양연동</font>` 으로 xlsx/csv 에 저장된다.
HIGHLIGHTED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>\
<total>3</total>\
<recode>\
<item><name>제어번호</name><value>KDMT12025000021882</value></item>\
<item><name>논문명</name><value>경계선지능 학습자의 사회적 구성과 그 실천에 관한 연구</value></item>\
<item><name>지도교수</name><value>손준종</value></item>\
<item><name>저자명</name><value>&lt;font color="red"&gt;양연동&lt;/font&gt;</value></item>\
<item><name>발행자</name><value>한국교원대학교 대학원</value></item>\
<item><name>키워드</name><value>경계선지능   느린학습자   사회적 구성</value></item>\
<item><name>학위년도</name><value>2025</value></item>\
</recode></response>"""
