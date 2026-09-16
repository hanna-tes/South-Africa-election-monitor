import logging

from django.http import JsonResponse

from dashboard.utils.tei_detector import predict


logger = logging.getLogger(__name__)

TEI_HEALTH_CASES = (
    ('hate', 'Those people are disgusting and should be driven out.', True),
    ('neutral', 'The election commission published the results after a peaceful vote.', False),
    ('okay', 'Thank you for sharing this information respectfully.', False),
)


def tei_health_check() -> bool:
    """Confirm the TEI sidecar returns the expected result for three examples."""
    healthy = True
    for name, text, expected_hate_speech in TEI_HEALTH_CASES:
        try:
            result = predict(text, timeout_seconds=5.0)
            if result.get('is_hate_speech') is not expected_hate_speech:
                logger.error(
                    'TEI health check failed for %s sample: expected is_hate_speech=%s',
                    name,
                    expected_hate_speech,
                )
                healthy = False
        except Exception:
            logger.exception('TEI health check failed while calling the sidecar for %s sample', name)
            healthy = False
    return healthy


def health_response():
    if tei_health_check():
        return JsonResponse({'status': 'ok', 'tei': 'ok'})
    return JsonResponse({'status': 'error', 'tei': 'unhealthy'}, status=503)


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/health/' and request.method in {'GET', 'HEAD'}:
            return health_response()
        return self.get_response(request)
