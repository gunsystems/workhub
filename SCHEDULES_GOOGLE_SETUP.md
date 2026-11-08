# 개인 일정 · 태그 · Google Calendar (2026-11-08)

## 사용 방법

- 개인 카드의 기본 태그 8종에서 여러 항목을 선택하고, 마지막 ‘새 태그 만들기’에서 본인 전용 태그를 추가한다. 중요도와 태그는 별개다.
- 날짜와 시간을 한국 시간으로 지정한다. 반복은 매일, 평일, 선택 요일, 매월 날짜/말일, 매월 n번째 요일, 매년 및 간격·종료일·총 회차 수를 지원한다. 시작 날짜는 선택한 규칙의 첫 회차여야 한다.
- 일정 기준은 미완료 회차를 남기고 예정 시각(또는 미리 알림 시각)에 다음 회차를 만든다. 완료 기준은 완료일에서 지정 간격 뒤에 다음 카드를 만든다. 자동 완료·자동 결재는 하지 않는다.
- 프로그램 내 알림함/조치 필요 알림에 알림을 표시한다. 브라우저 푸시나 이메일 알림은 아니다. Google 앱 알림은 기기 설정에 따른다.
- 원본 카드의 반복 중지/재시작을 지원한다. 원본을 보관/휴지통으로 옮기면 반복을 중지한다. 규칙 변경은 이후 회차에 적용하며 이미 생성된 회차는 남긴다. 횟수는 원본을 포함한다.

## Google 최초 설정

1. Google Cloud에서 프로젝트를 만들거나 선택하고 Google Calendar API를 사용 설정한다.
2. Google Auth Platform의 대상 사용자/동의 화면을 구성한다. 개인 Gmail도 연결하려면 외부 사용자를 지원해야 한다. 테스트 상태라면 연결할 Google 이메일을 테스트 사용자로 등록한다. Google 테스트 앱의 토큰 만료/검증 정책에 따라 재연결이 필요할 수 있다.
3. 웹 애플리케이션 OAuth 클라이언트를 만들고 승인된 리디렉션 URI에 아래 값을 정확히 등록한다.

   `https://gunsystems-workhub-test.onrender.com/work/calendar/google/callback/`

4. Render 서비스 환경 변수에 `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`을 저장하고 배포한다. 비밀 값은 채팅·Git 저장소에 넣지 않는다.
5. Workhub의 `/work/calendar/`에서 직원이 본인 Google 계정을 선택해 동의한다. 회사 로그인 이메일은 바뀌지 않는다. 실제 Google 동의와 전송 테스트는 이 설정 후 진행한다.

요청 권한은 `openid email https://www.googleapis.com/auth/calendar.app.created`이다. 앱이 만든 Workhub 보조 캘린더만 사용하며 기존 Google 개인 일정을 읽거나 가져오지 않는다. 카드별 기본값은 전송 안 함이다. 시간만/제목/메모 포함을 선택한다. 시간 카드는 30분, 날짜만 있는 카드는 종일 일정으로 내보낸다. 공식 업무는 최고 관리자 정책이 허용된 경우 본인 수행 업무만 별도 선택한다. 양방향 동기화나 Google에서의 업무 상태 변경은 지원하지 않는다.

연결 해제는 저장 토큰을 제거하고 이후 동기화를 중지한다. 이미 생성된 Google 일정은 남는다. 일정 기준 반복은 Google에 하나의 반복 시리즈로 전송하여 각 회차 카드와 중복되지 않게 한다.

## 운영과 호환성

- 기존 항상 실행 중인 Render 웹 서비스의 Gunicorn `post_worker_init`에서 예약 처리 스레드를 시작한다. 별도 유료 cron/worker 서비스는 만들지 않는다. DB lease와 회차/알림 고유 제약으로 중복을 방지한다. 기본 15초마다 회차/알림을 처리하고 Google 동기화는 별도 루프에서 45초마다 처리한다. 서버 중단 시에는 재시작 후 놓친 회차를 순차 처리한다.
- `WORKHUB_SCHEDULER_ENABLED=0`으로 내장 처리를 끌 수 있다. 수동 점검은 `python manage.py run_scheduled_work`를 사용한다.
- OAuth는 일회성 state, 10분 제한, PKCE를 사용한다. refresh token은 Fernet으로 암호화한다. 기본 키는 기존 SECRET_KEY에서 파생되므로 이를 바꾸면 Google 재연결이 필요하다. 별도 `GOOGLE_TOKEN_ENCRYPTION_KEY`를 지정할 수도 있다. 인증 코드가 access log에 남지 않도록 Gunicorn은 query string을 기록하지 않는다.
- tasks.0014는 기존 카드 라벨을 태그로 복사하고 원래 데이터와 기존 날짜 단위 반복을 보존한다. 기존 반복은 편집·저장 시 새 반복 설정으로 전환한다. 관리자 인증/OTP/직원 계정을 변경하지 않는다.
- 검증: 전체 176개 테스트 통과(21개 신규 포함), migration drift 없음. 실제 Google API 전송은 OAuth 클라이언트 설정 후 확인해야 한다.
