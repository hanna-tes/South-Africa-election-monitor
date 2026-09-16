import os
from io import StringIO
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')

import django

django.setup()

from django.core.management import call_command


def test_clear_application_cache_calls_default_cache_clear():
    with (
        patch('dashboard.management.commands.clear_application_cache.cache.clear') as clear_cache,
        patch('dashboard.management.commands.clear_application_cache.log_event'),
    ):
        output = StringIO()
        call_command('clear_application_cache', reason='test', stdout=output)

    clear_cache.assert_called_once_with()
    assert 'Application cache flushed.' in output.getvalue()
