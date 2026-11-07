# 개발 인수인계 · 2026-11-07

- 프로젝트: GUNSYSTEMS Workhub, https://github.com/gunsystems/workhub
- 기반 main: 7be6e91258b271d2982d3a4481d07c5188dea25d (2026-11-06 22:38 KST)
- 이번 브랜치: feature/employee-invitation-mfa
- 요청: 관리자 직원 초대 → 회사 이메일 코드 인증 → 비밀번호 설정 → OTP 등록/로그인, 관리자-직원 상호 업무 테스트.
- 회사 메일: 하이웍스. 실제 발신 계정/SMTP 인증정보 미연결.
- 이번 구현: AUTH_SETUP.md 참조. 인증 기능과 기존 업무 기능은 자동 테스트로 검증. 실제 직원 초대 메일 수신과 휴대폰 OTP 등록은 SMTP 설정 후 확인 필요.
- 대상 서버: gunsystems-workhub-test, https://gunsystems-workhub-test.onrender.com
- 기존 배포는 27bf942 (v0.2 대시보드/평가 단계), main의 v0.3 기능도 아직 미배포인 상태에서 작업 시작.
- 회사 홈페이지 저장소/서버/DB/도메인은 변경 대상이 아님.
- 실제 메일 발송·직원 계정 생성·서버 배포 상태를 완료로 추정하지 말고 각 단계에서 확인할 것.

## API 발송 전환 작업

- v0.4 배포 완료: f6ce86c, health 정상. SMTP 인증은 535로 거절됨.
- 사용자가 Office Token(이메일 보내기 권한, 허용 IP 설정)을 생성하고 Render에 HIWORKS_OFFICE_TOKEN을 저장했다고 확인함.
- 변경: 하이웍스 메일 REST API backend를 기본 발송 방식으로 사용. 발신 user_id=admin. SMTP fallback 없음.
- 테스트 직원: 이상건 / GUNSYSTEMS / 경영관리 / lee@gunsystems.co.kr / 일반 직원.
- 현재 단계: API 변경 코드 자동 테스트 및 배포 준비. 실제 발송 결과·직원 활성화 여부는 확인 후 별도로 기록할 것.

## 초대 메일 디자인

- GUNSYSTEMS 헤더, 계정/부서/권한 카드, 시작 절차, 활성화 버튼과 48시간 만료 안내를 HTML 템플릿으로 추가.
- 메일에 긴 URL을 노출하지 않고 버튼과 이름 있는 보조 링크만 표시. 텍스트 대체본에도 원본 URL은 표시하지 않음.
- 하이웍스 API backend는 HTML alternative를 유지하며 일반 텍스트 메일은 계속 HTML escape 처리.
- 관리자만 볼 수 있는 디자인 미리보기는 사용 가능한 초대 토큰을 생성하거나 노출하지 않음.
- 사용자 요청으로 lee@gunsystems.co.kr에 디자인 적용 초대를 재발송할 예정. 재발송은 이전 링크를 무효화.

## HTTPS 인증 요청 CSRF 수정

- 사용자가 이메일 코드 발송 버튼에서 CSRF 403 오류를 보고함. 배포된 로그인 화면에서도 브라우저의 일반 form POST로 동일 오류를 재현.
- 인증 화면의 Referrer-Policy:no-referrer가 HTTPS CSRF provenance 검사와 충돌. same-origin으로 변경해 외부 URL 유출 차단을 유지하면서 정상 same-origin POST를 허용.
- HTTPS reverse proxy 조건에서 Origin/Referer 유무·올바른 토큰·잘못된 토큰·외부 Origin·쿠키 누락을 검증하는 테스트 추가.
- CSRF 검증 자체는 유지. 만료/잘못된 요청은 403으로 차단하고 브랜드 안내와 화면 다시 열기 링크를 제공.
- 기존 초대·코드·직원 데이터는 변경하지 않음. 배포 후 기존 인증 페이지를 새로 열어야 새 정책이 적용됨.
