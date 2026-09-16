import io

import pandas as pd
import pytest

from dashboard.utils.csv_processor import (
    UploadValidationError,
    validate_mapped_dataframe,
    validate_upload_file,
)


def test_brandwatch_file_requires_text_column():
    file_obj = io.BytesIO(b"metadata\n" * 6 + b"id,wrong\n1,value\n")

    with pytest.raises(UploadValidationError, match="Columns found: id, wrong"):
        validate_upload_file(file_obj, 'brandwatch', 'Ethiopia.csv')


def test_empty_file_is_rejected():
    file_obj = io.BytesIO()

    with pytest.raises(UploadValidationError, match="is empty"):
        validate_upload_file(file_obj, 'meltwater', 'empty.csv')


def test_valid_standard_csv_is_accepted():
    file_obj = io.BytesIO(b"text,url\nhello,https://example.test\n")

    result = validate_upload_file(file_obj, 'meltwater', 'posts.csv')

    assert result['columns'] == 2


def test_mapped_data_requires_source_dataset():
    mapped = pd.DataFrame({'original_text': ['hello']})

    with pytest.raises(UploadValidationError, match='source_dataset.*Mapped fields present'):
        validate_mapped_dataframe(mapped, 'posts.csv')


def test_mapped_data_rejects_blank_source_dataset():
    mapped = pd.DataFrame(
        {
            'account_id': ['account'],
            'content_id': ['content'],
            'original_text': ['hello'],
            'URL': ['https://example.test/post'],
            'timestamp_share': ['2026-08-26'],
            'Platform': ['X'],
            'source_dataset': [''],
        }
    )

    with pytest.raises(UploadValidationError, match='row 1'):
        validate_mapped_dataframe(mapped, 'posts.csv')
