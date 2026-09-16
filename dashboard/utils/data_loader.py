import pandas as pd
import requests
import re
from io import StringIO
from datetime import datetime
from django.utils import timezone


def rewind_file(file_obj):
    try:
        file_obj.seek(0)
    except (AttributeError, OSError):
        pass

def parse_timestamp_robust(timestamp):
    """
    Standardizes various timestamp formats found in Meltwater, 
    TikTok, and OpenMeasure exports. Ensures timezone-aware output 
    to prevent Django USE_TZ warnings.
    """
    if pd.isna(timestamp) or timestamp is None or str(timestamp).strip() == "":
        return None
    
    # If already a datetime object, ensure it's timezone-aware
    if isinstance(timestamp, datetime):
        return timestamp if timezone.is_aware(timestamp) else timezone.make_aware(timestamp)
    
    ts_str = re.sub(r'\s+GMT$', '', str(timestamp).strip(), flags=re.IGNORECASE)
    
    # Try standard pandas parsing first
    try:
        parsed = pd.to_datetime(ts_str, errors='coerce', utc=True)
        if pd.notna(parsed):
            dt = parsed.to_pydatetime()
            return dt if timezone.is_aware(dt) else timezone.make_aware(dt)
    except: 
        pass
    
    # Fallback to specific formats
    formats = [
        '%Y-%m-%d %H:%M:%S', 
        '%Y-%m-%d %H:%M', 
        '%d/%m/%Y %H:%M', 
        '%b %d, %Y %H:%M', 
        '%Y-%m-%d'
    ]
    
    for fmt in formats:
        try:
            parsed = pd.to_datetime(ts_str, format=fmt, errors='coerce', utc=True)
            if pd.notna(parsed):
                dt = parsed.to_pydatetime()
                return dt if timezone.is_aware(dt) else timezone.make_aware(dt)
        except: 
            continue
            
    return None

def load_data_robustly(file_path, original_name=None):
    """
    Advanced loader that handles Meltwater's specific UTF-16 Tab-Separated format
    as well as standard UTF-8 CSVs.
    """
    # Attempt 1: Try Meltwater Style (UTF-16, Tab Separated)
    try:
        rewind_file(file_path)
        df = pd.read_csv(file_path, encoding='utf-16', sep='\t', low_memory=False, on_bad_lines='skip')
        if len(df.columns) > 1:
            if original_name:
                print(f"✅ Loaded {original_name} as Meltwater (UTF-16/Tab)")
            return df
    except Exception:
        pass 

    # Attempt 2: Try Standard CSV (UTF-8)
    try:
        rewind_file(file_path)
        df = pd.read_csv(file_path, encoding='utf-8', low_memory=False, on_bad_lines='skip')
        if len(df.columns) > 1:
            return df
    except Exception:
        pass

    # Attempt 3: Try UTF-8 with Byte Order Mark (BOM)
    try:
        rewind_file(file_path)
        df = pd.read_csv(file_path, encoding='utf-8-sig', low_memory=False, on_bad_lines='skip')
        if len(df.columns) > 1:
            return df
    except Exception:
        pass

    # Attempt 4: Last resort (Latin-1)
    try:
        rewind_file(file_path)
        df = pd.read_csv(file_path, encoding='latin1', low_memory=False, on_bad_lines='skip')
        return df
    except Exception as e:
        print(f"❌ All loading attempts failed for {original_name or file_path}: {e}")
        return pd.DataFrame()

def load_peps_from_github(csv_url):
    """Load PEPs from GitHub CSV (skips title row to avoid header mismatch)."""
    try:
        resp = requests.get(csv_url, timeout=30)
        resp.raise_for_status()
        lines = resp.text.split('\n')
        # Skip row 0 (title/metadata), keep headers + data
        content = '\n'.join([line for line in lines[1:] if line.strip()])
        df = pd.read_csv(StringIO(content))
        return df.to_dict('records')
    except Exception as e:
        print(f"❌ Failed to load PEPs: {e}")
        return []
