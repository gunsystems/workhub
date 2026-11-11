from django import template
from tasks.report_labels import display_kind

register=template.Library()
register.filter('report_kind',display_kind)
