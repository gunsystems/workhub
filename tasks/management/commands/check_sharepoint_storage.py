from django.core.management.base import BaseCommand, CommandError
from tasks.sharepoint_probe import check_repository, SharePointProbeError


class Command(BaseCommand):
    help = 'Check the dedicated SharePoint site without reading or changing files.'

    def handle(self, *args, **options):
        try:
            libraries = check_repository()
        except SharePointProbeError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(
            f'SharePoint 사이트 접근 확인: 문서함 {len(libraries)}개. 파일 변경 없음.'))

