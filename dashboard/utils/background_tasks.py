"""
Background task processor for LLM-heavy operations.
Uses Django's threading + database queue for async processing.
"""
import threading
import logging
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)

# Global lock to prevent multiple background tasks
_processing_lock = threading.Lock()

def start_background_llm_scan(post_ids, user_id=None):
    """
    Start LLM processing in background thread.
    Returns immediately - processing happens asynchronously.
    """
    if _processing_lock.acquire(blocking=False):
        thread = threading.Thread(
            target=_process_llm_scan,
            args=(post_ids, user_id),
            daemon=True
        )
        thread.start()
        logger.info(f"Started background LLM scan for {len(post_ids)} posts")
        return True
    else:
        logger.info("Background LLM scan already running, skipping")
        return False

def _process_llm_scan(post_ids, user_id=None):
    """
    Process LLM extraction for posts in batches.
    Updates database with new terms found.
    """
    try:
        from dashboard.models import ProcessedPost, LexiconTerm, LLMScanLog
        from .lexicon_engine import extract_new_trigger_terms_llm
        
        posts = ProcessedPost.objects.filter(id__in=post_ids)
        new_terms_found = 0
        posts_processed = 0
        
        for post in posts.iterator():
            if not post.original_text or len(post.original_text.strip()) < 20:
                continue
            
            # Extract new terms via LLM
            new_terms = extract_new_trigger_terms_llm(post.original_text)
            
            # Save high-confidence terms to database
            for term_data in new_terms:
                if term_data.get('severity') in ['high', 'critical'] and term_data.get('confidence', 0) >= 0.7:
                    term, created = LexiconTerm.objects.get_or_create(
                        term=term_data['term'].lower().strip(),
                        defaults={
                            'category': term_data.get('category', 'uncategorized'),
                            'severity': term_data.get('severity', 'medium'),
                            'target_entity': term_data.get('target_entity', ''),
                            'language': term_data.get('language', 'english'),
                            'is_election_related': True,
                            'is_active': True,
                            'discovered_by': 'llm',
                            'discovered_at': timezone.now()
                        }
                    )
                    if created:
                        new_terms_found += 1
            
            posts_processed += 1
            
            # Log progress every 50 posts
            if posts_processed % 50 == 0:
                logger.info(f"LLM scan progress: {posts_processed}/{len(post_ids)} posts, {new_terms_found} new terms")
        
        # Create scan log entry
        LLMScanLog.objects.create(
            posts_scanned=posts_processed,
            new_terms_found=new_terms_found,
            completed_at=timezone.now(),
            triggered_by_user_id=user_id
        )
        
        logger.info(f"LLM scan complete: {posts_processed} posts, {new_terms_found} new terms")
        
    except Exception as e:
        logger.error(f"Background LLM scan failed: {e}", exc_info=True)
    finally:
        _processing_lock.release()
