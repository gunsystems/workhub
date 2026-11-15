"""Audience-specific guidance; never changes feature access rights."""
from copy import deepcopy
from .permissions import is_company_admin, can_override_work, profile


def for_audience(name, source, user):
    result = deepcopy(source)
    authenticated = bool(user and user.is_authenticated and user.is_active)
    admin = authenticated and is_company_admin(user)
    employee = profile(user) if authenticated else None
    reviewer = bool(admin or (employee and (employee.role == 'manager' or employee.position in ['leader', 'head', 'executive'])))
    if name in ['shared_documents', 'shared_document_link', 'shared_folder_manage', 'shared_document_access']:
        result['steps'] = [
            '회사 문서함 연결에서 Workhub와 같은 이메일의 회사 Microsoft 계정으로 로그인합니다.',
            '폴더 이름을 눌러 열고, 상단 폴더 경로나 상위 폴더로 이동합니다.',
            '검색어와 정렬을 선택해 문서를 찾습니다. 더 보기 또는 다음 항목 검색이 있으면 이어서 확인하세요.',
            '문서 열기로 원본을 확인하고, 업무에 연결에서 관련 업무·프로젝트를 선택합니다.',
            '연결한 문서는 해당 업무의 공동 문서 보기 · 연결에서 다시 엽니다.',
        ]
        result['notes'] = ['문서를 연결해도 원본을 복사하지 않습니다. 수정한 내용은 같은 원본에 반영됩니다.',
                           '문서가 열리지 않거나 편집이 필요하면 관리자에게 문의하세요.',
                           '검색은 현재 폴더의 이름을 기준으로 합니다. 문서함 전체에서 찾으려면 SharePoint에서 열기를 이용하세요.',
                           '접근 권한 확인에서는 본인에게 공개된 공유 정보를 볼 수 있습니다.']
        result['flow_labels'] = ['회사 계정 연결', '폴더 · 문서 찾기', '원본 열기', '업무에 연결']
        if authenticated and can_override_work(user):
            result['management_notes'] = ['새 폴더와 폴더 옆 ⋯ 메뉴에서 폴더를 만들거나 이름을 변경할 수 있습니다. 회사 문서함의 편집 권한이 필요합니다.',
                                          '빈 폴더만 삭제할 수 있습니다. 삭제한 폴더는 SharePoint 휴지통으로 이동합니다.']
        if admin:
            result.setdefault('management_notes', []).append('최종 권한 확인·변경은 SharePoint 액세스 관리에서 진행하세요. 표시된 공유 정보는 전체 접근자 목록이 아닐 수 있습니다.')
    elif name in ['notice_list', 'notice_detail'] and not admin:
        result.update(title='공지사항', purpose='본인에게 전달된 공지를 확인합니다.',
            steps=['공지 제목을 눌러 내용을 확인합니다.', '첨부자료와 공지에서 안내하는 일시·대상을 확인합니다.'],
            notes=['본인에게 전달된 공지만 표시됩니다.', '공지 내용을 열면 확인 시각이 기록됩니다.'],
            flow_labels=['공지 목록', '내용 · 자료 확인', '안내에 따라 진행'])
    elif name == 'survey_detail' and not admin:
        result.update(title='설문 응답', purpose='안내받은 설문의 질문을 읽고 의견을 제출합니다.',
            steps=['응답 마감과 기명·익명 여부를 확인합니다.', '질문별 단일·복수 선택 안내에 맞춰 답변합니다.',
                   '임시 저장 후 이어서 작성하거나 응답 제출을 누릅니다.'],
            notes=['제출 후에는 답변을 변경할 수 없습니다.', '익명 설문의 자유 의견에는 본인을 식별할 내용을 적지 마세요.'],
            flow_labels=['설문 안내 확인', '답변 작성', '응답 제출'])
    elif name in ['offboarding_assignments', 'offboarding_item'] and not admin:
        result.update(purpose='본인에게 배정된 후속 조치를 처리하고 결과와 증빙을 제출합니다.',
            steps=['배정 항목과 처리 기한을 확인합니다.', '해당 서비스에서 작업한 뒤 처리 일시·내용과 화면 캡처 또는 PDF 증빙을 제출합니다.',
                   '보완 요청이 오면 다시 제출합니다. 관리자의 확인 완료 후 처리가 끝납니다.',
                   '퇴사 처리 취소로 표시되면 작업을 진행하지 말고 취소 확인을 합니다.'],
            notes=['다른 담당자의 항목과 증빙은 표시되지 않습니다.', '비밀번호·인증키와 불필요한 개인정보는 증빙에서 가려 주세요.'],
            flow_labels=['배정 내용 확인', '실제 작업', '증빙 제출', '검토 결과 확인'])
    elif name == 'my_information' and not admin:
        result['steps'][-1] = '이름·부서·사번 등 기본 정보의 수정이 필요하면 관리자에게 요청하세요.'
        result['notes'] = ['입사일을 기준으로 근속기간을 표시합니다.',
                           '주소 검색으로 우편번호와 기본 주소를 선택한 뒤 상세 주소를 입력하고 저장합니다. 직접 입력할 수도 있습니다.',
                           '생년월일·연락처·주소는 본인과 관리자만 확인하며 사원카드 이미지에는 포함하지 않습니다.']
    elif name == 'period_reports' and not reviewer:
        result.update(purpose='내 업무보고를 작성·제출하고 검토 결과를 확인합니다.',
            steps=['보고 구분과 기간을 선택해 업무보고를 작성합니다.', '주간업무보고는 이번 주 진행 업무와 차주 예정 업무를 보완해 팀장에게 제출합니다.',
                   '제출한 보고에서 확인 완료·보완 요청과 피드백을 확인합니다. 보완 요청이 있으면 수정 후 재제출합니다.'],
            notes=['임시 저장한 보고는 제출 전 상태입니다.', '제출한 내용은 버전별로 보존됩니다.'],
            flow_labels=['업무보고 작성', '내용 보완', '제출', '피드백 확인'])
    elif name == 'schedule_control' and not reviewer:
        result.update(steps=['본인에게 지정된 등록 대상과 기한을 확인하고 실제 등록한 업무를 연결합니다.', '일정 변경 시 변경 사유를 남깁니다.',
                             '업무 상세에서 참석·불참·연기 등 실제 결과와 필요한 설명을 기록합니다.'],
                      notes=['미등록·지연 표시는 확인이 필요한 상태입니다. 고의성이나 근태를 자동으로 판단하지 않습니다.'])
    if name in ['approval_portal', 'leave_new', 'leave_detail']:
        result['steps'] = [item.replace(' 대표 본인 휴가는 직접 신청 후 본인이 승인합니다.', '').replace(' 대표 본인은 신청 후 본인이 승인합니다.', '') for item in result['steps']]
        result['notes'] = [item.replace(' 대표 본인의 신청은 셀프 결재를 허용합니다.', '') for item in result['notes'] if not item.startswith('관리자의 직원 선택') or admin]
        if name == 'approval_portal':
            result['notes'] = [item for item in result['notes'] if not item.startswith('개발·테스트')]
    if name == 'period_reports':
        result['notes'] = [item.replace('팀장 취합 및 임원 보고 기능은 보류 중입니다. ', '') for item in result['notes']]
    for field in ['purpose', 'steps', 'notes', 'management_notes']:
        value = result.get(field)
        if isinstance(value, str):
            result[field] = value.replace('하이웍스 메신저', '하이웍스')
        elif isinstance(value, list):
            result[field] = [item.replace('하이웍스 메신저', '하이웍스') for item in value]
    return result
