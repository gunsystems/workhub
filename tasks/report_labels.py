"""Current presentation labels without rewriting immutable report snapshots."""
from .models import PeriodReport


def display_kind(snapshot):
    names=dict(PeriodReport.REPORT_TYPES)
    return names.get(snapshot.get('report_type')) or {'개별 보고':'건별 보고','기간 보고':'기간 지정 보고'}.get(snapshot.get('report_type_label'),snapshot.get('report_type_label') or names['period'])
