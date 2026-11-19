"""Normalize already permission-filtered notices into one chronological feed."""
from django.urls import reverse
from django.utils import timezone


def build_feed(*, work, personal, requests, quick, followups, leave, proposals, certificates):
    feed = []

    def add(kind, row, at, title, message, route=None, args=(), post=False,
            button='내용 확인', detail='', closed=False):
        feed.append({'kind': kind, 'pk': row.pk, 'at': at, 'title': title,
                     'message': message, 'detail': detail, 'closed': closed,
                     'unread': not getattr(row, 'read_at', None) and not closed,
                     'url': reverse(route, args=args) if route else '', 'post': post,
                     'button': button})

    for row in work:
        add('업무', row, row.created_at, row.task.title, row.message,
            'read_work_notification', [row.pk], post=True, button='업무 확인')
    for row in personal:
        add('개인 일정', row, row.delivered_at, row.card.title, '개인 일정 알림',
            'personal_reminder_read', [row.pk], post=True, button='개인 카드 확인',
            detail='예정 시간 ' + timezone.localtime(row.scheduled_for).strftime('%Y-%m-%d %H:%M') + ' · 나만 보기')
    for row in requests:
        add('업무협조', row, row.event.created_at, row.request.title, row.event.action,
            'read_request_notification', [row.pk], post=True, button='요청 확인', detail=row.event.body[:150])
    for row in quick:
        add('간단 업무', row, row.event.created_at, row.event.request.title, row.event.action,
            'quick_detail', [row.event.request_id], button='간단 업무 확인')
    for row in followups:
        add('후속 조치', row, row.created_at, f'{row.item.case.employee.name} · {row.item.title}',
            row.current_state, None if row.closed else 'offboarding_item',
            [] if row.closed else [row.item_id], closed=row.closed,
            button='취소 내용 확인' if row.item.case.state == 'cancelled' else '배정 항목 확인')
    for row in leave:
        snapshot = row.snapshot
        name = snapshot.get('name', '')
        kind = snapshot.get('kind', '') or row.event.request.get_kind_display()
        title = f'{name} · {kind} 신청'.strip(' ·')
        closed = row.kind == 'pending' and not row.actionable
        add('휴가', row, row.created_at, title, row.message,
            None if closed else 'leave_detail', [] if closed else [row.event.request_id], closed=closed,
            detail=f"{snapshot.get('start', '')} ~ {snapshot.get('end', '')}",
            button='결재 내용 확인' if row.actionable else '결재 결과 확인')
    for row in proposals:
        item = row.event.proposal
        add('품의', row, row.created_at, item.title, row.message,
            'proposal_detail', [item.pk], button='품의 확인',
            detail=f'품의 #{item.pk} · {item.get_state_display()}')
    for row in certificates:
        item = row.event.request
        add('증명서·서류', row, row.created_at, item.title,
            '증명서·서류 발급 요청' if row.kind == 'pending' else row.message,
            'certificate_detail', [item.pk], button='서류 신청 확인', detail=item.get_state_display())
    return sorted(feed, key=lambda item: (item['at'], item['kind'], item['pk']), reverse=True)
