from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0013_analytics_snapshots_and_lexicon_matches'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='processedpost',
            index=models.Index(fields=['timestamp_share'], name='idx_timestamp'),
        ),
        migrations.AddIndex(
            model_name='processedpost',
            index=models.Index(fields=['is_election_related'], name='idx_election_related'),
        ),
    ]
