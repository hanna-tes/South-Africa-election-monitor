import logging
from django.core.management.base import BaseCommand
from django.core.cache import cache
from dashboard.models import ProcessedPost
from dashboard.views import detect_hate_speech_afro_xlmr

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Scans all posts in the database using the AFRO-XLMR model and caches the results for the Mapped Lexicons tab.'

    def handle(self, *args, **kwargs):
        self.stdout.write("🚀 Starting bulk AFRO-XLMR scan...")
        
        posts = ProcessedPost.objects.filter(original_text__isnull=False).exclude(original_text='').only('id', 'original_text')
        total_posts = posts.count()
        
        if total_posts == 0:
            self.stdout.write(self.style.WARNING("No posts found to scan."))
            return

        ai_results = {}
        scanned_count = 0

        # Process in batches to prevent memory spikes
        batch_size = 500
        for i in range(0, total_posts, batch_size):
            batch = posts[i:i + batch_size]
            for post in batch:
                try:
                    result = detect_hate_speech_afro_xlmr(post.original_text)
                    if result.get('is_hate_speech') and result.get('confidence', 0) > 0.5:
                        ai_results[str(post.id)] = {
                            'category': result.get('category', 'Unknown'),
                            'confidence': round(result.get('confidence', 0) * 100, 1),
                            'severity': result.get('severity', 'medium')
                        }
                    scanned_count += 1
                except Exception as e:
                    logger.error(f"Failed to scan post {post.id}: {e}")

        # Save to cache for 24 hours (86400 seconds)
        cache_key = 'afro_xlmr_lexicons_full_scan'
        cache.set(cache_key, ai_results, 86400)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Scan Complete! Scanned {scanned_count} posts. Found {len(ai_results)} AI-detected matches. Results cached for 24 hours."
        ))
