import hashlib
import json
from collections import Counter

from django.core.cache import cache
from django.utils import timezone

from dashboard.models import ProcessedPost


def build_analytics_snapshot_key(kind, start_date=None, end_date=None, view_all=False):
    """
    Builds a deterministic, unique cache key based on snapshot kind and filter bounds.
    Matches the original cache key generation scheme.
    """
    start_key = start_date.date().isoformat() if hasattr(start_date, 'date') else str(start_date or '')
    end_key = end_date.date().isoformat() if hasattr(end_date, 'date') else str(end_date or '')
    raw_key = f"{kind}:{start_key}:{end_key}:{bool(view_all)}"
    digest = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
    return f"analytics_snapshot:{kind}:{digest}"


def get_filter_cache_key(prefix: str, query_params: dict) -> str:
    """
    Convenience alias for build_analytics_snapshot_key to support query-param dict signatures.
    """
    return build_analytics_snapshot_key(
        kind=prefix,
        start_date=query_params.get('start_date'),
        end_date=query_params.get('end_date'),
        view_all=str(query_params.get('view_all', '')).lower() in ['true', '1']
    )


def compute_filtered_analytics(snapshot_type: str, params: dict) -> dict:
    """
    Calculates analytics snapshots using the original PR computation logic.
    Imports helper functions locally to prevent circular import errors.
    """
    from dashboard.views import (
        get_category_trend_analysis,
        get_ethiopia_summaries,
        get_risk_actors_insight,
        get_top_hashtags,
    )

    qs = ProcessedPost.objects.all().order_by('-timestamp_share')

    # Apply date bounds if present
    if params.get('start_date'):
        qs = qs.filter(timestamp_share__gte=params['start_date'])
    if params.get('end_date'):
        qs = qs.filter(timestamp_share__lte=params['end_date'])

    # Apply platform filter if present
    if params.get('platform'):
        qs = qs.filter(platform__iexact=params['platform'])

    view_all = str(params.get('view_all', '')).lower() in ['true', '1']

    if snapshot_type == 'home':
        start_key = str(params.get('start_date', ''))
        end_key = str(params.get('end_date', ''))
        return {
            'risk_actors': get_risk_actors_insight(qs),
            'top_hashtags': get_top_hashtags(qs),
            'trend_analysis': get_category_trend_analysis(
                qs,
                days_back=90,
                cache_suffix=f"{start_key}_{end_key}"
            ),
            'generated_at': timezone.now()
        }

    elif snapshot_type == 'narratives':
        return {
            'summaries': get_ethiopia_summaries(qs),
            'generated_at': timezone.now()
        }

    return {}


def get_analytics_snapshot(snapshot_type: str, request_params: dict):
    """
    Tiered fetch: Checks low-level Django Cache first, falls back on computing dynamically.
    """
    cache_key = build_analytics_snapshot_key(
        kind=snapshot_type,
        start_date=request_params.get('start_date'),
        end_date=request_params.get('end_date'),
        view_all=str(request_params.get('view_all', '')).lower() in ['true', '1']
    )

    cached_payload = cache.get(cache_key)
    if cached_payload:
        return cached_payload, cached_payload.get('generated_at')

    payload = compute_filtered_analytics(snapshot_type, request_params)
    cache.set(cache_key, payload, timeout=86400)

    return payload, payload.get('generated_at')
