"""Normalize already permission-filtered notices into one chronological feed."""
from django.urls import reverse
from django.utils import timezone


def build_feed(*, work, personal, requests, quick, followups, leave, proposals, certificates):
    feed = []

    def add(kind, row, at, title, message, route=None, args=(), post=False,
            button='내용 확인', detail='', closed=False, status_label=''):
        feed.append({'kind': kind, 'pk': row.pk, 'at': at, 'title': title,
                     'message': message, 'detail': detail, 'closed': closed,
                     'unread': not getattr(row, 'read_at', None) and not closed,
                     'url': reverse(route, args=args) if route else '', 'post': post,
                     'button': button, 'status_label': status_label or ('처리 완료' if closed else '')})

    for row in work:
        status = getattr(row.task, 'status', '')
        closed = status in ('done', 'cancelled')
        add('업무', row, row.created_at, row.task.title, row.message,
            'read_work_notification', [row.pk], post=True,
            button='업무 기록 보기' if closed else '업무 확인', closed=closed,
            status_label={'done':'업무 완료','cancelled':'업무 취소'}.get(status,''))
    for row in personal:
        add('개인 일정', row, row.delivered_at, row.card.title, '개인 일정 알림',
            'personal_reminder_read', [row.pk], post=True, button='개인 카드 확인',
            detail='예정 시간 ' + timezone.localtime(row.scheduled_for).strftime('%Y-%m-%d %H:%M') + ' · 나만 보기')
    for row in requests:
        item = row.request
        closed = bool(item.closed_at or item.stage == 'rejected' or item.confirmed_at or (item.task_id and item.task.status == 'cancelled'))
        add('업무협조', row, row.event.created_at, row.request.title, row.event.action,
            'read_request_notification', [row.pk], post=True, button='처리 내역 보기' if closed else '요청 확인', detail=row.event.body[:150],
            closed=closed, status_label=item.state if closed else '')
    for row in quick:
        status = row.event.request.state
        closed = status in ('done','cancelled')
        add('간단 업무', row, row.event.created_at, row.event.request.title, row.event.action,
            'quick_detail', [row.event.request_id], button='처리 내역 보기' if closed else '간단 업무 확인',
            closed=closed, status_label={'done':'확인 완료','cancelled':'요청 취소'}.get(status,''))
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
        item = row.event.request
        final = item.state in ('approved','rejected','withdrawn','cancelled')
        old_pending = row.kind == 'pending' and not row.actionable
        closed = final or old_pending
        label = item.get_state_display() if final else ('내 처리 종료' if old_pending else '')
        message = f'휴가 신청 처리 상태: {label}' if old_pending else row.message
        add('휴가', row, row.created_at, title, message,
            None if old_pending else 'leave_detail', [] if old_pending else [row.event.request_id], closed=closed,
            status_label=label,
            detail=f"{snapshot.get('start', '')} ~ {snapshot.get('end', '')}",
            button='결재 내용 확인' if row.actionable else '결재 결과 확인')
    for row in proposals:
        item = row.event.proposal
        final = item.state in ('approved','rejected','withdrawn')
        old_pending = row.kind == 'pending' and not getattr(row, 'actionable', item.state == 'pending')
        closed = final or old_pending
        label = item.get_state_display() if final else ('내 처리 종료' if old_pending else '')
        message = f'품의 처리 상태: {label}' if row.kind == 'pending' and closed else row.message
        add('품의', row, row.created_at, item.title, message,
            'proposal_detail', [item.pk], button='처리 결과 보기' if closed else '품의 확인',
            closed=closed, status_label=label,
            detail=f'품의 #{item.pk} · {item.get_state_display()}')
    for row in certificates:
        item = row.event.request
        closed = item.state in ('issued','rejected','cancelled','revoked')
        label = item.get_state_display() if closed else ''
        add('증명서·서류', row, row.created_at, item.title,
            f'서류 신청 처리 상태: {label}' if closed and row.kind == 'pending' else ('증명서·서류 발급 요청' if row.kind == 'pending' else row.message),
            'certificate_detail', [item.pk], button='처리 결과 보기' if closed else '서류 신청 확인',
            closed=closed, status_label=label, detail=item.get_state_display())
    return sorted(feed, key=lambda item: (item['at'], item['kind'], item['pk']), reverse=True)
