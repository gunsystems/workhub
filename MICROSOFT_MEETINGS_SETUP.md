# Teams 자동 생성 연결

## 연결 범위

Workhub의 미팅 등록에서 주최자 본인의 회사 Microsoft 계정으로 Teams 회의를 생성합니다. Microsoft Graph의 일정 API를 사용하므로 해당 회사 계정의 Exchange 일정과 Teams 회의 생성 권한이 필요합니다. 메일 MX 이전은 필요하지 않습니다. Microsoft에서 발송하는 회의 초대의 실제 수신·스팸 여부는 현재 회사 발신 도메인 설정과 함께 점검합니다.

미팅 주최자가 한 번 계정을 연결합니다. 참석자에게 Workhub 로그인 계정이나 Microsoft 관리자 권한을 새로 부여하지 않습니다. 외부 참석자는 Microsoft 회의 초대만 받으며 내부 Workhub 자료에 접근할 수 없습니다.

## 관리자 설정

1. 회사 계정으로 Microsoft Entra 관리 센터에 접속합니다. 개인 Microsoft 계정과 구분합니다.
2. 앱 등록 → 새 등록. 이름 예: `GUNSYSTEMS Workhub`. 지원 계정은 **이 조직 디렉터리의 계정만** 선택합니다.
3. 리디렉션 플랫폼은 **웹**, URI는 아래 값입니다.

   `https://workhub.gunsystems.co.kr/work/meetings/microsoft/callback/`

4. Microsoft Graph **위임된 권한** `User.Read`, `Calendars.ReadWrite`를 확인합니다. 로그인 요청에 `offline_access`도 포함해 연결을 유지합니다. 조직 정책에 따라 관리자 동의를 완료합니다. 전체 사용자 일정에 접근하는 애플리케이션 권한은 추가하지 않습니다.
5. 개요의 디렉터리(테넌트) ID와 애플리케이션(클라이언트) ID를 확인합니다.
6. 인증서 및 비밀에서 클라이언트 암호를 생성하고 **값**을 Render에 직접 저장합니다. 비밀 ID가 아닙니다. 값과 다운로드한 인증 파일은 채팅·GitHub에 넣지 않습니다. 만료일도 운영 기록에 보관합니다.
7. Render 환경 변수:

   | 항목 | 내용 |
   |---|---|
   | `MICROSOFT_TENANT_ID` | 회사 테넌트 UUID |
   | `MICROSOFT_CLIENT_ID` | 등록한 앱의 클라이언트 UUID |
   | `MICROSOFT_CLIENT_SECRET` | 클라이언트 암호 **값** |
   | `MICROSOFT_TOKEN_ENCRYPTION_KEY` | 선택. 미설정 시 서버 SECRET_KEY에서 별도 키를 파생합니다. 변경 시 재연결이 필요합니다. |

8. 저장·재배포 후 Workhub → 계정 메뉴 → Teams 연결 또는 미팅 등록의 Teams 연결 설정을 엽니다.
9. 주최자는 Workhub와 같은 회사 이메일의 **회사 또는 학교 계정**으로 로그인·동의합니다. 연결 시 계정 이메일과 Teams 회의 제공자 지원을 확인합니다.

## 검증 순서

실제 회의 초대 발송 전 대상과 시간을 확인합니다.

1. 주최자 본인만 포함하는 확인용 미팅을 등록하고 Teams 자동 생성을 선택합니다.
2. Workhub의 Teams 연결 완료 표시와 참여 버튼, 주최자 Microsoft 일정의 동일 회의를 확인합니다.
3. 시간·제목 변경이 새 회의를 중복 생성하지 않고 같은 회의를 갱신하는지 확인합니다.
4. 내부 참석자 1명을 명시적으로 추가하고 Workhub 알림·메일 및 Microsoft 회의 초대를 확인합니다.
5. Workhub 참석 응답과 Outlook 회의 초대 응답은 별개입니다. 현재 서로 자동 합산하지 않습니다.
6. 취소 후 Microsoft 일정 취소와 참석자 안내를 확인합니다. Teams 미팅의 휴지통 이동은 취소 연결 완료 후 허용합니다.
7. Google 일정은 자동으로 추가 생성하지 않습니다. 기존 Google·하이웍스 구독 및 별도 초대 일정과의 중복 표시는 사용 중인 캘린더에서 확인합니다.

## 실패·보안

- refresh token은 서버에서 암호화하며 네트워크 응답·토큰·원본 오류 본문을 감사 로그에 저장하지 않습니다.
- OAuth에는 단일 회사 테넌트, 사용자·시간·state 확인, PKCE를 적용합니다.
- 외부 Graph 호출은 고정 Microsoft 호스트만 사용하고 리디렉션을 따라가지 않습니다.
- Workhub에 보관한 생성 이벤트 ID만 수정·삭제합니다. 사용자의 다른 일정을 탐색·수정하지 않습니다.
- 생성 transactionId를 고정합니다. 결과 불명은 자동 재발송하지 않고 주최자 확인 후 같은 식별자로 재시도합니다.
- 처리 중에는 시간 변경·취소를 막습니다. 생성 결과 불명이며 이벤트 ID도 없으면 연결 결과를 먼저 복구해야 합니다.
- Teams로 연결된 미팅이 남아 있으면 연결 해제를 제한합니다. 만료·동의 철회·비밀 만료는 재연결이 필요할 수 있습니다.

## 공식 근거

- https://learn.microsoft.com/en-us/graph/outlook-calendar-online-meetings
- https://learn.microsoft.com/en-us/graph/api/user-post-events?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/event-update?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
