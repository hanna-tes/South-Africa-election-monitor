from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0011_lexiconterm_justification'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApplicationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('level', models.CharField(choices=[('DEBUG', 'Debug'), ('INFO', 'Info'), ('WARNING', 'Warning'), ('ERROR', 'Error'), ('CRITICAL', 'Critical')], db_index=True, default='INFO', max_length=20)),
                ('logger_name', models.CharField(blank=True, db_index=True, max_length=255)),
                ('event_type', models.CharField(blank=True, db_index=True, max_length=100)),
                ('status', models.CharField(choices=[('started', 'Started'), ('success', 'Success'), ('skipped', 'Skipped'), ('warning', 'Warning'), ('failed', 'Failed'), ('info', 'Info')], db_index=True, default='info', max_length=20)),
                ('message', models.TextField()),
                ('actor', models.CharField(blank=True, max_length=255)),
                ('source', models.CharField(blank=True, db_index=True, max_length=255)),
                ('object_type', models.CharField(blank=True, max_length=100)),
                ('object_id', models.CharField(blank=True, max_length=255)),
                ('request_path', models.CharField(blank=True, max_length=500)),
                ('duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('traceback', models.TextField(blank=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['created_at', 'level'], name='dashboard_a_created_154f5e_idx'),
                    models.Index(fields=['event_type', 'status'], name='dashboard_a_event_t_f3b3b9_idx'),
                    models.Index(fields=['source', 'created_at'], name='dashboard_a_source_519066_idx'),
                ],
            },
        ),
    ]
