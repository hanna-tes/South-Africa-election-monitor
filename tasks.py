# tasks.py
import re
import logging
from django.core.cache import cache
from django.db import transaction
from .models import ProcessedPost, PEPRegistry, LexiconKeyword
# Assuming these helper functions are in your views or a utils file:
from .views import get_ethiopia_summaries, get_coordination_groups

logger = logging.getLogger(__name__)

# --- Cache Key Definitions ---
SUMMARIES_CACHE_KEY = "election_monitor_summaries"
COORDINATION_CACHE_KEY = "election_monitor_coordination"
TOTAL_POSTS_CACHE_KEY = "election_monitor_total_count"
PEP_CACHE_KEY = "election_monitor_peps_data"
LEXICON_ANALYTICS_CACHE_KEY = "election_monitor_lexicon_analytics"


def refresh_dashboard_metrics_task():
    """
    1. OVERVIEW & NARRATIVES TABS
    Background task to pre-compute heavy operations (DBSCAN clustering,
    TF-IDF matrix compilation, LLM summaries) and store results in Redis.
    """
    logger.info("Starting background pre-computation of dashboard metrics...")
    try:
        all_posts = ProcessedPost.objects.all().order_by('-timestamp_share')
        
        # Cache total counts
        total_posts = all_posts.count()
        cache.set(TOTAL_POSTS_CACHE_KEY, total_posts, timeout=None)
        
        # Compute text clustering & IMI-style summaries
        logger.info("Computing Ethiopia summaries cluster task...")
        summaries = get_ethiopia_summaries(all_posts)
        cache.set(SUMMARIES_CACHE_KEY, summaries, timeout=None)
        
        # Compute sparse-matrix coordination network groups
        logger.info("Computing coordination groups network task...")
        coordination = get_coordination_groups(all_posts)
        cache.set(COORDINATION_CACHE_KEY, coordination, timeout=None)
        
        logger.info("✅ Dashboard metrics successfully updated in Redis cache.")
        return True
    except Exception as e:
        logger.error(f"❌ Error during background metrics calculation: {str(e)}")
        return False


def extract_and_cache_peps_task():
    """
    2. PEPS TAB
    Scans un-analyzed posts against the PEP Registry using efficient regex compilation,
    updates database records asynchronously, and caches aggregated stats.
    """
    logger.info("Starting background PEP extraction and matching...")
    try:
        peps = list(PEPRegistry.objects.all().values('id', 'name', 'associated_party'))
        if not peps:
            cache.set(PEP_CACHE_KEY, {}, timeout=None)
            return True
        
        # Build efficient single-pass matching regex pattern
        pattern_map = {p['name'].lower(): p for p in peps}
        escaped_names = [re.escape(name) for name in pattern_map.keys()]
        combined_regex = re.compile(r'\b(' + '|'.join(escaped_names) + r')\b', re.IGNORECASE)

        # Process a chunk of unscanned posts to prevent locking the database
        posts = ProcessedPost.objects.filter(pep_scanned=False)[:5000]
        
        with transaction.atomic():
            for post in posts:
                if post.clean_text:
                    matches = combined_regex.findall(post.clean_text)
                    if matches:
                        matched_pep_ids = [pattern_map[m.lower()]['id'] for m in set(matches)]
                        post.detected_peps.add(*matched_pep_ids)
                post.pep_scanned = True
                post.save()

        # Generate aggregated metrics for the PEP view layout (e.g., top mentioned profiles)
        # TODO: Replace with your custom aggregation dictionary if needed
        pep_analytics = {"status": "Updated", "total_scanned_peps": len(peps)}
        cache.set(PEP_CACHE_KEY, pep_analytics, timeout=None)
        
        logger.info("✅ PEP scanning and analytics caching complete.")
        return True
    except Exception as e:
        logger.error(f"❌ Error during PEP extraction: {str(e)}")
        return False


def compute_lexicon_hit_rates_task():
    """
    3. LEXICON VIEW & MANAGEMENT TABS
    Scans text data via database streaming to calculate category metric weights.
    Saves results completely to Redis without hitting the UI threads.
    """
    logger.info("Starting background Lexicon compilation...")
    try:
        keywords_by_category = {}
        queryset = LexiconKeyword.objects.all().values('word', 'category__name')
        
        for item in queryset:
            cat = item['category__name']
            word = item['word'].lower()
            if cat not in keywords_by_category:
                keywords_by_category[cat] = []
            keywords_by_category[cat].append(re.escape(word))
            
        if not keywords_by_category:
            cache.set(LEXICON_ANALYTICS_CACHE_KEY, {}, timeout=None)
            return True

        compiled_regexes = {
            category: re.compile(r'\b(' + '|'.join(words) + r')\b', re.IGNORECASE)
            for category, words in keywords_by_category.items() if words
        }

        analytics_payload = {
            "category_counts": {cat: 0 for cat in compiled_regexes.keys()},
            "top_keywords": {},
            "total_flagged_posts": 0
        }

        # Stream posts out of the database efficiently via a memory-safe iterator
        post_stream = ProcessedPost.objects.all().values_list('clean_text', flat=True).iterator(chunk_size=2000)

        for post_text in post_stream:
            if not post_text:
                continue
                
            is_flagged = False
            for category, regex_pattern in compiled_regexes.items():
                matches = regex_pattern.findall(post_text)
                if matches:
                    is_flagged = True
                    analytics_payload["category_counts"][category] += len(matches)
                    
                    for match in matches:
                        m_lower = match.lower()
                        analytics_payload["top_keywords"][m_lower] = analytics_payload["top_keywords"].get(m_lower, 0) + 1
                        
            if is_flagged:
                analytics_payload["total_flagged_posts"] += 1

        cache.set(LEXICON_ANALYTICS_CACHE_KEY, analytics_payload, timeout=None)
        logger.info("✅ Lexicon analytics successfully pre-computed and cached.")
        return True
    except Exception as e:
        logger.error(f"❌ Error during Lexicon compilation: {str(e)}")
        return False


def run_global_dashboard_refresh():
    """
    4. MASTER PIPELINE ORCHESTRATOR
    Can be assigned to a single cron schedule inside Django-Q to refresh
    the global system cache data sequentially.
    """
    logger.info("=== Starting Master Dashboard Background Pipeline ===")
    refresh_dashboard_metrics_task()
    extract_and_cache_peps_task()
    compute_lexicon_hit_rates_task()
    logger.info("=== Master Dashboard Background Pipeline Finished ===")
