"""Versioned document structure policy. This module never writes to SharePoint.

Group keys identify business roles, never personal principals. Existing paths
are reported for review, never adopted or rewritten by a provisioning retry.
"""
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError

POLICY_VERSION = '2026-11-20.1'
VENDORS = ('CrowdStrike', 'Tenable', 'Fortra_DLP', '지란지교시큐리티',
           'Broadcom_Symantec', 'GUNSYSTEMS')
PROJECT_TYPES = ('구축', 'PoC', '유지보수', '기술지원', '제안')
PROJECT_CHILDREN = ('01_영업', '02_기술', '03_회의_이슈', '04_산출물')
GROUPS = {'staff': '전 직원', 'sales': '영업', 'support': '경영지원',
          'technical': '보안기술팀', 'executives': '경영진', 'admins': '관리자'}
COMMON = ('01_회사소개_CI', '02_규정_인사', '03_양식_템플릿', '04_공지_회의록')
SALES = '01_영업자료'
TECHNICAL = '02_기술자료'
ARCHIVE = '03_완료프로젝트'
SALES_CHILDREN = ('01_고객계약', '02_벤더_파트너계약', '03_제안서', '04_견적', '05_벤더_세일즈_가격정책')
TECHNICAL_CHILDREN = ('01_구축_운영문서', '02_벤더_제품_기술자료', '03_교육_인증')
VENDOR_BASES = tuple((SALES, name) for name in SALES_CHILDREN[2:]) + tuple(
    (TECHNICAL, name) for name in TECHNICAL_CHILDREN)


def validate_name(value, *, limit=120):
    if (not isinstance(value, str) or not value or len(value) > limit
            or value != value.strip() or value.endswith('.') or value.startswith('~$')
            or re.search(r'[\s"*:<>?/\\|#%\x00-\x1f]', value)
            or re.match(r'^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', value, re.I)
            or value.casefold() in ('.', '..', '.lock', 'desktop.ini', '_vti_')):
        raise ValidationError('이름에 공백·금지 문자·예약어를 사용할 수 없습니다. 공백 대신 _를 사용하세요.')
    return value


def vendor_list(values):
    result = tuple(validate_name(value) for value in values)
    if not result or len(result) > 100 or len({v.casefold() for v in result}) != len(result):
        raise ValidationError('벤더 목록이 비어 있거나 중복됩니다.')
    return result


@dataclass(frozen=True)
class Folder:
    library: str
    path: tuple
    grants: tuple = ()

    @property
    def unique(self):
        return bool(self.grants)

    @property
    def display(self):
        return '/'.join((self.library,) + self.path)


def rights(**values):
    return tuple(sorted({'admins': 'manage', **values}.items()))


def fixed_folders(vendors=VENDORS):
    """51 folders for six vendors; roots and on-demand years are excluded."""
    vendors = vendor_list(vendors)
    result = [Folder('common', (name,)) for name in COMMON]
    result.append(Folder('documents', (SALES,), rights(sales='write_no_delete',
        support='write_no_delete', executives='read')))
    for name in SALES_CHILDREN:
        grants = rights(sales='write_no_delete', support='write_no_delete',
                        technical='read', executives='read') if name == '03_제안서' else ()
        result.append(Folder('documents', (SALES, name), grants))
    result.append(Folder('documents', (TECHNICAL,), rights(technical='write_no_delete', executives='read')))
    for name in TECHNICAL_CHILDREN:
        grants = rights(technical='write', sales='read', executives='read') if name != '01_구축_운영문서' else ()
        result.append(Folder('documents', (TECHNICAL, name), grants))
    result.extend(Folder('documents', base + (vendor,)) for base in VENDOR_BASES for vendor in vendors)
    result.append(Folder('documents', (ARCHIVE,)))
    return tuple(result)


COMMON_ROOT_GRANTS = rights(staff='read')


def project_name(year, *, customer='', vendor='', kind='', internal='', vendors=VENDORS):
    if not re.fullmatch(r'20\d{2}', str(year)):
        raise ValidationError('프로젝트 연도는 2000~2099년으로 입력하세요.')
    if internal:
        if customer or vendor or kind:
            raise ValidationError('내부 과제는 과제명만 입력하세요.')
        name = f'{year}_내부_{validate_name(internal)}'
    else:
        if not customer or customer == '내부' or vendor not in vendor_list(vendors) or kind not in PROJECT_TYPES:
            raise ValidationError('고객명·벤더·프로젝트 유형을 확인하세요.')
        name = f'{year}_{validate_name(customer)}_{vendor}_{kind}'
    return validate_name(name, limit=40)


def project_folders(name, *, completed=False, year=None):
    validate_name(name, limit=40)
    if not re.match(r'^20\d{2}_', name):
        raise ValidationError('프로젝트 폴더 이름을 확인하세요.')
    if completed:
        if not re.fullmatch(r'20\d{2}', str(year)):
            raise ValidationError('종료 연도를 확인하세요.')
        base = (ARCHIVE, str(year), name)
        root = Folder('documents', base, rights(sales='read', technical='read', executives='read'))
        children = [Folder('documents', base + (child,),
            rights(sales='read', support='read', executives='read') if child == '01_영업' else
            rights(technical='read', executives='read') if child == '02_기술' else ()) for child in PROJECT_CHILDREN]
    else:
        base = (name,)
        root = Folder('projects', base, rights(participants='write', executives='read'))
        children = [Folder('projects', base + (child,)) for child in PROJECT_CHILDREN]
    return (root, *children)


def preview(existing, vendors=VENDORS):
    """Existing is a complete path inventory; never interpret absence as permission.

    The caller must verify the snapshot is complete, scoped and fresh enough for
    display. This output alone is NEVER sufficient to authorize remote writes.
    """
    known = {(library, tuple(part.casefold() for part in path)) for library, path in existing}
    return [{'folder': folder, 'action': 'existing_review' if
             (folder.library, tuple(p.casefold() for p in folder.path)) in known else 'create',
             'inheritance': '별도 권한' if folder.unique else '상위 권한 상속'}
            for folder in fixed_folders(vendors)]


def inventory_paths(payload):
    """Read display inventory without conflating legacy and standard libraries."""
    libraries = {r['id']: r['name'] for r in payload['libraries']}
    aliases = {'문서':'documents', 'Documents':'documents',
               '프로젝트 공동작업':'projects', '전사공통':'common'}
    nodes = {n['key']: n for n in payload['nodes']}
    if len(nodes) != len(payload['nodes']):
        raise ValidationError('중복된 폴더 정보가 있습니다.')
    result = []
    for node in nodes.values():
        kind = aliases.get(libraries[node['drive']])
        if kind is None:
            continue
        parts, seen, cursor = [], set(), node
        while cursor['parent'] is not None:
            if cursor['key'] in seen or len(seen) > 64:
                raise ValidationError('폴더 경로를 확인하지 못했습니다.')
            seen.add(cursor['key'])
            parts.append(cursor['name'])
            cursor = nodes[cursor['parent']]
            if cursor['drive'] != node['drive']:
                raise ValidationError('문서함 경계가 다른 폴더 정보입니다.')
        if cursor['item'] != 'root':
            raise ValidationError('문서함 시작 위치를 확인하지 못했습니다.')
        if parts:
            result.append((kind, tuple(reversed(parts))))
    return result
