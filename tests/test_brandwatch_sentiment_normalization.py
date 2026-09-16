import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')

import django

django.setup()

import pandas as pd

from dashboard.views import final_preprocess_and_map_columns


def test_brandwatch_lowercase_sentiment_values_are_retained():
    frame = pd.DataFrame(
        {
            'account_id': ['one', 'two', 'three'],
            'content_id': ['1', '2', '3'],
            'URL': ['https://example.test/1', 'https://example.test/2', 'https://example.test/3'],
            'timestamp_share': ['2026-08-24', '2026-08-24', '2026-08-24'],
            'Platform': ['X', 'X', 'X'],
            'original_text': ['negative text', 'neutral text', 'positive text'],
            'Sentiment': ['negative', 'neutral', 'positive'],
            'source_dataset': ['Brandwatch', 'Brandwatch', 'Brandwatch'],
        }
    )

    result = final_preprocess_and_map_columns(frame)

    assert len(result) == 2
    assert result['Sentiment'].tolist() == ['negative', 'neutral']
