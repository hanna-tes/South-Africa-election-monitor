import pandas as pd
import numpy as np
import re
import logging
import hashlib

logger = logging.getLogger(__name__)


class UploadValidationError(ValueError):
    """Raised when an uploaded file cannot match the selected source type."""


def validate_mapped_dataframe(df, filename=''):
    """Require the columns produced by the source-specific mapping stage."""
    required_columns = {
        'account_id',
        'content_id',
        'original_text',
        'URL',
        'timestamp_share',
        'Platform',
        'source_dataset',
    }
    missing_columns = sorted(required_columns.difference(df.columns))
    if missing_columns:
        missing = ', '.join(missing_columns)
        present = ', '.join(str(column) for column in df.columns) or '(none)'
        raise UploadValidationError(
            f"File '{filename}' could not be processed because the mapped data "
            f"is missing required field(s): {missing}. Mapped fields present: {present}."
        )

    blank_sources = df['source_dataset'].isna() | df['source_dataset'].astype(str).str.strip().isin({'', 'nan', 'None'})
    if blank_sources.any():
        row_number = int(df.index[blank_sources][0]) + 1
        raise UploadValidationError(
            f"File '{filename}' could not be processed because row {row_number} "
            "has a blank source_dataset value after mapping."
        )

    return True


def _rewind_file(file_obj):
    try:
        file_obj.seek(0)
    except (AttributeError, OSError):
        pass


def _is_empty_file(file_obj):
    size = getattr(file_obj, 'size', None)
    if size is not None:
        return size == 0
    try:
        current_position = file_obj.tell()
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(current_position)
        return size == 0
    except (AttributeError, OSError):
        return False


_TEXT_COLUMNS = {
    'brandwatch': {'full text', 'title'},
    'meltwater': {'hit sentence', 'opening text', 'headline', 'text', 'content'},
    'civicsignals': {'title', 'text', 'content', 'body'},
    'tiktok': {'text', 'transcript', 'caption', 'content'},
    'openmeasure': {'text', 'message', 'body'},
    'generic': {'original_text', 'text', 'content', 'body', 'message', 'title'},
}


def validate_upload_file(file_obj, data_type, filename=''):
    """Validate file shape before it enters the background processing queue."""
    if data_type not in _TEXT_COLUMNS:
        raise UploadValidationError(
            f"'{filename}' uses unsupported data type '{data_type}'. "
            "Choose one of the available upload types."
        )

    if not filename.lower().endswith('.csv'):
        raise UploadValidationError(f"'{filename}' is not a CSV file.")

    if _is_empty_file(file_obj):
        raise UploadValidationError(f"'{filename}' is empty.")

    try:
        _rewind_file(file_obj)
        read_options = {'nrows': 0, 'low_memory': False}
        if data_type == 'brandwatch':
            read_options['skiprows'] = 6
        header = pd.read_csv(file_obj, **read_options)
    except Exception as exc:
        raise UploadValidationError(
            f"'{filename}' could not be read as a CSV file: {exc}"
        ) from exc
    finally:
        _rewind_file(file_obj)

    if len(header.columns) < 2:
        raise UploadValidationError(
            f"'{filename}' has {len(header.columns)} column(s). "
            "It must contain at least two columns."
        )

    normalized_columns = {str(column).strip().lower() for column in header.columns}
    if not normalized_columns.intersection(_TEXT_COLUMNS[data_type]):
        expected = ', '.join(sorted(_TEXT_COLUMNS[data_type]))
        provided = ', '.join(str(column) for column in header.columns)
        raise UploadValidationError(
            f"'{filename}' does not look like a {data_type} export. "
            f"Required text columns include one of: {expected}. "
            f"Columns found: {provided}."
        )

    return {
        'columns': len(header.columns),
        'headers': list(header.columns),
    }

def get_col(df, cols):
    """Helper to find the first matching column name safely."""
    df_cols_lower = {c.lower().strip(): c for c in df.columns}
    for col in cols:
        norm = col.lower().strip()
        if norm in df_cols_lower:
            return df[df_cols_lower[norm]]
    return pd.Series([np.nan] * len(df), index=df.index)

def load_brandwatch_data(filepath):
    try:
        df = pd.read_csv(
            filepath, 
            encoding='utf-8', 
            sep=',', 
            low_memory=False, 
            skiprows=6,  # Skip metadata rows
            on_bad_lines='skip'
        )
    except Exception as e:
        logger.error(f"Brandwatch load failed: {e}")
        return pd.DataFrame()

    if df.empty:
        logger.warning("Brandwatch file loaded but is empty.")
        return df

    brandwatch_df = pd.DataFrame()
    
    # Use get_col for robust column matching
    brandwatch_df['account_id'] = get_col(df, ['Author', 'Full Name', 'author', 'full name']).str.strip().fillna('Unknown')
    brandwatch_df['original_text'] = get_col(df, ['Full Text', 'Title', 'full text', 'title']).fillna('')
    brandwatch_df['timestamp_share'] = get_col(df, ['Date', 'date', 'Timestamp'])
    brandwatch_df['URL'] = get_col(df, ['Url', 'URL', 'url', 'Link'])
    brandwatch_df['content_id'] = get_col(df, ['Resource Id', 'resource id', 'ID']).fillna(brandwatch_df['URL'])

    # Platform mapping with NaN safety
    page_type = get_col(df, ['Page Type', 'page type', 'Platform']).astype(str).str.lower()
    platform_map = {
        'twitter': 'X', 'x': 'X', 'facebook': 'Facebook', 'instagram': 'Instagram',
        'reddit': 'Reddit', 'youtube': 'YouTube', 'linkedin': 'LinkedIn',
        'tiktok': 'TikTok', 'threads': 'Threads', 'bluesky': 'Bluesky'
    }
    brandwatch_df['platform'] = page_type.map(platform_map).fillna('Unknown')
    brandwatch_df['platform'] = brandwatch_df['platform'].replace(['', 'nan'], 'Unknown')

    # Clean empty text
    brandwatch_df = brandwatch_df[brandwatch_df['original_text'].str.strip() != '']
    brandwatch_df['source_dataset'] = 'Brandwatch'
    
    logger.info(f"✅ Brandwatch processed: {len(brandwatch_df)} valid posts")
    return brandwatch_df
    
def map_columns_by_type(df, platform):
    """Maps platform-specific CSV headers to a standard format."""
    mapped = pd.DataFrame()

    # Brandwatch is handled by load_brandwatch_data, so we skip it here
    
    if platform == 'meltwater':
        mapped['account_id'] = get_col(df, ['Influencer', 'author', 'username', 'account'])
        mapped['content_id'] = get_col(df, ['tweet id', 'post id', 'id', 'ID'])
        mapped['object_id'] = get_col(df, ['Hit Sentence', 'text', 'content', 'opening text'])
        mapped['URL'] = get_col(df, ['URL', 'url', 'link', 'permalink'])
        mapped['timestamp_share'] = get_col(df, ['Date', 'timestamp', 'alternate date format'])

    elif platform == 'civicsignal':
        mapped['account_id'] = get_col(df, ['media_name', 'author', 'username'])
        mapped['content_id'] = get_col(df, ['stories_id', 'post_id', 'id', 'ID'])
        mapped['object_id'] = get_col(df, ['title', 'text', 'content', 'body'])
        mapped['URL'] = get_col(df, ['url', 'URL', 'link', 'permalink'])
        mapped['timestamp_share'] = get_col(df, ['publish_date', 'timestamp', 'date'])

    elif platform == 'tiktok':
        mapped['account_id'] = get_col(df, ['authorMeta/name', 'username', 'creator', 'author'])
        mapped['content_id'] = get_col(df, ['id', 'video_id', 'itemId', 'ID'])
        mapped['object_id'] = get_col(df, ['text', 'Transcript', 'caption', 'content'])
        mapped['URL'] = get_col(df, ['webVideoUrl', 'TikTok Link', 'url', 'URL', 'shareUrl'])
        mapped['timestamp_share'] = get_col(df, ['createTimeISO', 'timestamp', 'createTime'])

    elif platform == 'openmeasure':
        mapped['account_id'] = get_col(df, ['context_name', 'channelusername', 'channeltitle', 'actor_username'])
        mapped['content_id'] = get_col(df, ['id', 'url'])
        mapped['object_id'] = get_col(df, ['text', 'message', 'body'])
        mapped['URL'] = get_col(df, ['url', 'URL', 'link', 'permalink'])
        raw_dates = get_col(df, ['created_at', 'date'])
        mapped['timestamp_share'] = raw_dates.astype(str).str.replace(' @ ', ' ', regex=False)
    else:
        # Fallback or custom
        mapped = df.copy()

    # Standardize fields
    if platform != 'openmeasure':
        mapped['Platform'] = platform.upper()
    else:
        mapped['Platform'] = 'Telegram'
    mapped['source_dataset'] = platform

    # Generate content_id from URL if missing/empty
    if 'content_id' in mapped.columns:
        mask = mapped['content_id'].isna() | (mapped['content_id'] == '')
        if mask.any():
            mapped.loc[mask, 'content_id'] = mapped.loc[mask, 'URL'].apply(
                lambda x: hashlib.md5(str(x).encode()).hexdigest()[:16] 
                if pd.notna(x) and str(x).strip() != '' 
                else hashlib.md5(str(np.random.random())).hexdigest()[:16]
            )

    return mapped

def preprocess_dataframe(df):
    """Basic cleaning: remove empty rows and format text."""
    if df.empty:
        return df
    if 'object_id' in df.columns:
        df = df.dropna(subset=['object_id'])
        df['object_id'] = df['object_id'].astype(str).str.strip()
        df = df[df['object_id'] != "nan"]
    return df

def process_uploaded_csv(file, platform):
    """Main entry point for processing a single uploaded file."""
    try:
        if platform == 'brandwatch':
            # Brandwatch has its own loader that handles skiprows and specific mapping
            return load_brandwatch_data(file)
        else:
            df = pd.read_csv(file, low_memory=False, on_bad_lines='skip')
            mapped_df = map_columns_by_type(df, platform)
            clean_df = preprocess_dataframe(mapped_df)
            return clean_df
    except Exception as e:
        logger.error(f"Failed to process CSV for {platform}: {e}")
        return pd.DataFrame()

def combine_social_media_data(
    meltwater_df=None, brandwatch_df=None, civicsignals_df=None, 
    tiktok_df=None, openmeasures_df=None
):
    """Accepts single DF or list of DFs per source."""
    combined = []
    
    def add_source(df_source):
        if df_source is None:
            return
        if isinstance(df_source, list):
            for df in df_source:
                if df is not None and not df.empty:
                    combined.append(df)
        elif not df_source.empty:
            combined.append(df_source)
    
    add_source(meltwater_df)
    add_source(brandwatch_df)  # Now accepts list or single DF
    add_source(civicsignals_df)
    add_source(tiktok_df)
    add_source(openmeasures_df)

    return pd.concat(combined, ignore_index=True) if combined else pd.DataFrame()
