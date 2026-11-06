# Workhub 전용 Render 테스트 서비스

기존 홈페이지 서비스와 DB, 도메인, 환경 변수는 수정하지 않습니다.
`gunsystems/workhub` 저장소의 `render.yaml`로 별도 서비스를 생성합니다.

## 생성

1. Render Dashboard에서 New → Blueprint를 선택합니다.
2. GitHub 연결에서 `gunsystems/workhub`만 필요한 접근 권한으로 연결합니다.
3. `gunsystems-workhub-test` 서비스와 `workhub-test-data` 디스크를 확인합니다.
4. **Starter 인스턴스 및 디스크의 표시 요금을 확인한 뒤** 생성합니다. 이 구성은 무료가 아닙니다.
5. 배포가 끝나면 Render가 제공하는 서비스의 HTTPS 주소를 사용합니다. 기존 회사 도메인은 연결하지 않습니다.

DB는 전용 디스크 `/var/data/workhub.sqlite3`에 보존됩니다. 무료 서비스의 임시 SQLite 파일은 재배포 시 사라질 수 있으므로 이 구성에서는 사용하지 않습니다. 데이터는 예시 또는 테스트 업무만 입력합니다. 단일 인스턴스용 SQLite이므로 운영 확장 구성은 별도로 준비해야 합니다.

## 첫 계정과 예시 업무

해당 **Workhub 테스트 서비스**의 Shell에서:

```bash
python manage.py createsuperuser
python manage.py seed_demo
```

계정과 비밀번호는 Shell에서 직접 입력하고 채팅이나 저장소에 기록하지 않습니다. 서비스 주소의 `/login/`에서 로그인하세요.
부서·직원 관리는 `/admin/`에 있습니다. 일반 직원용 로그인 계정은 관리자 Users에서 별도 생성합니다.

## 진척 확인

- 첫 화면: 월/주/일 전환, 직원/부서 필터
- 새 업무: 담당자·상태·중요도·우선순위·시작일·마감일을 입력하고 저장
- 상세 화면: 저장 값 확인 후 수정
- 마감일을 어제/오늘로 지정해 기한 초과/임박 표시 확인
- 완료로 변경해 경고에서 제외되는지 확인
- 후속 변경은 이 테스트 서비스에만 배포하고 같은 HTTPS 주소에서 확인

초기 클라우드 개발 환경의 서버 프로세스나 db.sqlite3는 Render로 복사하지 않습니다. Render에는 별도 DB와 계정을 생성합니다. 운영 서비스로 승격하거나 실제 운영 데이터를 복사하지 않습니다.
