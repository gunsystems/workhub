## v0.13.0 · 배정 확인과 업무 처리 안내

명시적 배정 확인 후 최소 1%를 반영하고 확인 전 보고·완료를 차단합니다. 공동 수행자별 배정 시각·확인 대기·경과 시간, 단계별 안내와 완료 버튼 명칭을 정리했습니다. 기존 방식 업무도 같은 확인 절차를 적용합니다. 434개 검사와 요청·첨부 스크립트 통과, 백업 및 운영 배포 확인. 실제 로그인 화면 검증은 대기 중입니다.

## v0.12.7 · 평가 기준 월 정렬

기본 월 선택기를 유지하면서 날짜 표시를 왼쪽·세로 가운데 정렬합니다. 월 변경·초기화·화면 복원 시 표시도 갱신합니다. 월 표시 스크립트 검사 및 기존 431개 검사 통과. 운영 배포 완료, 실제 iOS 육안 확인 대기.

## v0.12.6 · iOS 날짜 입력과 보드 제목 보완

날짜·시간·월 입력 컨테이너의 폭과 네이티브 입력 스타일을 정리하고 보드 목록 제목·건수·설정 버튼을 분리했습니다. 기존 431개 검사와 요청·첨부 스크립트 통과. 운영 배포 완료(2026-11-11 07:20 KST), 새 버전 및 정상 상태 확인. 로그인된 업무 화면과 실제 iOS 최종 검증은 대기 중입니다. 전체 부서·전체 직원 문구, 프로젝트 입력 및 보고 조회 날짜도 함께 반영합니다.

## v0.12.5 · 모바일 조회와 상세 화면 개선

모바일 필터 높이·날짜 입력칸과 버튼 배치를 정리하고 일정 요약에 등록일·현재 시작일·마감일을 표시합니다. 작성자 부서·이름 표시, 결재라인 안내 및 단계 표시, 자료함 여백을 통일합니다. 기존 감사 기록과 초기 계획은 유지합니다. 431개 검사 및 요청·첨부 스크립트 통과. 운영 배포 완료, 로그인 세션 만료로 화면 검증은 대기 중입니다.

## v0.12.4 · 업무 접수함 탭 디자인

보낸 요청·받은 요청·전체 요청을 카드형 탭으로 구분하고 선택 상태·설명·아이콘·키보드 초점을 정리했습니다. 기존 430개 검사와 요청·첨부 스크립트 검사 통과. 운영 화면에서 세 탭 전환 및 받은 요청·전체 요청의 현재 선택 표시 확인.

## v0.12.3 · 진행 상태 모두 조회

업무 현황에 모두 조건을 추가했습니다. 완료·취소를 포함하며 기존 조회 권한과 기본 진행 업무 조건은 유지합니다. 전체 430개 검사와 요청·첨부 스크립트 검사 통과, 운영 화면에서 선택 상태 및 완료·취소 행 확인.

# v0.11.1 · 2026-11-10

## v0.12.2 · 드래그 앤 드롭 첨부와 지연 등록 판정 수정

전용 첨부 영역에서 선택·업로드 중·완료 상태를 구분합니다. 당일 업무 작성 시간이 길어져 지연으로 표시되던 오류를 수정하고 기존 잘못된 표시를 최초 계획 기준으로 정정합니다. 전체 429개 검사와 첨부 스크립트 검사 통과.

## v0.12.1 · 기존 업무 자료 관리 호환

구형 업무 상세에도 업무자료를 표시하고 저장소 인증 설정 오류를 구체적으로 안내합니다. 실제 공유 드라이브 자원과 업로드 권한을 확인합니다. 전체 424개 검사 통과.

## v0.12.0 · 업무자료와 공유 드라이브

업무·요청·프로젝트별 파일·링크 등록, 요청 작성 시 참고 파일 첨부, 보고서 신규 첨부 Drive 저장. 다운로드 권한 확인과 자료 보관 이력, 관리자 연결 확인을 추가했습니다. 세부 사항은 releases/v0.12.0.md를 참조하세요.
계정 전환 로그인에서 이전 인증 세션이 남아 잘못된 OTP 등록 화면으로 이동하는 오류 수정. OTP 화면 계정 표시·다른 계정 로그인·혼합/만료 대기 상태 정리.

# v0.11.0 · 2026-11-10
업무 등록 경로 구분, 직접 업무 요청, 팀 간 협조·엔지니어 지원 접수·협의·인력 배정·결과 확인. 자세한 내용은 releases/v0.11.0.md 및 테스트 가이드·참고 사례 문서.

# Workhub v0.10.0 일정 준수 관리 · 마감 알림

2026-11-10 · Asia/Seoul

## 변경
- 업무 현황·캘린더·상세에서 일정·마감 관리로 이동. 새 사이드바 메뉴는 추가하지 않음.
- 신규 등록·초안 공개 시 실제 등록 시각·최초 일정 보존. 시작 후 등록을 지연 등록으로 표시하며 기존 업무 등록 시각은 소급 추정하지 않음.
- 시작일·시작 시간·수행 시작/종료·마감 변경 시 사유 필수, 변경 전후·변경자·시각 및 최근 30일 변경 횟수 표시.
- 수행자별 참석/실시·불참/미실시·연기·사유 설명. 실제 실시일 입력과 사유 필수. 관리자 확인 의견 및 직원 정정 시 재확인, 과거 설명은 업무 이력에 보존. 근태·고의성 자동 판정 없음.
- 관리자·팀장이 등록 대상 일정과 담당자·등록 기한을 지정. 직원은 실제 업무 연결, 관리자는 사유를 남겨 면제 가능. 비공개 개인 보드 제외.
- 마감 3일 전·당일·지연 시 내부 알림. 결재는 현재 단계 결재자와 별도 검토 기한 기준. 지속 지연은 직속 상위자에게 안내.
- 마감 알림 기본 꺼짐. 특정 직원 수신 범위, 사전 일수·시각·상위자 안내 일수, 토·일요일 보류, 이메일·메신저 선택.
- 이메일 직원별 일일 요약, 중복 키·발송 상태 기록. 날짜·담당자·업무 상태 변경 시 대기 발송 재검증. 불명확한 메일 결과 자동 재시도 안 함.
- 이전 v0.9.11 Google 캘린더 날짜/표시 내용 분리 및 선택 미리보기 포함.

## 검증
- 전체 Django 회귀 검사 364개 통과. 신규 일정·마감 검사 18개 포함. JavaScript Google 미리보기 선택 갱신 검사 통과.
- Windows 테스트 실행 중 운영체제 예외 진단 문구가 출력됐으나 테스트 프로세스는 정상 종료·364개 통과. 운영 Linux에서 migration 및 추가 검증 예정.
- migration 일치 검사 통과. 운영 실제 메일·메신저 시험 발송은 수행하지 않음.
- migration 0027(기존 Google 설정 auto 기본값 보존), 0028(일정 관리·알림 테이블 신규 생성).
- 운영 백업 /var/data/backups/workhub-pre-v0100-20261110T061650Z.sqlite3, integrity_check=ok. 원본 업무·계정·비밀번호·OTP·Google 연결 정보 수정 없음.
- 운영 배포 대기.

# Workhub v0.9.11 Google 캘린더 등록 설정 개선

2026-11-10 · Asia/Seoul

- 등록 날짜(마감일 하루 / 수행 시간 또는 업무 기간)와 표시 내용(업무 일정 / 제목 / 제목과 설명)을 분리.
- 페이지·등록 버튼은 Google 캘린더에 등록으로 통일. 선택 결과 미리보기 추가.
- 기존 내보내기는 date_mode=auto로 보존해 시간 일정/마감일 표시 동작 유지. 이전에 열린 폼도 지원.
- 신규 migration 0027_calendarexport_date_mode는 설정 필드 추가만 수행. 계정·토큰·원본 업무는 변경하지 않음.
- 도움말을 새 표현과 일치하도록 정리.
- 운영 반영 전: 배포 브라우저 연결 시간 초과로 운영 백업·배포 대기. 현재 운영 버전은 v0.9.10.

# Workhub v0.9.10 취소 일정 한 줄 표시

2026-11-09 · Asia/Seoul

- 취소 일정은 캘린더에서 취소 - 업무명 한 줄로 표시. 담당자·분류·우선순위 보조 줄 생략.
- 회색 배경·점선 테두리와 상세 화면 접근 유지. 긴 제목은 말줄임.
- 하이웍스 제목과 구독 동작은 변경하지 않음. DB migration 없음.

# Workhub v0.9.9 취소 업무 표시

2026-11-09 · Asia/Seoul

- 월·주·일 캘린더의 취소 업무에 취소 배지, 제목 취소선, 회색 배경과 점선 테두리를 적용. 범례에 취소 추가.
- 신규·이전 방식 업무 상세에 취소 안내 추가. 원본 이력 보존.
- 하이웍스 iCalendar 제목에 [취소] 접두사 전달. 원본 제목과 UID는 유지하고 STATUS:CANCELLED도 유지.
- 실제 하이웍스가 취소 일정을 유지하는 경우 제목으로 구분 가능하도록 보완. 외부 갱신 시점과 표시 방식은 하이웍스에 따름.
- DB migration 없음. 실제 업무·계정·구독 설정 변경 없음.

# Workhub v0.9.8 하이웍스 갱신 안내

2026-11-09 · Asia/Seoul

- 공통 하이웍스 도움말과 일정 연결 페이지에 제목·날짜·시간 수정의 자동 갱신 설명 추가.
- 기본 약 5분 후 반영되며 갱신 상황에 따라 더 오래 걸릴 수 있다고 안내.
- 재등록·연결 주소 재발급 불필요 안내. 사용자 화면에 테스트 표현 없음.
- 기존 일정과 계정·연결 설정은 변경하지 않음. DB migration 없음.

# Workhub v0.9.7 하이웍스 일정 변경 정보 보완

2026-11-09 · Asia/Seoul

- 이전 방식 업무 수정 시 revision 증가 누락 수정.
- 구독 일정 SEQUENCE에 기존 변경 이력 수를 포함하여 이미 수정된 업무도 데이터 재저장 없이 변경 번호 제공. 참여자 조인의 중복은 distinct로 제외.
- LAST-MODIFIED에 마지막 업무 이력 시각 전달. UID 유지.
- 제목 변경 전후 UID 유지·수정 번호 증가·기존 revision 0 업무 호환 및 접근 제한/토큰 폐기/취소 표시 등 관련 테스트 21개 통과.
- 업무/구독 주소/하이웍스 설정 변경 및 DB migration 없음.
- 하이웍스는 읽기 전용 구독이므로 개별 일정 수정/삭제는 Workhub에서 처리. 실제 하이웍스 재수집 시점과 화면 반영은 별도 확인 필요.
- 운영 배포 확인 대기.

# Workhub 개발 변경 이력

운영 조사: [2026-11-09 직원 가입 500 분석](investigations/2026-11-09-signup-500.md). 실패 단계와 등록 완료 상태 확인, 동시 DB 쓰기 충돌 가능성 재현. 상세 예외 로그 부재로 근본 원인 미확정이며 이 조사에서 코드 수정/배포는 하지 않음.

기준: 2026-11-09 · 시간 Asia/Seoul. 과거 버전은 당시 화면/문서에서 확인된 v0.5를 기준으로 하며, 그 이전 커밋에 임의의 의미 버전을 소급 부여하지 않는다. 아래 Git 커밋은 코드 변경 근거이며 실제 배포 성공을 뜻하지 않는다. 운영 검증 근거는 DEVELOPMENT_STATUS.md의 deploy ID/확인 기록을 함께 본다.

## v0.9.6 — 하이웍스 도움말 캡처 선명도 개선 (운영 배포 완료)

- 고밀도 화면에서 다시 캡처한 1000×900 무손실 PNG로 교체. 이미지 클릭 시 원본을 새 탭에서 열기.
- 개인정보와 실제 연결 주소가 없는 빈 설정창만 보관. 상세 releases/v0.9.6.md.

## v0.9.5 — 업무 처리·확인 항목 배치 및 i 설명 (운영 배포 완료)

- 기본 처리와 일정/관리 기능을 확인 항목보다 앞에 배치. 확인 항목 추가는 목록 뒤에 배치하고 필수/선택·확인 상태를 구분.
- 제목 옆 i 아이콘의 마우스/키보드 설명. 기존 권한·완료 조건 유지. 상세 releases/v0.9.5.md.

## v0.9.4 — Google 연결 화면 상단 바로가기 제거 (운영 배포 완료)

- Google Calendar 화면 제목 위의 중복 하이웍스 버튼 제거. 계정 메뉴와 실제 연결 설정은 유지. 상세 releases/v0.9.4.md.

## v0.9.3 — 하이웍스 일정 연결 캡처 도움말 (운영 배포 완료)

- 실제 설정창 캡처와 숨겨진 ⊕ 메뉴·입력 항목·저장/체크 순서를 연결 화면/도움말/전체 가이드에 추가. 실제 일정·연결 주소는 이미지에 포함하지 않음.
- 관련 검사 22개 통과. 사용자가 하이웍스 실제 연동 확인. 상세 releases/v0.9.3.md.

## v0.9.2 — 공통 화면 정렬·간격 정리 (운영 배포 완료)

- 보고·업무·직원·캘린더·로그의 조회 조건 높이/라벨 정렬 통일, 박스 최소 높이 제거와 간격·폭 정리.
- 관련 검사 39개, HTML 템플릿 컴파일 55개 및 새 테스트 탭 보고 목록 시각 확인. 상세 releases/v0.9.2.md.

## v0.9.1 — 공지 메일 디자인·문구 정리 (운영 배포 완료)

- 공지 제목·작성자·본문 중심으로 메일 배치 정리. 큰 배너와 불필요한 도착·자동 발송·로그인 안내 제거.
- 공지 검사 20개 및 HTML 렌더링 확인. 실제 재발송 없음. 상세 releases/v0.9.1.md.

## v0.9.0 — 보고 구분·배정 알림·상위자 자동 연결 (운영 배포 완료)

- 일일·주간·월간·개별·기간 보고 구분, 날짜 제안·기존 입력 보존·제출 버전 기록.
- 업무 배정 이메일·하이웍스 메신저 개별/동시 선택. 반복 회차·인계에도 적용.
- 미지정 직속 상위자를 같은 부서의 유일한 팀장/본부장에 자동 연결. 수동 지정 보존.
- 업무 등록과 접수함 안내 및 기존 도움말 보강. 교육자료는 최종 단계로 보류.
- 전체 343개 검사 및 JavaScript 날짜 제안 4개 통과. 문서 저장소는 검토안이며 미구현. 상세 releases/v0.9.0.md.

## v0.8.1 — 메일 회신 안내 문구 제거 (운영 배포 완료)

- 업무 배정 메일·공지 메일의 ‘메일에 회신하는 대신…’ 안내 제거. 양식·확인 버튼 유지.
- 관련 검사 2개 및 HTML 직접 렌더링 검사 통과. DB·업무 접수함 변경 없음. 상세 releases/v0.8.1.md.

## v0.8.0 — 등록 기본값·필수 표시·공지사항·로그 개선 (운영 배포 완료)

- 새 등록 시작일·시작 시간 기본값, 기존/재입력 보존, 선택 시간과 필수 입력 배지 및 조건부 안내.
- 수행자 표 ‘진행 상태’·‘진행률’ 명칭 정리. 변경 로그는 실제 차이만 비교하고 접속·발송은 처리 결과로 표시, 업무/직원 이름 사용.
- 전사·부서·직원 대상 공지 작성·확인·즉시/예약, 이메일·하이웍스 메신저 선택, 직원별 전달·열람 결과와 예약 취소.
- 전체 321개 검사 통과. DB 0023/0024 추가, 사전 백업 확인. 실제 공지 수신은 미검증. 상세 releases/v0.8.0.md.

## v0.7.2 — 직원 재직 확인·재시도 경로 복구 (운영 배포 · 사용자 저장 확인 대기)

- 오래된 하이웍스 캐시를 업무 진입 시 재조회, 백그라운드 인증 갱신을 정기 처리보다 먼저 실행.
- 직원 확인 차단 HTML의 same-origin Referrer 정책으로 재확인·로그아웃 CSRF 충돌 방지.
- 오류 복귀 GET이 POST 전용 주소에서 405로 막히지 않도록 대시보드로 안전하게 이동. CSRF·직원 자격·OTP 검사는 유지.
- 실제 직원 조회 복구 및 이후 자동 갱신 확인. 최초 갱신 공백의 세부 원인은 아직 미확정. 사용자 실등록·메일 수신은 확인 대기.

## v0.7.1 — 부서 간 프로젝트 업무 배정 (운영 배포 완료)

- 프로젝트 주관 부서와 담당자의 부서가 다르다는 이유로 업무 등록을 막던 검사를 제거. 프로젝트 조회·선택 범위와 기간 검사는 유지.
- 대표·본부장은 부서와 관계없이 직원에게 배정할 수 있고, 일반 직원도 본인에게 공개된 프로젝트에서 타 부서 직원과 공동 수행할 수 있음.
- 타 부서 프로젝트에 실제 업무로 참여한 직원은 해당 프로젝트를 조회·선택할 수 있음. 프로젝트 상세의 업무 목록은 기존 업무 조회 권한으로 계속 필터링.
- 임원 업무 권한은 계정 관리 권한과 별개. 기존 계정·직원 직책·소속·업무를 변경하거나 테스트 메일을 보내지 않음.

## v0.7.0 — 업무 배정 메일 · 30분 무활동 로그아웃 · 시스템 로그 (운영 배포 완료)

- 신규 배정·새 공동 수행자·담당자 인계·반복 업무 회차에 회사 양식 HTML 메일을 예약. 업무명·역할·마감일·배정자와 업무 열기 버튼. 사용자 시안 확인 완료, 실제 테스트 메일은 미발송.
- 업무 저장 트랜잭션과 메일 예약을 함께 저장하며 메일 API 호출은 기존 백그라운드 작업에서 처리. 동일 배정 중복 방지, 전송 직전 재직·인증·현재 배정 검사. 발송 결과가 불명확하면 자동 재발송하지 않음.
- 서버가 30분 무활동을 판정하고 세션·OTP 로그인 상태를 종료. 브라우저 클릭·입력·스크롤·포인터 활동을 15초 단위로 반영하며 백그라운드 조회·캘린더 동기화는 활동에서 제외. 다른 탭의 서버 활동 상태를 함께 확인.
- 관리자 전용 시스템 로그: 처리자·시각·대상·생성/변경/삭제·변경 전후, 접속·만료·배정 메일 결과 조회. 삭제된 업무·직원과 관계없는 보존 기록. 개인 메모 내용·초안·비밀번호·OTP·인증 토큰 제외.
- DB 추가 migration 0022. 기존 업무나 과거 배정에 메일을 소급 발송하지 않음. 과거 로그도 새로 만들어내지 않음.
- 전체 282개 검사 통과. dep-dbon52bl550s73a9on40 LIVE, 16:14:15 KST. 실제 도메인 health 정상, 기존 업무·직원·인증 수량 보존 및 소급 메일 0건 확인. 실제 수신 테스트는 사용자 진행 예정. 상세는 releases/v0.7.0.md.

## v0.6.1 — 하이웍스 일정 연결 오류 수정 (운영 배포 완료)

- 페이지별 Referrer 정책 차이로 HTTPS 로그아웃이 403 거부되는 조건 수정. 구독 설정 HTML은 same-origin, 토큰 포함 iCal 응답은 no-referrer 유지.
- 기존 CSRF·POST 로그아웃·외부 Origin 차단 유지. DB/계정/권한/구독 주소 변경 없음.
- 연결 주소 발급도 같은 정책 충돌로 실패하는 조건 수정. HTTPS 발급/표시/iCal/폐기 및 로그아웃 등 관련 40개 검사 통과. 메뉴·제목·도움말은 ‘하이웍스 일정 연결’로 통일하고 단방향·최초 연결·본인 수행 범위를 명시.
- 수정 전 재현 및 운영 결과는 releases/v0.6.1.md에 기록한다.
- 운영 소스 0c44b70308be9f549bc1d9604a276589769e08d2 / dep-dboktgbl550s73a2pufg LIVE (2026-11-09 13:41:27 KST). 서버 정상·실서버 설정 view 정책/명칭 확인. 실제 하이웍스 수신은 아직 미검증.

## v0.6.0 — 업무·일정 통합 및 정기 보고 (운영 확인 완료)

- 업무 종류와 사용자 정의, 보고 업무/간단 일정, 실제 수행시간·장소·근무 형태·고객사·예상시간·다음 조치.
- 대표 직원 업무 직권과 계정 운영 권한 분리, 본인 수행 업무 직권 승인 차단.
- 보고 업무의 반복 템플릿·독립 회차·미래 예정·중지·중복 방지·사용중지 담당자 확인.
- 주간/기간 보고·기존 업무 연결·제출 버전·팀장 취합·지정 검토자 승인/보완, 비공개 저장 첨부.
- 본인 수행 업무 iCal 구독 주소 발급/폐기, 최소정보·권한 범위·사용중지 차단.
- 신규 마이그레이션: tasks 0018~0021. 기존 인증정보·업무·개인 보드 삭제 없음.
- 검증: 전체 247개 자동 검사 통과. 정적 파일 생성·마이그레이션 정합성 확인. 배포 상태는 releases/v0.6.0.md에 실제 소스/배포 ID로 기록한다.
- 배포: 최종 소스 [0b729a7](https://github.com/gunsystems/workhub/commit/0b729a7170118f7ce773a41f6daa89807668184a), Render dep-dbokljpsrm7s73c9d6u0 LIVE (2026-11-09 13:24:39 KST). 서버 정상 응답과 새 화면, 기존 관리자·직원·업무 보존 확인. 후속 관련 검사 42개 통과.
- 제한: 템플릿 이후 회차 일괄 수정은 중지 후 새 업무에서 재설정. 휴일 이동 자동화, 반복 미래회차의 메인 달력 통합, 상세 시간표 보기 및 보고 의무 템플릿 기반 미제출 판정은 후속 개선. 하이웍스 실제 최초 구독과 공급자 갱신 주기는 별도 확인.

## v0.5 — 기존 운영 기능 누적 이력

| 구간 (한국 날짜) | 주요 변경 | 근거 커밋 | 검증 기록 |
|---|---|---|---|
| 2026-11-06 · 초기 기반 | 캘린더·역할/부서 권한·진행보고/검토·프로젝트·업무접수·조치 필요 | 569b9721, 077f9ec4, 27bf9424, 7be6e912 | 이후 누적 회귀 검사에 포함 |
| 2026-11-07 · 인증/메일 | 회사 이메일 초대·인증·OTP, 하이웍스 HTTPS 발송, HTML 초대/재설정 메일, HTTPS CSRF 수정 | cc036d6d, 78776237, 0679c97d, 94d16246, 14a8f42f | 당시 76/81/122개 등 커밋 메시지 및 개발 기록 참고 |
| 2026-11-07 · 계정 운영 | 직원 중지/재활성화·이력 보존 삭제, 관리자 지원 OTP 복구, 관리자 역할 분리 | 2ca8e3eb, ae60bb47 | 당시 93/117개 통과 기록 |
| 2026-11-07 · v0.5 | 공동/개별 배정, 순차·병렬 결재, 회수/보완/인계/직권, 비공개 개인 보드 | 7b1c9212 | 147개 통과 기록, d0256e19 운영 확인 기록 |
| 2026-11-08 · 개인 일정 | 태그·중요도·반복 날짜/시간·알림·Google 선택 전송, 보드 설정 취소/정렬 | 49a5ea33, a4e801f7, 1a77631d | 커밋별 자동검사 및 운영 확인 기록 |
| 2026-11-08 · 브랜드/도메인/도움말 | GUNSYSTEMS/Workhub 표기, 회사 도메인, 페이지별 시각 도움말, 명확한 Google 전송 표현 | 73aa8d4a, cfe7895b, aef9af18, a45b8835, c4e7c1a1, 20f40459 | DEVELOPMENT_STATUS.md 및 기존 증거 이미지 |
| 2026-11-08~09 · 조직/접근 | 팀·전사 현황/밀집 캘린더, 하이웍스 직원/메신저, 인증된 직원 자동 연결, 회사 재직 자격 검사 | 90db3c1d, 0255f5a1, 25883e92, ebb1d8ea | 당시 자동검사 및 운영 연동 기록 |
| 2026-11-09 · 직원 관리 | 실제 하이웍스 직원 선택, 정보 등록/초대 분리, 직속 상위자/임원 권한 표시, 부서 보존 | 8635960f, 6f61fc31, 07795c43, 0351e14d, 5e6fb732 | 225개 및 직원 UI 관련 검사 기록 |
| 2026-11-09 · UI/개발 기준 | 관리자 정렬·필터 규격, 초안 안내 수정, 외부 사례·하이웍스 일정 검토·최종 개발 범위 | e9f033dc, 7664440e, 8ea8699d | 기존 코드 227개 통과; 문서 변경과 배포 상태 구분 |

## 커밋별 원본 목록

공개 Git 변경만 수록한다. 토큰·비밀번호·초대 URL·직원 개인 데이터는 이력 문서에 기록하지 않는다.

| 한국 시각 | 커밋 | 변경 내용 |
|---|---|---|
| 2026-11-09 12:44:31 | [8ea8699d](https://github.com/gunsystems/workhub/commit/8ea8699dacea2f086c7a7895510e56c8293cb846) | docs: finalize unified work, recurring reporting and calendar development scope [skip render] |
| 2026-11-09 12:37:34 | [7664440e](https://github.com/gunsystems/workhub/commit/7664440e625f8d9742b8ad8098d60df59855639a) | Correct draft visibility guidance to match the existing creator-only access policy |
| 2026-11-09 12:28:22 | [ad7b066e](https://github.com/gunsystems/workhub/commit/ad7b066ebd00afc15e17fabf2956fbff2c2a75aa) | Record official external workflow patterns, Workhub design gaps and prioritized recommendations [skip render] |
| 2026-11-09 12:11:29 | [a4373ea4](https://github.com/gunsystems/workhub/commit/a4373ea45c27e1b0bc7f09305ae242bc6c41031b) | Record requested recurring report updates and official Hiworks calendar subscription findings [skip render] |
| 2026-11-09 10:48:29 | [e9f033dc](https://github.com/gunsystems/workhub/commit/e9f033dcae6688328f5c64ada9b2f0e9084255d8) | Keep work list filters aligned in a responsive bounded grid |
| 2026-11-09 10:45:54 | [a734c7cb](https://github.com/gunsystems/workhub/commit/a734c7cb9c30092ff33668ee99b65280765b08f9) | Unify search inputs, filter selects and toolbar buttons across work lists |
| 2026-11-09 10:43:59 | [8d555f36](https://github.com/gunsystems/workhub/commit/8d555f36020050e54f2ae53f2865f927a92d7046) | Keep administrator forms left aligned within the standard content width |
| 2026-11-09 10:39:11 | [3e25c482](https://github.com/gunsystems/workhub/commit/3e25c4826eba41e8e595b1a28b9e18102acaf59f) | Align administrator account content with the shared workspace layout |
| 2026-11-09 09:53:22 | [5e6fb732](https://github.com/gunsystems/workhub/commit/5e6fb732b2bd40c605200c7652be15b0c336121e) | Preserve the existing department when editing employee information |
| 2026-11-09 09:50:59 | [0351e14d](https://github.com/gunsystems/workhub/commit/0351e14d330d0938135935fb1a3486de3783f575) | Show effective executive business authority consistently across staff forms and employee displays |
| 2026-11-09 09:43:52 | [f30df86f](https://github.com/gunsystems/workhub/commit/f30df86f4de2917774a549dfb0e31f867e00ff18) | Record verified staff directory release and approved fixture cleanup [skip render] |
| 2026-11-09 09:39:50 | [f3998644](https://github.com/gunsystems/workhub/commit/f3998644558c9a2aba86c7bd579e6e497524b44e) | Clarify information-only registration and require explicit review of seed feedback cleanup |
| 2026-11-09 09:35:47 | [07795c43](https://github.com/gunsystems/workhub/commit/07795c434658832227bd9d2efbc8bac6a478d742) | Manage Workhub employees from the Hiworks directory with separate registration and invitation actions |
| 2026-11-09 09:21:07 | [6f61fc31](https://github.com/gunsystems/workhub/commit/6f61fc3194501317e968cf6ee7d5a9af719d6443) | Limit direct supervisor choices to team leaders, heads and executives |
| 2026-11-09 09:19:00 | [5dcf6289](https://github.com/gunsystems/workhub/commit/5dcf6289489c74cc804870933b63144ca8342963) | Only offer onboarded active employees as direct supervisors |
| 2026-11-09 09:15:09 | [c85251c9](https://github.com/gunsystems/workhub/commit/c85251c9fe3e1f9ceb4d0fbfd03ef7648f0b2dad) | Keep employee search compact and match existing form styling |
| 2026-11-09 09:12:42 | [8635960f](https://github.com/gunsystems/workhub/commit/8635960f6c91ed97b50ed92f881a513bacdf39aa) | Select verified Hiworks employees when sending staff invitations |
| 2026-11-09 08:35:23 | [c0d5cba1](https://github.com/gunsystems/workhub/commit/c0d5cba199b441b6a7946c9c266b878d9009d96b) | Use clearer Korean copy for safe request retry screen |
| 2026-11-09 08:05:12 | [6a814c8f](https://github.com/gunsystems/workhub/commit/6a814c8f4aef63a068a26be561d66e0ea1750f77) | Record verified production Hiworks employee access policy [skip render] |
| 2026-11-09 08:01:41 | [ebb1d8ea](https://github.com/gunsystems/workhub/commit/ebb1d8ea97326d25d58399092cff8f1bcb512765) | Restrict employee access to recently verified Hiworks membership and enforce OTP on account routes |
| 2026-11-08 22:48:37 | [b1aba312](https://github.com/gunsystems/workhub/commit/b1aba312308805bedab56b21a926bba5454cbb85) | Record automatic Hiworks linking and native staff UI verification [skip render] |
| 2026-11-08 22:44:42 | [25883e92](https://github.com/gunsystems/workhub/commit/25883e92d3d5f4fb43a47719686caadef1227408) | Automatically link verified invited employees to Hiworks and unify employee administration UI |
| 2026-11-08 22:20:37 | [3381dc69](https://github.com/gunsystems/workhub/commit/3381dc691e9c7468134791293492eb2619027c45) | Record Hiworks matching fix and outbound IP allowlist requirement [skip render] |
| 2026-11-08 22:14:57 | [0255f5a1](https://github.com/gunsystems/workhub/commit/0255f5a14123e8181bf916f1e6d548cf19d4cdf7) | Match Hiworks employees by office mail ID instead of shared contact email |
| 2026-11-08 21:51:15 | [5046db87](https://github.com/gunsystems/workhub/commit/5046db871745699cd025a4efd1a7d57ce99618e6) | Record verified priority release and remaining Hiworks secret setup [skip render] |
| 2026-11-08 21:47:06 | [90db3c1d](https://github.com/gunsystems/workhub/commit/90db3c1de655ec3d9c340e9d28563da4fa7881a3) | Strengthen team oversight and calendar views; add reviewed Hiworks staff links and notification outbox |
| 2026-11-08 21:15:56 | [d528f29a](https://github.com/gunsystems/workhub/commit/d528f29a6a04a5581d81e49e2692fb06e6190098) | Document reference-led roadmap and Hiworks integration review [skip render] |
| 2026-11-08 20:57:58 | [74ad5da2](https://github.com/gunsystems/workhub/commit/74ad5da210e386d8d1b1430379b77e3b704e439f) | Record work status naming and employee identity display [skip render] |
| 2026-11-08 20:53:34 | [20f40459](https://github.com/gunsystems/workhub/commit/20f4045902b44dfaa42742df94b0a27b205be28e) | Rename official work to work status and show employee identity in header |
| 2026-11-08 20:47:48 | [86d363eb](https://github.com/gunsystems/workhub/commit/86d363eb50b23e86fd6948a117c82bba6c571864) | Document company-domain cutover and verified employee login [skip render] |
| 2026-11-08 20:43:33 | [c4e7c1a1](https://github.com/gunsystems/workhub/commit/c4e7c1a176d3f0e6a08094c660f03f8d38d47b65) | Route legacy Workhub links safely to the company domain |
| 2026-11-08 20:34:37 | [a45b8835](https://github.com/gunsystems/workhub/commit/a45b88358df2c73a1a0ff719967f98ad28f37654) | Place contextual help at the far right of the page header |
| 2026-11-08 20:32:23 | [f80e8ef3](https://github.com/gunsystems/workhub/commit/f80e8ef3029bc076c55988f16062a4977bfb2023) | Record verified visual help and Google registration release [skip render] |
| 2026-11-08 20:28:19 | [aef9af18](https://github.com/gunsystems/workhub/commit/aef9af18e5e5c6a4812fb12ea2ebbe801cb0a9f1) | Add contextual visual help and clarify per-task Google Calendar registration |
| 2026-11-08 20:09:32 | [e58f91fd](https://github.com/gunsystems/workhub/commit/e58f91fd3a2270e6232fde0b1c3c828769997060) | Preserve white text on primary action links in detail panels |
| 2026-11-08 18:03:37 | [39b859a2](https://github.com/gunsystems/workhub/commit/39b859a2d9df61f57db2777605cd4b88a4f4ec45) | Record verified administrator layout and balanced branding [skip render] |
| 2026-11-08 18:00:43 | [cfe7895b](https://github.com/gunsystems/workhub/commit/cfe7895b0a71a0cb291e5d7930aede36084e0029) | Align administrator account form and balance Workhub product typography |
| 2026-11-08 17:55:31 | [dbda2183](https://github.com/gunsystems/workhub/commit/dbda218343c3755246d59200e0a67891acbb3343) | Record verified company and product branding [skip render] |
| 2026-11-08 17:53:46 | [4f359cb9](https://github.com/gunsystems/workhub/commit/4f359cb939273413fcbcc84d42bff56dabe46d5f) | Keep company name prominent and Workhub subtitle compact |
| 2026-11-08 17:51:38 | [39a23112](https://github.com/gunsystems/workhub/commit/39a2311240db5dc3408a248a59e557a49d1c62fa) | Add Workhub product subtitle beneath GUNSYSTEMS company wordmark |
| 2026-11-08 17:48:34 | [6039e917](https://github.com/gunsystems/workhub/commit/6039e91704fdfa6e366adf3b0a2ca221accc6777) | Record company branding release and DNS certificate dependency [skip render] |
| 2026-11-08 17:46:35 | [73aa8d4a](https://github.com/gunsystems/workhub/commit/73aa8d4a7e11ed80088ce8ebf90edfdc8ceefee4) | Replace temporary Workhub mark with GUNSYSTEMS company wordmark |
| 2026-11-08 17:38:44 | [3575d981](https://github.com/gunsystems/workhub/commit/3575d9814409f7fc9e55ff1c910f05f91ffde54a) | Use consistent bold priority borders for all personal cards |
| 2026-11-08 17:36:23 | [6a3a33f1](https://github.com/gunsystems/workhub/commit/6a3a33f130eabacc7fb74f5bfca1390fe476af60) | Record urgent styling release and pending company DNS verification [skip render] |
| 2026-11-08 17:31:32 | [0e47fedb](https://github.com/gunsystems/workhub/commit/0e47fedb2ca6c773417870dbea58a2c4e671823e) | Highlight urgent personal cards in bold red and prepare company hostname |
| 2026-11-08 10:35:17 | [9a42de08](https://github.com/gunsystems/workhub/commit/9a42de08d447d8415f68cb424e876f046841323a) | Record verified board settings and footer fix [skip render] |
| 2026-11-08 10:32:40 | [1a77631d](https://github.com/gunsystems/workhub/commit/1a77631d712352cbbe35503b9c58744761a3e077) | Fix board settings dismissal and align calendar action and footer spacing |
| 2026-11-08 10:27:29 | [ede08323](https://github.com/gunsystems/workhub/commit/ede083238bf632ddff72d4b3e56872efcabe7749) | Record verified timed scheduling release and pending OAuth setup [skip render] |
| 2026-11-08 10:24:44 | [a4e801f7](https://github.com/gunsystems/workhub/commit/a4e801f71925f7c884ff88083f8166880f493b66) | Add private tags, timed recurrence and opt-in Google Calendar integration |
| 2026-11-08 00:17:48 | [a9a291d1](https://github.com/gunsystems/workhub/commit/a9a291d154c529922c62a09789c49f947203700e) | Record verified navigation and personal card design release [skip render] |
| 2026-11-08 00:13:54 | [33e9a5e2](https://github.com/gunsystems/workhub/commit/33e9a5e2d5e8c54b6c561f08ae0388e58ba39f3e) | Refine laptop navigation spacing and anchor card menus near their trigger |
| 2026-11-08 00:07:34 | [49a5ea33](https://github.com/gunsystems/workhub/commit/49a5ea33a1fdb957dd308efef607227871968221) | Simplify navigation and upgrade personal board cards |
| 2026-11-07 23:27:34 | [d0256e19](https://github.com/gunsystems/workhub/commit/d0256e1962f24b08842097249a6a8c705b796cec) | Record verified live v0.5 release and preserved owner access [skip render] |
| 2026-11-07 23:23:17 | [7b1c9212](https://github.com/gunsystems/workhub/commit/7b1c9212c3f3b47592552177fe19f4d9036b5611) | Add official collaboration, explicit approval stages and private personal boards |
| 2026-11-07 22:32:36 | [d6dc2e2f](https://github.com/gunsystems/workhub/commit/d6dc2e2ff3c7e26c2aa0a34d2fe9041ef0ef5ad7) | Style password reset request, new password and expired-link screens |
| 2026-11-07 22:25:29 | [e6d0014f](https://github.com/gunsystems/workhub/commit/e6d0014fc8af3d1d8d7c8eab4b2923c78adf4ed2) | Record live confirmation popup, reset email design and Lee test deletion [skip render] |
| 2026-11-07 22:22:26 | [14a8f42f](https://github.com/gunsystems/workhub/commit/14a8f42fa9e646045dc5418f72b20ccf34618ec1) | Confirm employee deletion in a popup and brand password reset emails |
| 2026-11-07 22:11:10 | [5330c996](https://github.com/gunsystems/workhub/commit/5330c996c9728d3157839ddc45b2d0c77b534f04) | Record live OTP recovery release and Lee test reset [skip render] |
| 2026-11-07 22:08:19 | [ae60bb47](https://github.com/gunsystems/workhub/commit/ae60bb47f5370b5e6591368c4d5a2b432890ef4b) | Replace employee recovery codes with administrator-approved OTP restoration |
| 2026-11-07 21:37:05 | [84ab4432](https://github.com/gunsystems/workhub/commit/84ab4432ffb3237ae2967b2df6c4d66990002455) | Record completed test account reset and lifecycle deployment [skip render] |
| 2026-11-07 21:33:33 | [2ca8e3eb](https://github.com/gunsystems/workhub/commit/2ca8e3eb6c817d8ed7eede814468f4597ccaafd1) | Add employee suspension, reactivation and safe account deletion |
| 2026-11-07 21:17:40 | [83eec0d6](https://github.com/gunsystems/workhub/commit/83eec0d6ffef4b1b798740965e45fe18c8c795d0) | Improve authentication button layout and explain recovery codes |
| 2026-11-07 21:07:31 | [94d16246](https://github.com/gunsystems/workhub/commit/94d162465cf46d182466483a19ef1352ab7d9547) | Fix HTTPS CSRF failures on Workhub authentication forms |
| 2026-11-07 20:59:17 | [0679c97d](https://github.com/gunsystems/workhub/commit/0679c97d993aba00999e8cc72b2bab072572a4ab) | Style Workhub employee invitations with a branded HTML email |
| 2026-11-07 20:43:26 | [78776237](https://github.com/gunsystems/workhub/commit/78776237988809591416f1df399e5fb1386fc603) | Send Workhub authentication emails through Hiworks Office Token API |
| 2026-11-07 20:24:16 | [f6ce86c9](https://github.com/gunsystems/workhub/commit/f6ce86c97b967bee2893e2e26ea76d9cddbaf74a) | Merge pull request #1 from gunsystems/feature/employee-invitation-mfa |
| 2026-11-07 19:57:58 | [cc036d6d](https://github.com/gunsystems/workhub/commit/cc036d6db9f92a9b37ae255b7dc9a5980d8eefcf) | Add employee email invitations and mandatory OTP onboarding |
| 2026-11-06 22:38:24 | [7be6e912](https://github.com/gunsystems/workhub/commit/7be6e91258b271d2982d3a4481d07c5188dea25d) | Add work intake, project oversight, assignment acknowledgement and action center |
| 2026-11-06 17:17:23 | [27bf9424](https://github.com/gunsystems/workhub/commit/27bf94245e321946d7b71423c03becbb73be40e9) | Add personal and management dashboards with review workflow and performance scoring |
| 2026-11-06 16:56:12 | [077f9ec4](https://github.com/gunsystems/workhub/commit/077f9ec4db5214ae82d49259b3d740ba6556a5b0) | Enforce department access and employee roles |
| 2026-11-06 16:32:58 | [569b9721](https://github.com/gunsystems/workhub/commit/569b9721b37f6af30fd65c950a3f9a4720e487b5) | Build Workhub calendar and dedicated Render test deployment |

