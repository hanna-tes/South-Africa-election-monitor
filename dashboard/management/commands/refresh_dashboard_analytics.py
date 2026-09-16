import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from dashboard.models import DashboardAnalyticsSnapshot, PEP, PostLexiconMatch, ProcessedPost
from dashboard.utils.app_logging import log_event
from dashboard.views import (
    build_analytics_snapshot_key,
    get_category_trend_analysis,
    get_enhanced_pep_analysis,
    get_ethiopia_summaries,
    get_risk_actors_insight,
    get_top_hashtags,
    scan_text_for_lexicon_terms,
    detect_hate_speech_afro_xlmr,
)


def json_safe(value):
    return json.loads(json.dumps(value, default=str))


class Command(BaseCommand):
    help = "Refresh precomputed dashboard analytics used by slow browsing pages."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='Number of recent days to precompute.')
        parser.add_argument('--view-all', action='store_true', help='Precompute using all available posts.')
        parser.add_argument('--skip-lexicons', action='store_true', help='Skip materialized lexicon detections.')
        parser.add_argument('--skip-afro-xlmr', action='store_true', help='Skip AFRO-XLMR AI discovery.')
        parser.add_argument('--skip-home', action='store_true', help='Skip home analytics snapshot.')
        parser.add_argument('--skip-narratives', action='store_true', help='Skip narrative summaries snapshot.')
        parser.add_argument('--skip-peps', action='store_true', help='Skip PEP mention analysis snapshot.')
        parser.add_argument('--batch-size', type=int, default=500, help='Bulk insert batch size for lexicon matches.')

    def handle(self, *args, **options):
        started_at = timezone.now()
        view_all = options['view_all']
        posts = ProcessedPost.objects.all().order_by('-timestamp_share')

        if view_all:
            date_range = posts.aggregate(min_date=Min('timestamp_share'), max_date=Max('timestamp_share'))
            start_date = date_range['min_date'] or started_at
            end_date = date_range['max_date'] or started_at
        else:
            end_date = started_at
            start_date = end_date - timedelta(days=options['days'])
            posts = posts.filter(timestamp_share__gte=start_date)

        log_event(
            'Refreshing dashboard analytics',
            event_type='analytics_refresh',
            status='started',
            source='refresh_dashboard_analytics',
            metadata={'view_all': view_all, 'days': options['days']},
        )

        if not options['skip_lexicons']:
            self.refresh_lexicon_matches(posts, options['batch_size'])

        if not options['skip_afro_xlmr']:
            self.refresh_afro_xlmr_discovery(posts)

        if not options['skip_home']:
            self.refresh_home_snapshot(posts, start_date, end_date, view_all)

        if not options['skip_narratives']:
            self.refresh_narratives_snapshot(posts, start_date, end_date, view_all)

        if not options['skip_peps']:
            self.refresh_peps_snapshot(posts, start_date, end_date, view_all)

        duration_ms = int((timezone.now() - started_at).total_seconds() * 1000)
        log_event(
            'Dashboard analytics refresh completed',
            event_type='analytics_refresh',
            status='success',
            source='refresh_dashboard_analytics',
            duration_ms=duration_ms,
        )
        self.stdout.write(self.style.SUCCESS(f"Dashboard analytics refreshed in {duration_ms}ms"))

    def refresh_lexicon_matches(self, posts, batch_size):
        self.stdout.write("Refreshing materialized lexicon matches...")
        posts = (
            posts
            .filter(original_text__isnull=False)
            .exclude(original_text='')
            .only('id', 'original_text')
        )

        deleted_count, _ = PostLexiconMatch.objects.filter(post__in=posts).delete()
        created_count = 0
        batch = []

        for post in posts.iterator(chunk_size=batch_size):
            for match in scan_text_for_lexicon_terms(post.original_text):
                if len(match.get('term', '').strip()) <= 1:
                    continue
                batch.append(PostLexiconMatch(
                    post_id=post.id,
                    term=match.get('term', ''),
                    category=match.get('category', ''),
                    severity=match.get('severity', 'medium'),
                    target_entity=match.get('target_entity', ''),
                    language=match.get('language', ''),
                ))
                if len(batch) >= batch_size:
                    created_count += self.flush_matches(batch)
                    batch = []

        if batch:
            created_count += self.flush_matches(batch)

        self.stdout.write(f"Materialized {created_count} lexicon matches; removed {deleted_count} old rows.")

    def flush_matches(self, batch):
        with transaction.atomic():
            created = PostLexiconMatch.objects.bulk_create(
                batch,
                batch_size=len(batch),
                ignore_conflicts=True,
            )
        return len(created)

    def refresh_afro_xlmr_discovery(self, posts):
        """Run AFRO-XLMR model on all posts to discover new hate speech categories and terms."""
        self.stdout.write("Running AFRO-XLMR AI discovery on all posts...")
        
        posts = (
            posts
            .filter(original_text__isnull=False)
            .exclude(original_text='')
            .only('id', 'original_text')
        )
        
        total_posts = posts.count()
        self.stdout.write(f"Processing {total_posts} posts with AFRO-XLMR model...")
        
        ai_discoveries = []
        processed_count = 0
        
        for post in posts.iterator(chunk_size=100):
            processed_count += 1
            if processed_count % 100 == 0:
                self.stdout.write(f"  Processed {processed_count}/{total_posts} posts...")
            
            try:
                result = detect_hate_speech_afro_xlmr(post.original_text)
                
                # Only store if it's actual hate speech (not neutral/error)
                if result.get('is_hate_speech') and result.get('confidence', 0) > 0.5:
                    ai_discoveries.append({
                        'post_id': post.id,
                        'category': result.get('category', 'Unknown'),
                        'confidence': round(result.get('confidence', 0) * 100, 1),
                        'severity': result.get('severity', 'medium'),
                        'detected_by': 'AFRO-XLMR',
                    })
            except Exception as e:
                # Silently skip errors to prevent batch failure
                continue
        
        # Store results in cache for 24 hours
        cache_key = 'lexicons_ai_discovery_v1'
        cache.set(cache_key, {
            'discoveries': ai_discoveries,
            'total_processed': processed_count,
            'total_hate_speech': len(ai_discoveries),
            'generated_at': timezone.now().isoformat(),
        }, 86400)  # 24 hours
        
        self.stdout.write(self.style.SUCCESS(
            f"AFRO-XLMR discovery complete: {len(ai_discoveries)} hate speech posts found out of {processed_count} processed."
        ))

    def refresh_home_snapshot(self, posts, start_date, end_date, view_all):
        self.stdout.write("Refreshing home analytics snapshot...")
        start_key = start_date.date().isoformat() if hasattr(start_date, 'date') else str(start_date)
        end_key = end_date.date().isoformat() if hasattr(end_date, 'date') else str(end_date)
        payload = {
            'risk_actors': get_risk_actors_insight(posts),
            'top_hashtags': get_top_hashtags(posts),
            'trend_analysis': get_category_trend_analysis(posts, days_back=90, cache_suffix=f"{start_key}_{end_key}"),
        }
        self.save_snapshot('home', start_date, end_date, view_all, payload)

    def refresh_narratives_snapshot(self, posts, start_date, end_date, view_all):
        self.stdout.write("Refreshing narrative summaries snapshot...")
        payload = {
            'summaries': get_ethiopia_summaries(posts),
        }
        self.save_snapshot('narratives', start_date, end_date, view_all, payload)

    def refresh_peps_snapshot(self, posts, start_date, end_date, view_all):
        self.stdout.write("Refreshing PEP mention analysis snapshot...")
        active_peps = PEP.objects.filter(is_active=True).order_by('name')
        election_posts = posts.filter(is_election_related=True).order_by('-timestamp_share')
        payload = {
            'pep_analysis': get_enhanced_pep_analysis(election_posts, active_peps, limit=8),
        }
        self.save_snapshot('peps', start_date, end_date, view_all, payload)

    def save_snapshot(self, kind, start_date, end_date, view_all, payload):
        key = build_analytics_snapshot_key(kind, start_date, end_date, view_all=view_all)
        DashboardAnalyticsSnapshot.objects.update_or_create(
            key=key,
            defaults={
                'kind': kind,
                'start_date': start_date.date() if hasattr(start_date, 'date') else None,
                'end_date': end_date.date() if hasattr(end_date, 'date') else None,
                'payload': json_safe(payload),
                'generated_at': timezone.now(),
                'source': 'refresh_dashboard_analytics',
            },
        )
