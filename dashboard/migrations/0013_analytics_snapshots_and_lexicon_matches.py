from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0012_applicationlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='DashboardAnalyticsSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=255, unique=True)),
                ('kind', models.CharField(db_index=True, max_length=100)),
                ('start_date', models.DateField(blank=True, db_index=True, null=True)),
                ('end_date', models.DateField(blank=True, db_index=True, null=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('generated_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('source', models.CharField(blank=True, max_length=100)),
            ],
            options={
                'ordering': ['-generated_at'],
                'indexes': [
                    models.Index(fields=['kind', 'generated_at'], name='dashboard_a_kind_57cda1_idx'),
                    models.Index(fields=['kind', 'start_date', 'end_date'], name='dashboard_a_kind_6b6408_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='PostLexiconMatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('term', models.CharField(db_index=True, max_length=255)),
                ('category', models.CharField(db_index=True, max_length=100)),
                ('severity', models.CharField(db_index=True, max_length=20)),
                ('target_entity', models.CharField(blank=True, max_length=255)),
                ('language', models.CharField(blank=True, max_length=20)),
                ('detected_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='materialized_lexicon_matches', to='dashboard.processedpost')),
            ],
            options={
                'ordering': ['-detected_at'],
                'indexes': [
                    models.Index(fields=['category', 'detected_at'], name='postlex_cat_detect_idx'),
                    models.Index(fields=['severity', 'detected_at'], name='postlex_sev_detect_idx'),
                    models.Index(fields=['term', 'detected_at'], name='dashboard_p_term_6f767a_idx'),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name='postlexiconmatch',
            constraint=models.UniqueConstraint(fields=('post', 'term', 'category'), name='uniq_post_lexicon_match'),
        ),
    ]
