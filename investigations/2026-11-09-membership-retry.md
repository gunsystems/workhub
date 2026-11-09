# 직원 접근 차단 및 재확인 오류 조사

## 확인된 운영 사실

- v0.7.1 배포 후 사용자 업무 등록 화면에서 직원 등록 확인 페이지가 표시됨.
- 최초 점검: HiworksDirectoryCache.checked_at=2026-11-09 07:22:53 UTC, fresh=false. 직원 7·8의 HiworksLink는 존재하지만 현재 멤버십 검사는 false.
- 16:30:10 KST POST /accounts/hiworks/access/ → 403. 이어 16:30:12 및 16:30:15 GET 같은 주소 → 405 확인.
- 차단 HTML 응답의 Referrer-Policy=no-referrer로 인해 Origin을 생략하는 HTTPS POST가 CSRF 거부되는 조건이 존재함. CSRF 오류 페이지의 재시도 링크는 GET인데 이 주소는 POST만 허용해 405로 이어짐.
- 서버에서 하이웍스 실제 조회 실행: refresh_directory(force=True)=True, 직원 7·8의 allowed=True 복구. 계정 삭제/재등록/권한 우회 없음.
- 추가 조회 16:38:22 KST: 자동 갱신 checked_at=16:37:41 KST, fresh=true, error 없음. 백그라운드 갱신이 다시 진행되는 것을 확인.
- 최초 캐시 갱신 공백의 세부 원인은 미확정. 배포 시 이전 작업 lease와 작업 순서로 생길 수 있는 지연에 대비하되 이를 확정 원인으로 기록하지 않음.

## v0.7.2 수정

- stale cache일 때 검증된 직원의 업무 요청 진입에서 제한된 실제 디렉터리 재조회를 먼저 수행. 실패나 불일치면 접근 차단 유지.
- 회사 직원 확인을 다른 정기 작업보다 먼저 갱신. 개인 일정/반복 처리 지연에 덜 의존하도록 변경.
- 차단 HTML에 same-origin Referrer 정책 적용. CSRF·외부 Origin 차단 유지.
- 재확인 주소의 GET은 안전하게 대시보드로 이동하고 조회 변경은 CSRF 보호된 POST로만 처리.
- 운영 데이터를 바꾸는 가짜 업무나 강제 로그인 세션을 만들지 않음. 실제 직원 브라우저의 업무 저장·메일 수신 검증은 사용자 확인 전 미완료로 남김.

## 운영 반영 결과

- v0.7.2 LIVE / 09ce83f / dep-dbonhubl550s73ab14o0.
- 전체 294개 검사 통과. 운영 설정의 same-origin 정책·HTTPS CSRF 검사·복귀 GET 302와 실제 직원 두 계정의 접근 가능 상태 확인.
- 실제 사용자 브라우저의 업무 저장·메일 수신은 확인 대기. 운영 구성 요소 검사와 실제 업무 E2E 완료를 혼동하지 않음.
