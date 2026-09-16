import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')

import django

django.setup()

import pandas as pd

from dashboard.views import combine_social_media_data


def test_brandwatch_mapping_produces_worker_fields():
    source = pd.DataFrame(
        {
            'Weblog Title': ['example-account'],
            'Full Text': ['example post'],
            'Url': ['https://example.test/post'],
            'Date': ['2026-08-26'],
            'Page Type': ['twitter'],
            'Resource Id': ['resource-1'],
        }
    )

    result = combine_social_media_data(brandwatch_df=source)

    assert result.loc[0, 'source_dataset'] == 'Brandwatch'
    assert result.loc[0, 'original_text'] == 'example post'
    assert result.loc[0, 'content_id'] == 'resource-1'
