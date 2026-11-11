"""Deterministic, editable report grouping; never invent completion or omit prose."""
import re
import unicodedata
from collections import OrderedDict

from .permissions import can_view_company, profile, visible_tasks

GENERAL_CATEGORIES = {'교육', '내부 회의', '휴가', '연차', '반차', '내부 업무'}
HEADER = re.compile(r'^【(고객사|벤더사) · (.+)】$')
SPECIAL = {'【팀 운영·기타】': ('general', ''), '【미분류 · 확인 필요】': ('unclassified', '')}
MARKER = re.compile(r'\[(고객사|벤더사):\s*([^\]\n]+)\]')


def may_aggregate(user):
    employee = profile(user)
    return can_view_company(user) or bool(employee and (employee.position == 'leader' or employee.role == 'manager'))


def clean_name(value):
    # Labels are bounded single lines; names are never inferred from a title.
    return ' '.join(str(value or '').replace('【', '').replace('】', '').split())[:120]


def name_key(value):
    # Merge case/spacing variants only, not different legal/company aliases.
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


def task_metadata(task):
    return {'customer': task.customer, 'vendor': task.vendor, 'solution': task.solution,
            'report_group': task.report_group, 'category': task.category.name if task.category_id else ''}


def metadata_group(meta):
    customer, vendor = clean_name(meta.get('customer')), clean_name(meta.get('vendor'))
    preferred = meta.get('report_group', 'auto')
    if preferred == 'general':
        return ('general', '')
    if preferred == 'vendor':
        return ('vendor', vendor) if vendor else ('unclassified', '')
    if preferred == 'customer':
        return ('customer', customer) if customer else ('unclassified', '')
    if customer:
        return ('customer', customer)
    if vendor:
        return ('vendor', vendor)
    if meta.get('category') in GENERAL_CATEGORIES:
        return ('general', '')
    return ('unclassified', '')


def metadata_tags(task):
    meta = task_metadata(task)
    area, _ = metadata_group(meta)
    values = []
    # Include the requested grouping first when both relationships exist.
    for kind in (['vendor', 'customer'] if area == 'vendor' else ['customer', 'vendor']):
        value = clean_name(meta.get(kind))
        if value:
            values.append('[' + ('고객사' if kind == 'customer' else '벤더사') + ': ' + value + ']')
    if area == 'general' and task.report_group == 'general':
        values.insert(0, '[팀 운영·기타]')
    return ' '.join(values)


def _group_line(line, tasks, inherited):
    if inherited:
        return inherited
    if '[팀 운영·기타]' in line:
        return ('general', '')
    marker = MARKER.search(line)
    if marker:
        name=clean_name(marker.group(2))
        return ('customer' if marker.group(1) == '고객사' else 'vendor', name) if name else ('unclassified', '')
    # Only an explicit, unambiguous original task title qualifies as metadata.
    def title_text(value):
        return re.sub(r'^(?:[-•]\s*)?(?:\[[^\]\n]*\]\s*)*', '', str(value)).strip()
    content=title_text(line)
    def matches_title(task):
        title=title_text(task.get('title') or '')
        return bool(title and re.match(re.escape(title)+r'(?:$|[\s·(（,:：])',content))
    matches = [task for task in tasks if matches_title(task)]
    groups = {metadata_group(task) for task in matches}
    if len(groups) == 1:
        return groups.pop()
    if not matches and any('[' + category + ']' in line for category in GENERAL_CATEGORIES):
        return ('general', '')
    return ('unclassified', '')


def grouped_draft(user, submissions):
    """Use frozen submission prose; supplement old snapshots with visible task labels."""
    submissions = list(submissions)
    task_ids = {task.get('id') for sub in submissions for task in sub.snapshot.get('tasks', []) if task.get('id')}
    live = {task.pk: task for task in visible_tasks(user).filter(pk__in=task_ids)}
    buckets = {'current': OrderedDict(), 'next': OrderedDict(), 'issues': OrderedDict()}
    sources, supplemented = [], False
    for sub in submissions:
        author = getattr(sub.report.author, 'employee', None)
        name = author.name if author else (sub.report.author.get_username() if sub.report.author else '이전 작성자')
        department = author.department.name if author and author.department_id else ''
        source = f'{department} · {name} · 제출 v{sub.version}'.strip(' ·')
        sources.append(source)
        tasks = []
        for frozen in sub.snapshot.get('tasks', []):
            meta = dict(frozen)
            if 'report_group' not in meta and meta.get('id') in live:
                meta.update(task_metadata(live[meta['id']]))
                supplemented = True
            tasks.append(meta)
        for field, target in [('summary', 'current'), ('next_week_plan', 'next'), ('report_issues', 'issues')]:
            inherited = None
            previous = None
            for raw in str(sub.snapshot.get(field) or '').splitlines():
                line = raw.strip()
                if not line:
                    continue
                header = HEADER.fullmatch(line)
                if header or line in SPECIAL:
                    inherited = (('customer' if header.group(1) == '고객사' else 'vendor', clean_name(header.group(2))) if header else SPECIAL[line])
                    previous = None
                    continue
                # Existing provenance lines stay attached to their preceding item.
                if line.startswith('↳ 출처:') and previous is not None:
                    previous['origins'].add(line.removeprefix('↳ 출처:').strip())
                    continue
                group = _group_line(line, tasks, inherited)
                key = (group[0], name_key(group[1]))
                section = buckets[target].setdefault(key, {'label': group, 'items': OrderedDict()})
                text = re.sub(r'^[-•]\s+', '', line)
                # Only identical text is collapsed; different progress/opinions survive.
                previous = section['items'].setdefault(text, {'text': text, 'origins': set()})
                previous['origins'].add(source)


    def render(sections):
        result = []
        order = {'customer': 0, 'vendor': 1, 'general': 2, 'unclassified': 3}
        for section in sorted(sections.values(), key=lambda row: (order[row['label'][0]], name_key(row['label'][1]))):
            kind, name = section['label']
            result.append(f'【{"고객사" if kind == "customer" else "벤더사"} · {name}】' if kind in ['customer', 'vendor'] else ('【팀 운영·기타】' if kind == 'general' else '【미분류 · 확인 필요】'))
            for item in section['items'].values():
                result.append('- ' + item['text'])
            result.append('')
        return '\n'.join(result).strip()

    return {'current': render(buckets['current']), 'next': render(buckets['next']),
            'issues': render(buckets['issues']), 'source_count': len(submissions),
            'unknown_count': sum(len(section['items']) for value in buckets.values() for section in value.values() if section['label'][0]=='unclassified'), 'supplemented': supplemented, 'source_labels': sources}
