from django.core.cache import cache
from django.core.management.base import BaseCommand

from dashboard.utils.app_logging import log_event


class Command(BaseCommand):
    help = 'Flush the Django Redis cache after an ECS deployment.'

    def add_arguments(self, parser):
        parser.add_argument('--reason', default='deployment startup')

    def handle(self, *args, **options):
        reason = options['reason']
        log_event(
            'Application cache clear started.',
            event_type='cache_clear',
            status='started',
            source='clear_application_cache',
            metadata={'reason': reason},
        )
        try:
            cache.clear()
        except Exception as exc:
            log_event(
                'Application cache clear failed; startup is stopping.',
                level='ERROR',
                event_type='cache_clear',
                status='failed',
                source='clear_application_cache',
                metadata={'reason': reason, 'error_type': type(exc).__name__},
                exc_info=exc,
            )
            raise
        log_event(
            'Application cache clear completed.',
            event_type='cache_clear',
            status='success',
            source='clear_application_cache',
            metadata={'reason': reason},
        )
        self.stdout.write(self.style.SUCCESS('Application cache flushed.'))
