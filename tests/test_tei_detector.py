import pytest

from dashboard.utils import tei_detector


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_tei_prediction_maps_labels_to_existing_response_shape(monkeypatch):
    monkeypatch.setenv('AFRO_XLMR_API_URL', 'http://tei.internal')
    monkeypatch.setenv('AFRO_XLMR_API_KEY', 'test-api-key')
    calls = {}

    def fake_post(url, **kwargs):
        calls['url'] = url
        calls['kwargs'] = kwargs
        return FakeResponse([{'label': 'Stractural', 'score': 0.81234}])

    monkeypatch.setattr(tei_detector.requests, 'post', fake_post)

    result = tei_detector.predict('a test message')

    assert result == {
        'is_hate_speech': True,
        'confidence': 0.8123,
        'category': 'Structural',
        'severity': 'low',
        'group': 'Ideological Radicalization',
    }
    assert calls['url'] == 'http://tei.internal/predict'
    assert calls['kwargs']['headers']['Authorization'] == 'Bearer test-api-key'
    assert calls['kwargs']['json'] == {'inputs': 'a test message'}


def test_tei_configuration_requires_url_and_key_together(monkeypatch):
    monkeypatch.setenv('AFRO_XLMR_API_URL', 'http://tei.internal')
    monkeypatch.setenv('AFRO_XLMR_API_KEY', '')

    with pytest.raises(tei_detector.TeiConfigurationError, match='must be set together'):
        tei_detector.is_configured()


def test_tei_prediction_rejects_unsafe_url(monkeypatch):
    monkeypatch.setenv('AFRO_XLMR_API_URL', 'http://tei.internal/predict?token=secret')
    monkeypatch.setenv('AFRO_XLMR_API_KEY', 'test-api-key')

    with pytest.raises(tei_detector.TeiConfigurationError, match='query or fragment'):
        tei_detector.predict('test')
