import os
import sys
import django
import pandas as pd
import hashlib
from django.utils import timezone

# 1. Setup Django Context Environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')
django.setup()

from dashboard.models import ProcessedPost, DataSource
from dashboard.utils.csv_processor import process_uploaded_csv, map_columns_by_type, preprocess_dataframe
from dashboard.utils.data_loader import parse_timestamp_robust

def safe_str(val):
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()

def map_brandwatch_columns(df):
    """Explicitly map Brandwatch export columns to our standard schema."""
    mapped = pd.DataFrame()
    
    # Account ID: Prefer Author, fallback to Full Name
    mapped['account_id'] = df.get('Author', df.get('Full Name', pd.Series(dtype='object'))).astype(str).str.strip().replace('nan', '')
    
    # Original Text: Prefer Full Text, fallback to Title
    mapped['original_text'] = df.get('Full Text', df.get('Title', pd.Series(dtype='object'))).astype(str).str.strip().replace('nan', '')
    
    # URL & Timestamp
    mapped['URL'] = df.get('Url', pd.Series(dtype='object'))
    mapped['timestamp_share'] = df.get('Date', pd.Series(dtype='object'))
    
    # Platform Mapping - EXPANDED FOR FACEBOOK
    page_type = df.get('Page Type', pd.Series(dtype='object')).astype(str).str.lower()
    platform_map = {
        'twitter': 'X', 'x': 'X', 'x.com': 'X', 't.co': 'X',
        'facebook': 'Facebook', 'fb': 'Facebook', 'fb.watch': 'Facebook', 'facebook.com': 'Facebook',
        'instagram': 'Instagram', 'insta': 'Instagram', 'ig': 'Instagram',
        'tiktok': 'TikTok', 'tik tok': 'TikTok',
        'youtube': 'YouTube', 'youtu.be': 'YouTube',
        'linkedin': 'LinkedIn', 'reddit': 'Reddit', 'telegram': 'Telegram', 't.me': 'Telegram'
    }
    mapped['Platform'] = page_type.map(platform_map)
    
    # Fallback: If URL contains facebook.com but Page Type didn't map, force Facebook
    mask = mapped['Platform'].isna() & mapped['URL'].astype(str).str.contains('facebook.com', case=False, na=False)
    mapped.loc[mask, 'Platform'] = 'Facebook'
    
    # Final fallback: default to 'Media' instead of 'X' for unmapped
    mapped['Platform'] = mapped['Platform'].fillna('Media')
    
    # Content ID fallback chain
    mapped['content_id'] = df.get('Resource Id', df.get('Mention Id', mapped['URL']))
    # Hash fallback for missing IDs
    mask = mapped['content_id'].isna() | (mapped['content_id'] == '') | (mapped['content_id'] == 'nan')
    if mask.any():
        mapped.loc[mask, 'content_id'] = mapped.loc[mask, 'URL'].apply(
            lambda x: hashlib.md5(str(x).encode()).hexdigest()[:16] if pd.notna(x) and str(x).strip() != '' else None
        )
        
    return mapped
    
folder = 'media/uploads/social_media'
print('🚀 Starting High-Yield Adaptive Batch Import (v9)...')

for filename in sorted(os.listdir(folder)):
    if not filename.endswith('.csv'): 
        continue
        
    filepath = os.path.join(folder, filename)
    print(f'\n📂 Processing: {filename}')

    name = filename.lower()
    dtype = 'custom'

    # Set platform type tags matching initialization layout
    if 'brandwatch' in name or 'hatespeech' in name or 'polarization' in name:
        dtype = 'brandwatch'
    elif 'meltwater' in name or 'x.csv' in name or 'apri2026x' in name: 
        dtype = 'meltwater'
    elif 'civicsignal' in name or 'media' in name: 
        dtype = 'civicsignal'
    elif 'openmeasure' in name: 
        dtype = 'openmeasure'
    elif 'tiktok' in name: 
        dtype = 'tiktok'

    try:
        # --- SMART EXPLICIT FORMAT HANDLING ---
        if dtype == 'meltwater':
            df = None
            for enc, sep in [('utf-16', '\t'), ('utf-8-sig', ','), ('latin-1', ',')]:
                try:
                    temp = pd.read_csv(filepath, encoding=enc, sep=sep, low_memory=False, on_bad_lines='skip')
                    if len(temp.columns) > 1:
                        df = temp
                        print(f"  📥 Loaded via {enc}/{sep} settings.")
                        break
                except: continue
                
            if df is not None:
                mapped_df = map_columns_by_type(df, 'meltwater')
                processed_df = preprocess_dataframe(mapped_df)
            else:
                processed_df = pd.DataFrame()

        elif dtype == 'brandwatch':
            try:
                # Brandwatch exports have 6 rows of metadata before the actual header
                df = pd.read_csv(filepath, encoding='utf-8', skiprows=6, low_memory=False, on_bad_lines='skip')
                processed_df = map_brandwatch_columns(df)
                processed_df = processed_df[processed_df['original_text'].str.len() > 10]  # Filter noise/empty rows
                print("  📥 Loaded & mapped Brandwatch export.")
            except Exception as e:
                print(f"  ❌ Brandwatch mapping failed: {e}")
                processed_df = pd.DataFrame()

        else:
            # Fallback to standard validation wrappers for other trackers
            processed_df = process_uploaded_csv(filepath, dtype)
        
        if processed_df is None or processed_df.empty:
            print("  ⚠️ No data rows parsed by the structural mapping configuration layout. Skipping.")
            continue
            
        print(f"  🔄 CSV Pipeline Output Verified. Total clean rows parsed: {len(processed_df)}")

        # --- DB INSERTION ---
        dropped_empty = 0
        dropped_duplicate = 0
        count = 0
        
        source_obj, _ = DataSource.objects.get_or_create(name=f"Import_{dtype}_{filename}")
        
        for _, row in processed_df.iterrows():
            text = safe_str(row.get('object_id') or row.get('original_text') or '')
            
            if not text or text.lower() in ['nan', 'none', '']: 
                dropped_empty += 1
                continue

            cid = safe_str(row.get('content_id') or '')
            url = safe_str(row.get('url') or row.get('URL') or row.get('link') or '')
            
            # Anomaly content checkpoint
            if not cid and not url:
                dropped_empty += 1
                continue

            # Check duplication parameters to preserve single entry unique indices 
            if cid and cid.lower() not in ['nan', 'none', ''] and ProcessedPost.objects.filter(content_id=cid).exists(): 
                dropped_duplicate += 1
                continue
            if url.startswith('http') and ProcessedPost.objects.filter(url=url).exists(): 
                dropped_duplicate += 1
                continue

            # Platform normalization
            plat = row.get('platform') or row.get('Platform') or dtype.title()
            plat_lower = str(plat).lower()
            if plat_lower in ['twitter', 'x', 'x.com', 't.co']: 
                plat = 'X'
            elif plat_lower in ['tiktok', 'tik tok', 'tik-tok']: 
                plat = 'TikTok'
            elif plat_lower in ['facebook', 'fb', 'fb.watch', 'facebook.com']: 
                plat = 'Facebook'
            elif plat_lower in ['instagram', 'insta', 'ig']: 
                plat = 'Instagram'
            elif plat_lower in ['telegram', 'tg', 't.me']: 
                plat = 'Telegram'
            elif plat_lower in ['youtube', 'yt', 'youtu.be']: 
                plat = 'YouTube'

            ProcessedPost.objects.create(
                account_id=safe_str(row.get('account_id', 'Unknown'))[:100],
                content_id=cid[:100] if cid else None,
                original_text=text,
                url=url[:500] if url.startswith('http') else None,
                platform=str(plat).title(),
                timestamp_share=parse_timestamp_robust(row.get('timestamp_share')),
                source_dataset=source_obj,
                is_election_related=True, 
                ingested_at=timezone.now()
            )
            count += 1

        print(f"  📊 Import Statistics for {filename}:")
        print(f"    - Null / Empty text discarded: {dropped_empty}")
        print(f"    - Existing database duplicates skipped: {dropped_duplicate}")
        print(f"  ✅ Saved {count} fully verified posts into your database system.")
        
    except Exception as e:
        print(f"  ❌ Import operation execution failure pipeline layer error: {e}")
        import traceback
        traceback.print_exc()

print('\n🏁 Import complete.')
