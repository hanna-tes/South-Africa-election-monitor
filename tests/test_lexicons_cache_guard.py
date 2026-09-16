import os
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'election_monitor.settings')

import django

django.setup()

from django.test import Client

from dashboard import views


class EmptyQuerySet:
    def exclude(self, *args, **kwargs):
        return self

    def count(self):
        return 0

    def values(self, *args, **kwargs):
        return self

    def annotate(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def distinct(self, *args, **kwargs):
        return self

    def __getitem__(self, key):
        return self

    def __iter__(self):
        return iter(())


class FakePost:
    original_text = 'example post'
    platform = 'test'
    timestamp_share = None
    url = ''


class FakeMatch:
    post_id = '123'
    term = 'example'
    post = FakePost()


class FakeMatchQuerySet(EmptyQuerySet):
    def count(self):
        return 1

    def select_related(self, *args, **kwargs):
        return self

    def values(self, *args, **kwargs):
        return EmptyQuerySet()

    def __iter__(self):
        return iter((FakeMatch(),))


def test_lexicons_route_survives_list_shaped_ai_cache_value():
    empty_queryset = EmptyQuerySet()
    matches_queryset = FakeMatchQuerySet()

    with (
        patch.object(views, 'get_election_posts_queryset', return_value=(empty_queryset, datetime.now(timezone.utc), datetime.now(timezone.utc))),
        patch.object(views, 'get_materialized_lexicon_matches', return_value=matches_queryset),
        patch.object(views.LexiconTerm.objects, 'filter', return_value=empty_queryset),
        patch.object(views.LexiconsView, '_get_lexicon_term_count', return_value=0),
        patch.object(views.cache, 'get', return_value={'123': []}),
        patch.object(views, 'log_event'),
    ):
        response = Client().get('/lexicons/?category=test', HTTP_HOST='localhost')

    assert response.status_code == 200
