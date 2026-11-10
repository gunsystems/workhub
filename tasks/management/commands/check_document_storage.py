from django.core.management.base import BaseCommand, CommandError
from tasks.drive_storage import DriveClient, RepositoryError, configured


class Command(BaseCommand):
    help='Check private document storage without uploading files or printing credentials.'
    def handle(self,*args,**kwargs):
        if not configured():raise CommandError('Document repository is not configured.')
        try:name=DriveClient().check()
        except RepositoryError as exc:raise CommandError(str(exc)) from None
        self.stdout.write('Document repository access verified: '+name)
