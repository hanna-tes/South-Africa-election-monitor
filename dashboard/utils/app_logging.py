import logging
import threading
import time
import traceback as traceback_module
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, OperationalError, ProgrammingError
from django.utils import timezone


_local_state = threading.local()
_last_prune_at = 0


def _safe_metadata(metadata):
    if not metadata:
        return {}
    if isinstance(metadata, dict):
        return metadata
    return {'value': str(metadata)}


def _normalize_level(level):
    return str(level or 'INFO').upper()


def _status_for_level(level):
    level = _normalize_level(level)
    if level in {'ERROR', 'CRITICAL'}:
        return 'failed'
    if level == 'WARNING':
        return 'warning'
    return 'info'


def _prune_old_logs():
    global _last_prune_at

    retention_days = getattr(settings, 'APPLICATION_LOG_RETENTION_DAYS', 90)
    if not retention_days:
        return

    now = time.monotonic()
    if now - _last_prune_at < 3600:
        return
    _last_prune_at = now

    from dashboard.models import ApplicationLog

    cutoff = timezone.now() - timedelta(days=retention_days)
    ApplicationLog.objects.filter(created_at__lt=cutoff).delete()


def log_event(
    message,
    *,
    level='INFO',
    logger_name='dashboard.events',
    event_type='application',
    status=None,
    actor='',
    source='',
    object_type='',
    object_id='',
    request_path='',
    duration_ms=None,
    metadata=None,
    exc_info=None,
):
    """Persist a structured operational event without raising on logging failures."""
    if getattr(_local_state, 'writing_log', False):
        return None

    _local_state.writing_log = True
    try:
        from dashboard.models import ApplicationLog

        _prune_old_logs()

        traceback_text = ''
        if exc_info:
            if exc_info is True:
                traceback_text = traceback_module.format_exc()
            elif isinstance(exc_info, tuple):
                traceback_text = ''.join(traceback_module.format_exception(*exc_info))
            else:
                traceback_text = str(exc_info)

        return ApplicationLog.objects.create(
            level=_normalize_level(level),
            logger_name=str(logger_name or '')[:255],
            event_type=str(event_type or '')[:100],
            status=status or _status_for_level(level),
            message=str(message),
            actor=str(actor or '')[:255],
            source=str(source or '')[:255],
            object_type=str(object_type or '')[:100],
            object_id=str(object_id or '')[:255],
            request_path=str(request_path or '')[:500],
            duration_ms=duration_ms,
            metadata=_safe_metadata(metadata),
            traceback=traceback_text,
        )
    except (OperationalError, ProgrammingError, DatabaseError):
        return None
    except Exception:
        return None
    finally:
        _local_state.writing_log = False


class DatabaseLogHandler(logging.Handler):
    """Logging handler that mirrors Python log records into ApplicationLog."""

    def emit(self, record):
        if getattr(_local_state, 'writing_log', False):
            return
        if record.name.startswith('django.db.backends'):
            return

        metadata = {
            'module': record.module,
            'func_name': record.funcName,
            'pathname': record.pathname,
            'lineno': record.lineno,
            'process': record.process,
            'thread': record.threadName,
        }
        request = getattr(record, 'request', None)
        request_path = getattr(request, 'path', '') if request else ''

        log_event(
            self.format(record),
            level=record.levelname,
            logger_name=record.name,
            event_type=getattr(record, 'event_type', 'log_record'),
            status=getattr(record, 'status', None),
            request_path=request_path,
            metadata=metadata,
            exc_info=record.exc_info,
        )
