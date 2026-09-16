from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('dashboard', '0008_alter_monitoringreport_report_category_and_more'),
    ]
    
    operations = [
        # Index for fast date filtering
        migrations.AddIndex(
            model_name='processedpost',
            index=models.Index(fields=['timestamp_share'], name='idx_timestamp'),
        ),
        # Index for fast category filtering
        migrations.AddIndex(
            model_name='lexiconterm',
            index=models.Index(fields=['category', 'severity'], name='idx_category_severity'),
        ),
        
        # Index for election-related posts
        migrations.AddIndex(
            model_name='processedpost',
            index=models.Index(fields=['is_election_related'], name='idx_election_related'),
        ),
    ]
