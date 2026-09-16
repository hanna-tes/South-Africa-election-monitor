"""Adapter for the shared Text Embeddings Inference classifier."""

import os
from urllib.parse import urlparse

import requests


class TeiConfigurationError(ValueError):
    """Raised when only part of the TEI connection settings is supplied."""


def is_configured() -> bool:
    url = _setting('AFRO_XLMR_API_URL', '').strip()
    key = _setting('AFRO_XLMR_API_KEY', '').strip()
    if bool(url) != bool(key):
        raise TeiConfigurationError('AFRO_XLMR_API_URL and AFRO_XLMR_API_KEY must be set together')
    return bool(url)


def predict(text: str, timeout_seconds=None) -> dict:
    if not is_configured():
        raise TeiConfigurationError('TEI is not configured')

    response = requests.post(
        _predict_url(_setting('AFRO_XLMR_API_URL', '')),
        headers={
            'Authorization': f"Bearer {_setting('AFRO_XLMR_API_KEY', '').strip()}",
            'Content-Type': 'application/json',
        },
        json={'inputs': text},
        timeout=float(timeout_seconds if timeout_seconds is not None else _setting('AFRO_XLMR_API_TIMEOUT_SECONDS', 30.0)),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError('TEI returned an unexpected prediction response')

    top = payload[0]
    label = str(top.get('label', '')).strip()
    if not label:
        raise ValueError('TEI returned a prediction without a label')
    category = {'Stractural': 'Structural'}.get(label, label)
    confidence = float(top.get('score', 0.0))
    neutral = category in {'Neutral', 'Normal', 'normal'}

    severity_map = {
        'Violence': 'critical',
        'Inciteful': 'critical',
        'Call for action': 'critical',
        'Dehumanization': 'critical',
        'Extremism': 'high',
        'Ethnic slur': 'high',
        'Slur': 'high',
        'Misognistic': 'high',
        'Deragatory': 'medium',
        'Inflammatory': 'high',
        'Gender disinformation': 'high',
        'Stereotype': 'high',
        'Homophobic': 'high',
        'Ethnicity': 'high',
        'Xenophobia': 'high',
        'Religion': 'high',
        'Ancestry': 'medium',
        'Class': 'low',
        'Structural': 'low',
        'Neutral': 'low',
        'Normal': 'low',
    }
    group_map = {
        'Ancestry': 'Targeted Identity Vectors',
        'Ethnicity': 'Targeted Identity Vectors',
        'Ethnic slur': 'Targeted Identity Vectors',
        'Gender disinformation': 'Targeted Identity Vectors',
        'Homophobic': 'Targeted Identity Vectors',
        'Misognistic': 'Targeted Identity Vectors',
        'Religion': 'Targeted Identity Vectors',
        'Xenophobia': 'Targeted Identity Vectors',
        'Dehumanization': 'Expressive Hostility',
        'Deragatory': 'Expressive Hostility',
        'Slur': 'Expressive Hostility',
        'Stereotype': 'Expressive Hostility',
        'Call for action': 'Incitement & Mobilization',
        'Inciteful': 'Incitement & Mobilization',
        'Violence': 'Incitement & Mobilization',
        'Class': 'Ideological Radicalization',
        'Extremism': 'Ideological Radicalization',
        'Inflammatory': 'Ideological Radicalization',
        'Structural': 'Ideological Radicalization',
        'Neutral': 'Neutral',
    }
    return {
        'is_hate_speech': not neutral,
        'confidence': round(confidence, 4),
        'category': category,
        'severity': severity_map.get(category, 'medium' if not neutral else 'low'),
        'group': group_map.get(category, 'Unknown' if not neutral else 'Neutral'),
    }


def _predict_url(value: str) -> str:
    raw = value.strip().rstrip('/')
    parsed = urlparse(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
        raise TeiConfigurationError('AFRO_XLMR_API_URL must be an http(s) URL without credentials')
    if parsed.query or parsed.fragment:
        raise TeiConfigurationError('AFRO_XLMR_API_URL must not contain a query or fragment')
    return raw if parsed.path.rstrip('/') == '/predict' else f'{raw}/predict'


def _setting(name: str, default):
    environment_value = os.getenv(name)
    if environment_value is not None:
        return environment_value
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return os.getenv(name, default)
