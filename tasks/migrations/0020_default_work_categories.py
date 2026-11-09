from django.db import migrations


def seed(apps,schema_editor):
    Category=apps.get_model('tasks','WorkCategory')
    for name in ['일반 업무','미팅','제품 소개','제안서 작성','POC','정기점검','유지보수','보고서 작성','교육','테스트']:
        Category.objects.get_or_create(name=name)
    apps.get_model('tasks','Task').objects.filter(category__isnull=True).update(category=Category.objects.get(name='일반 업무'))


class Migration(migrations.Migration):
    dependencies=[('tasks','0019_report_files_and_linked_work')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
