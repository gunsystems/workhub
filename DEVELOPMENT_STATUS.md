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
