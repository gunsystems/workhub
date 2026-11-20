from django import template

register = template.Library()


@register.filter
def library_label(name):
    # Display only: never rename the remote library or change its identity.
    return {'전사공통': '전사공통 · 표준 자료',
            '전사 공통': '전사 공통 · 기존 자료'}.get(name, name)
