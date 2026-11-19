# Teams 개인 알림 연결

업무 배정, 결재·신청 대기, 처리 결과를 회사 Teams 개인 채팅으로 전달한다.
기존 Workhub 내부 알림·하이웍스·메일은 변경하지 않는다. 미팅 생성 앱 및 문서관리 앱과 별도의 회사 전용 알림 앱을 사용한다.

## 구현 범위

- 일반 업무: 책임자·공동 수행자 배정, 현재 단계 검토자 대기, 최종 완료·보완 요청 결과.
- 업무 이관: 현재 차례 결재자 대기, 요청자·인계자·인수자의 처리 결과. 실제 담당자 교체 시 배정 알림도 기존 배정 경로를 사용한다.
- 간단 업무: 배정, 결과 회신·확인 완료·추가 확인 요청·취소.
- 휴가·품의·증명서·서류: 현재 결재자/발급 담당자의 대기 알림, 신청자의 처리 결과.
- Teams 카드의 확인 버튼으로 Workhub를 연다. Teams 안에서 직접 승인하지 않는다.
- 회사/개인 계정 혼동을 방지하기 위해 회사 테넌트의 개인 채팅만 연결한다.
- 직원이 본인 Workhub에서 10분짜리 일회용 연결 코드를 발급하여 회사 Teams의 알림 앱에 입력한다. 서버에는 코드 해시만 보관한다. 기존 Microsoft 본인 확인 연결이 있으면 동일한 Microsoft 사용자만 허용한다.
- 개인별 수신 및 종류별 선택. 기본값은 수신 꺼짐. 설정 전 과거 알림을 일괄 발송하지 않는다.
- 신청 사유·금액·날짜·첨부파일·업무 제목 등 민감한 본문은 Teams에 보내지 않는다.

## Microsoft 관리자 설정 (아직 적용 전)

1. 회사 관리자 계정으로 Azure 포털에 로그인한다. 개인 Microsoft 계정과 구분한다.
2. Microsoft Entra ID → 앱 등록 → 새 등록: 이름 `GUNSYSTEMS Workhub Notifications`, 이 조직 디렉터리만(단일 테넌트). 브라우저 OAuth 복귀 주소는 필요 없다.
3. 개요의 디렉터리(테넌트) ID와 애플리케이션(클라이언트) ID를 확인한다.
4. 인증서 및 비밀 → 새 클라이언트 비밀을 직접 생성하여 Value를 Render 비밀 환경변수에 저장한다. 채팅·저장소·스크린샷으로 공유하지 않는다. 만료 전 교체 일정을 관리한다.
5. 같은 회사 Azure 구독에서 Azure Bot을 등록한다. 무료 F0 요금제 사용 가능 여부와 화면의 비용을 확인한다. 유료 서비스는 별도 동의 없이 생성하지 않는다. 새 웹 호스팅/App Service를 만들 필요가 없다. Single Tenant, 위 앱의 ID와 테넌트를 지정한다. Graph 채팅 읽기/전송, 전체 사이트 권한은 추가하지 않는다.
6. Azure Bot → Configuration → Messaging endpoint:
   `https://workhub.gunsystems.co.kr/work/teams/messages/`
7. Channels → Microsoft Teams를 활성화한다. 이 단계의 Microsoft 약관/정책 동의는 실행 직전에 확인한다.
8. Render 기존 Workhub 서비스에 아래 값 4개를 저장하고 배포한다. 설정 완료 전 활성화하지 않는다.

| 변수 | 값 |
|---|---|
| `TEAMS_NOTIFICATION_TENANT_ID` | 위 회사 테넌트 ID |
| `TEAMS_NOTIFICATION_APP_ID` | 새 알림 앱의 클라이언트 ID |
| `TEAMS_NOTIFICATION_CLIENT_SECRET` | 직접 생성한 비밀 Value |
| `WORKHUB_TEAMS_NOTIFICATIONS_ENABLED` | 준비·정책 확인 후 `1`, 즉시 중단은 `0` |

9. Workhub → Teams 알림 관리 → Teams 앱 설치 파일 다운로드. Teams 관리 센터 → Teams 앱 → 앱 관리에서 회사용 앱 패키지로 업로드한다. 처음에는 대표 본인에게만 설치/사용을 허용한다. 앱 정책 때문에 보이지 않으면 관리 센터의 앱 사용 가능 대상 및 사용자 지정 앱 허용 정책을 확인한다. 전사 자동 설치는 별도 승인 후 진행한다.
10. 대표 본인이 Teams 알림에서 연결 코드를 발급하고 Teams 회사 계정의 `Workhub 알림` 개인 채팅에 `연결 <코드>`를 입력한다. Workhub의 연결 완료 확인에서 연결됨을 확인하고 수신을 켠다.
11. 본인 대상 검증용 업무와 본인 결재만 별도 동의 후 생성하여 실제 배정/대기/결과, 링크 로그인, 중복, 수신 끄기를 검사한다. 다른 직원 알림은 이 검증에 사용하지 않는다. 실제 외부 발송은 Microsoft 설정 후에만 검증 가능하다.

관리자 화면: `/work/teams/manage/`, 직원 설정: `/work/teams/`.

## 발송·보안·장애 처리

- 저장 트랜잭션에서 Teams 대기 기록만 생성한다. 발송은 기존 스케줄러의 독립 단계에서 최대 5건씩 처리한다. Teams 장애로 다른 알림 단계를 중단하지 않는다.
- 업무·알림 이벤트/수신자별 고유 키로 중복 등록 방지. 여러 실행자의 claim은 조건부 DB 업데이트로 한 번만 획득한다.
- 발송 직전 활성 직원·인증된 이메일·회사 이용 조건·본인 연결·수신 종류·현재 접근 권한·결재 순서를 다시 확인한다. 퇴사/차단/담당 변경·처리 완료·오래된 알림은 제외한다.
- 설정 변경/재연결/연결 해제 시 이전 연결 세대의 대기 알림을 무효화한다. Teams 앱 제거 이벤트 또는 403/404/410이면 수신을 중지한다.
- Microsoft RS256 서명, 고정 issuer/JWKS, 대상 앱, 유효기간, Teams 채널 키 endorsement, 서명된 service URL 일치 여부, 회사 테넌트, 개인 scope, 봇 수신자를 모두 확인한다. HTTPS `smba.trafficmanager.net`만 외부 발송 대상으로 허용한다. 검증 우회 옵션은 없다.
- 채팅 본문을 저장하지 않는다. 코드 재사용·만료·다른 직원의 Microsoft 본인 연결 교체·두 Workhub 사용자의 같은 Teams 연결을 거부한다.
- 429만 제한적으로 재시도한다. 타임아웃/5xx/중단된 발송은 접수됐을 수 있어 `unknown`으로 남기고 자동 중복 전송하지 않는다. 기록에 실패 본문이나 비밀 값을 남기지 않는다.
- 관리자 화면과 시스템 로그에 발송 상태 메타데이터를 남긴다. `sent`는 Microsoft의 접수 완료이며 직원의 열람 확인을 뜻하지 않는다.
- 본인 연결/단일 수신 테스트가 끝나기 전 전사 확대 금지. Teams 자체 푸시 허용/방해 금지/앱 음소거는 직원의 Teams 설정에도 영향을 받는다.

## 근거 (Microsoft 공식)

- [개인 사전 알림 및 앱 설치 요건](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)
- [Bot Connector REST 인증 전체 검증 규칙 및 단일 테넌트 토큰](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication?view=azure-bot-service-4.0)
- [Azure Bot 등록](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration?view=azure-bot-service-4.0)
- [Teams 앱 manifest](https://learn.microsoft.com/en-us/microsoftteams/platform/resources/schema/manifest-schema)

이 구현은 폐기된 Bot Framework SDK나 레거시 Office 365 Incoming Webhook을 사용하지 않고 위 공식 REST 프로토콜과 PyJWT 검증을 사용한다. 별도 FastAPI 서버나 봇용 유료 호스팅을 추가하지 않는다.
