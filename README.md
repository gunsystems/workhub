# GUNSYSTEMS Workhub

직원과 부서의 업무를 월·주·일 캘린더에서 관리하는 Django 애플리케이션입니다.

## 개발 시작 (Python 3.12)

```bash
cd /workspace/workhub
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py createsuperuser
# 선택: 빈 개발 DB에만 예시 직원과 업무 생성
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

로그인은 `/login/`에서 합니다. 관리자에서 부서, 직원, 사용자 계정을 등록합니다.
일반 로그인 사용자는 모든 업무를 조회·등록·수정할 수 있습니다. 사용자별 역할 및 부서별 수정 권한은 아직 구현하지 않았습니다. 사용자 계정과 업무 담당자(직원)는 현재 별도 관리합니다.

## 기본 기능

- 월·주·일 캘린더, 이전/다음 기간 및 오늘 이동
- 직원·부서·진행 상태 필터 (함께 적용 가능)
- 업무 등록, 상세 조회, 수정
- 담당자와 소속 부서, 예정/진행 중/보류/완료, 중요도, P1~P4 우선순위, 시작일과 마감일
- 캘린더 내 우선순위 순 정렬, 여러 날 업무의 날짜별 표시
- 기한 초과 및 3일 이내 마감 업무 별도 목록 (전체 기간, 현재 필터 기준)
- 완료 업무는 마감 경고에서 제외, 날짜는 Asia/Seoul 기준
- SQLite 파일로 데이터 영구 저장, CSRF 및 로그인 접근 보호

중요도는 업무 영향도, 우선순위는 실행 순서입니다. 부서는 담당 직원의 현재 소속을 따릅니다.
예시 데이터는 `[예시]`와 `(예시)`로 표시되며 실제 직원이나 고객 업무가 아닙니다.

## 검증

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

## 현재 범위

로컬 개발용 기본 버전입니다. 실제 사내 운영 전 역할별 권한, 직원 계정 연결, 변경 이력, 백업, PostgreSQL, HTTPS 및 배포 구성을 추가해야 합니다.
개발 서버는 로컬에만 바인딩합니다. 배포 시 `DEBUG=0`, 안전한 `SECRET_KEY`, `ALLOWED_HOSTS`를 설정하고 운영 WSGI 서버를 사용하세요.
저장소는 기존 `/workspace/workhub`를 사용하며 새 Git worktree를 만들 필요가 없습니다.
