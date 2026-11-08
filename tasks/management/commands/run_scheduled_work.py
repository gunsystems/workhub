from django.core.management.base import BaseCommand
from tasks.scheduler import tick


class Command(BaseCommand):
    help='Process due personal occurrences, reminders and opted-in calendar exports once.'
    def handle(self,*args,**options): self.stdout.write(str(tick()))
