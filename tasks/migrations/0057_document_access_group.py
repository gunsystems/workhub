from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('tasks', '0056_document_permission_management')]
    operations = [migrations.CreateModel(name='DocumentAccessGroup', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('site_url', models.URLField(max_length=500)),
        ('principal_id', models.PositiveIntegerField()),
        ('name', models.CharField(max_length=100)),
        ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
    ], options={'constraints': [models.UniqueConstraint(fields=('site_url', 'principal_id'), name='document_group_site_id')]})]
