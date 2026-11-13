from decimal import Decimal, InvalidOperation
from django import template
register=template.Library()

@register.filter
def won(value):
    try:
        amount=Decimal(str(value))
        return format(amount, ',.0f') if amount.is_finite() else ''
    except (InvalidOperation, ValueError, TypeError):
        return ''
