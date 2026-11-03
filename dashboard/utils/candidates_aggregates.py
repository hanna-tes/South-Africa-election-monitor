# dashboard/utils/candidates_aggregates.py
import pandas as pd
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

def get_candidates_aggregates(file_path: str) -> dict:
    """
    Read-only aggregate helper for the SA Municipal Candidates tab.
    Reads persons, candidacies, and contest sheets. 
    Cached for 24 hours to prevent heavy recomputation on 75k+ rows.
    """
    cache_key = "sa_candidates_aggregates_v1"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    try:
        # Load sheets (using pandas)
        # Note: Adjust sheet_name if your Excel file uses different casing
        persons = pd.read_excel(file_path, sheet_name='persons')
        candidacies = pd.read_excel(file_path, sheet_name='candidacies')
        contest = pd.read_excel(file_path, sheet_name='contest')

        # --- 1. PERSONS SHEET AGGREGATES ---
        total_candidates = len(persons)
        
        # Normalize municipality names by code (split on " - ")
        if 'municipality' in persons.columns:
            persons['muni_code'] = persons['municipality'].astype(str).str.split(' - ').str[0].str.strip()
            unique_municipalities = persons['muni_code'].nunique()
        else:
            unique_municipalities = 0

        unique_parties = persons['party_name'].nunique() if 'party_name' in persons.columns else 0
        independents = len(persons[persons['party_name'] == 'Independent']) if 'party_name' in persons.columns else 0

        # Gender Split
        gender_counts = persons['gender'].value_counts().to_dict() if 'gender' in persons.columns else {}
        gender_missing = int(persons['gender'].isna().sum()) if 'gender' in persons.columns else 0

        # Social Media Coverage (Prompt states 0 mapped, but we calculate dynamically if columns exist)
        social_cols = [c for c in persons.columns if any(k in c.lower() for k in ['fb', 'twitter', 'insta', 'social', 'linkedin'])]
        social_mapped = 0
        if social_cols:
            # If any social col has a non-null, non-empty string, it's mapped
            has_social = persons[social_cols].apply(lambda row: any(pd.notna(v) and str(v).strip() != '' for v in row), axis=1)
            social_mapped = int(has_social.sum())

        # Ward vs PR (File Data - Unverified Labelling)
        file_ward = 0
        file_pr = 0
        if 'office' in persons.columns:
            file_ward = int(persons['office'].astype(str).str.contains('Ward', case=False, na=False).sum())
            file_pr = int(persons['office'].astype(str).str.contains('PR', case=False, na=False).sum())

        # --- 2. CANDIDACIES SHEET AGGREGATES (Authoritative IEC) ---
        auth_ward = 0
        auth_pr = 0
        # Assuming column 'role' or similar indicates ward vs PR. Adjust column name as needed.
        if 'role' in candidacies.columns:
            auth_ward = int(candidacies['role'].astype(str).str.contains('Ward', case=False, na=False).sum())
            auth_pr = int(candidacies['role'].astype(str).str.contains('PR', case=False, na=False).sum())
        elif 'contest_type' in candidacies.columns:
            auth_ward = int((candidacies['contest_type'] == 'ward').sum())
            auth_pr = int((candidacies['contest_type'] == 'pr').sum())

        # --- 3. CONTEST SHEET AGGREGATES ---
        ward_contests = 0
        pr_contests = 0
        if 'contest_type' in contest.columns:
            ward_contests = int((contest['contest_type'] == 'ward').sum())
            pr_contests = int((contest['contest_type'] == 'pr').sum())
        elif 'type' in contest.columns:
            ward_contests = int((contest['type'] == 'Ward').sum())
            pr_contests = int((contest['type'] == 'PR').sum())

        aggregates = {
            'total_candidates': total_candidates,
            'unique_municipalities': unique_municipalities,
            'unique_parties': unique_parties,
            'independents': independents,
            'gender': gender_counts,
            'gender_missing': gender_missing,
            'social_mapped': social_mapped,
            'file_ward_count': file_ward,
            'file_pr_count': file_pr,
            'auth_ward_count': auth_ward,
            'auth_pr_count': auth_pr,
            'ward_contests': ward_contests,
            'pr_contests': pr_contests,
            'coverage_pct': round((total_candidates / 136790) * 100, 1) if total_candidates > 0 else 0
        }

        # Cache for 24 hours
        cache.set(cache_key, aggregates, 86400)
        logger.info(f"Cached SA Candidates Aggregates: {total_candidates} candidates processed.")
        return aggregates

    except Exception as e:
        logger.error(f"Failed to generate SA Candidates Aggregates: {e}")
        return {
            'total_candidates': 0, 'unique_municipalities': 0, 'unique_parties': 0,
            'independents': 0, 'gender': {}, 'gender_missing': 0, 'social_mapped': 0,
            'file_ward_count': 0, 'file_pr_count': 0, 'auth_ward_count': 0, 'auth_pr_count': 0,
            'ward_contests': 0, 'pr_contests': 0, 'coverage_pct': 0, 'error': str(e)
        }
