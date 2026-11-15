# 문서 권한 관리 · 적용 준비

상태: 구현 및 CI(Django 827개·Node 10개) 통과. 운영 배포 및 실제 ACL 검증 전. Microsoft 설정 진행 현황은 맨 아래 최신 기록 참조.

## 동작
- 관리자 전용: 폴더/파일 옆 권한 관리 → 직원별 허용하지 않음/보기/편집 → 변경 사유 → 영향 검토 → 적용.
- 실제 SharePoint ACL을 조회/변경하며 Workhub만의 별도 접근권한표는 두지 않음.
- 기존 일반 그룹 및 개인 권한을 선택 직원으로 대체. 소유자·권한관리자·현재 처리자·제한된 접근은 보존.
- 상위 상속을 복사 후 분리. 상속하는 하위 항목은 영향을 받고 이미 독립 권한인 하위 항목은 유지.
- 사이트 관리자의 고유 접근은 회수되지 않음.
- 일반 직원은 본인 Microsoft 위임 토큰으로만 문서 조회. 관리자 별도 연결을 빌려쓰지 못함.
- 연결/권한 변경 시작/단계/결과/부분 실패를 시스템 로그로 기록.
- SharePoint에서 직접 바꾼 권한은 다음 조회에 반영. 직접 변경의 감사 기록은 Microsoft 감사 로그 대상이며 Workhub 로그가 자동 수집하지 않음.

## 필요한 Microsoft 설정 (사용자 확인 후에만)
1. 문서 앱 dfadf2e7-3a78-464e-a8fc-f99599fd2818에 **SharePoint / Delegated / Sites.Selected** 추가 및 동의.
2. Web 복귀 주소 https://workhub.gunsystems.co.kr/work/shared-documents/permissions/callback/ 추가.
3. Workhub 전용 문서 사이트의 앱 권한을 write → fullcontrol로 변경. 다른 사이트는 부여하지 않음.
4. 사이트 권한 설정용 Graph Explorer의 임시 Sites.FullControl.All 동의는 작업 후 회수.
5. GUNSYSTEMS 관리자 Workhub 세션에서 lee 회사 Microsoft 계정을 권한 관리자 연결로 인증.

2026-11-15 Entra 실제 카탈로그에서 SharePoint 위임 Sites.Selected 지원 확인. 선택/추가/동의는 하지 않음.

## 배포 전 및 실제 검증
- 전체 회귀 검사, 마이그레이션 일치, Node 검사.
- PC/모바일 실제 배포 화면 확인은 아직 미완료. 로컬 HTML 생성했으나 점검용 브라우저 localhost 접근 실패.
- 검증용 빈 폴더만 사용: 보기/편집/해제, 일반 직원 비노출 및 직접 URL 차단, 소유자 보존.
- SharePoint에서 변경 후 Workhub 재조회, 권한 변경 로그·부분 실패 확인.
- 동시 변경은 요청 전후 재조회로 감지. SharePoint API의 여러 호출은 원자적 트랜잭션이 아니며 중간 실패 시 결과 재확인 필요.

## 근거
- https://learn.microsoft.com/en-us/graph/permissions-selected-overview
- https://learn.microsoft.com/en-us/sharepoint/dev/sp-add-ins/set-custom-permissions-on-a-list-by-using-the-rest-interface

검사 현황: 관련 Django 68개 + 파일 관리 17개 통과, Node 검사 10개 통과, makemigrations 변경 없음. 전체 826개 CI 동일 환경 재검사 실행 중(outputs/document-permissions-regression.log). 앞선 전체 실행은 CI와 다른 기본 보안/스케줄러 환경으로 실행되어 중단했으며 성공으로 기록하지 않음. Microsoft 설정 확대는 사용자 확인 대기.

최종 준비 기록 2026-11-15:
- 사용자 권한 확대 승인 받음. SharePoint 위임 Sites.Selected 추가/관리자 콜백/전용 사이트 write→fullcontrol/Graph Explorer 임시 권한 회수만 승인 범위.
- Microsoft 설정 저장은 아직 실행하지 못함. 탭38에서 검색·펼치기까지만 확인. 권한 행 클릭은 시간 초과, Add permissions나 관리자 동의 버튼은 누르지 않음.
- 사용자 재로그인 후에도 CUA getTab/getState가 연속 시간 초과. 명시적 js_reset 후 getState도 실패. 브라우저 제어 연결 자체가 현재 막힘. CUA completion_summary 완료.
- 운영 main d7e2a1580eddf781f901ba2fb2ece4a996f86897, 실제 운영 소스 v0.28.0 45b5cba05db6859fc87ec75422a652c70a274694 유지.
- 개발 브랜치 feature/document-permission-management, 코드 6bc7a260ba36680797c9a9e93cbec2409c779549, 문서 후속 d95c4d2bbc345e868e0edb8f810f4a4066b2039c.
- Draft PR https://github.com/gunsystems/workhub/pull/23. CI 43713042233/job132808584016: Django827 + Node10 전부 통과. 배포/merge 안 함.
- Windows 전체 검사는 sandbox 임시 폴더 정리 Access denied에서 중단. 임시경로를 작업폴더 .tmp/test-runtime로 바꾼 재검사는 CI전체통과 확인 후 종료. 앞선 실패는 기능 오류로 간주하지 않고 CI증거 보존.
- 로컬 테스트/미리보기 서버 실행 종료. 로컬 실제 HTML은 만들었으나 IAB localhost 연결 시간 초과, data URL은 보안 정책이 금지하여 우회하지 않음. 배포 후 실제 PC·모바일 검증 필요.
- 다음: 브라우저 제어 복구 → 승인 범위 MS설정 → 배포 준비 화면 확인 → 관리자 별도 Microsoft OAuth → 검증용 빈 폴더에서 실제 ACL/재조회/비권한자 차단 및 소유자 보존 확인 → 설정용 Graph Explorer 권한 회수 검증 → 이력 업데이트.

## 최신 Microsoft 점검 기록 · 2026-11-15
- 문서 앱 SharePoint 위임 Sites.Selected 추가 및 관리자 동의 성공 확인. 기존 Graph 권한 유지.
- 관리자용 Web 복귀 주소 `/work/shared-documents/permissions/callback/` 저장 확인. 기존 문서 연결 복귀 주소 유지.
- Graph Explorer 엔터프라이즈 앱의 User consent 목록에 Sites.FullControl.All 저장 확인. 해당 동의 사용자는 SANGGUN LEE / lee@gunsystems.co.kr로 확인. 조직 전체 관리자 동의는 추가하지 않음.
- 같은 계정의 Entra Assigned roles에 Global Administrator 직접 할당 확인.
- 그럼에도 Graph Explorer GET 전용 사이트 `/permissions`는 403 accessDenied. 새 탭·재로그인 후에도 동일. 동의 버튼은 추가 인증 창을 열지 않음. 정확한 원인은 미확정이며 브라우저 인증 상태 문제로 단정하지 않음.
- 전용 사이트 write→fullcontrol 변경은 실행하지 않음. 운영 merge/배포 및 실제 ACL 허용·차단 검증은 미완료.
- 사용자에게 일반 Edge의 공식 Graph Explorer로 비교 확인 요청. 기존 사용자 탭은 닫지 않음.
- 임시 Graph Explorer Sites.FullControl.All 동의는 아직 남아 있음. 설정 성공 또는 작업 중단 시 회수하고 실제 목록에서 제거를 검증해야 함.
- 공식 참조: https://learn.microsoft.com/en-us/graph/api/site-list-permissions?view=graph-rest-1.0 및 site-post-permissions. 위임 설정에는 Sites.FullControl.All 외 관리자 역할·대상 사이트 멤버십도 요구됨. 현재 대표는 전역 관리자이며 사이트 접근 조회는 기존에 성공했으나 권한 목록 조회는 실패.

## Edge 비교 및 설정 완료 · 후속 기록
- 사용자가 일반 Edge InPrivate에 회사 계정으로 로그인. native CUA로 기존 해당 창만 조작. 기존 Chrome 등 다른 탭은 변경하지 않음.
- 같은 전용 사이트 GET `/permissions`가 200으로 성공, 문서 앱의 기존 write grant 1개 확인.
- 같은 사이트와 같은 앱 ID에 POST `roles:[fullcontrol]` 및 grantedToIdentities를 적용. 201 성공 후 GET 200 재조회: 같은 grant가 fullcontrol로 변경되어 1개만 존재함을 확인. 다른 사이트는 조회/변경하지 않음. PATCH는 공식 문서의 위임 미지원 표기를 확인해 사용하지 않음.
- My Apps → Graph Explorer → Manage your application → Revoke consent 실행. 화면에 `This app does not have any permissions.` 확인. 임시 Sites.FullControl.All 포함 Graph Explorer 본인 동의 회수 완료.
- 증거: outputs/workhub-site-fullcontrol-verified.jpg 및 outputs/graph-explorer-consent-revoked.jpg.
- PR23 ready 후 merge: 13a02fdb492fecc1b4ed9da18b1dc331224d8dd6. 자동 배포가 발생하지 않아 실제 Render 대기열에 새 작업이 없음을 확인하고 대시보드 Manual Deploy → Deploy latest commit 실행. dep-dbsrm261egvs73ema5ng 진행 중.
- 다음: Live 확인, 사용자 GUNSYSTEMS 관리자 로그인(요청함), 별도 문서 권한 OAuth 연결, 실제 ACL/직원 차단 및 모바일 확인. 기능 검증 완료로 기록하지 않음.

## 운영 확인 완료 · 2026-11-15 후속 기록
- v0.29.0 및 소유자 표시 보완 7326b2e 배포 Live. 관리자 별도 OAuth 연결과 실제 파일 ACL 조회 성공.
- 사용자 승인을 받아 내용 없는 Workhub_연결검증_20261114.txt만 김예린 보기 허용 후 해제. 각각 실제 ACL 재조회 성공. SharePoint 액세스 관리에서 소유자 본인·소유자 그룹만 남고 김예린은 없는 것을 확인.
- Workhub 시스템 로그에 관리자 연결, 변경 시작·단계·전후 ACL·처리자가 기록됨.
- 직접 항목 권한이 없는 사이트 관리자 표시 문제 보완. 실제 UI ‘관리자 접근 유지’ 비활성으로 확인. 관련 검사 20개 및 전체 CI 43722943918 성공.
- 직원 본인 Microsoft 로그인으로 파일 열기/거부, SharePoint 직접 변경 후 Workhub 재조회는 별도 미확인. 모바일 viewport 재검증은 도구 적용 대상이 불명확하여 완료로 판단하지 않음.
- 증거: outputs/document-permissions-live-final.jpg, outputs/sharepoint-verification-file-access-revoked.jpg. 배포 dep-dbsrt1d6e07s73an7o90.
