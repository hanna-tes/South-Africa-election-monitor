from election_monitor import health


def test_tei_health_check_requires_all_three_examples(monkeypatch):
    calls = []

    def fake_predict(text, timeout_seconds):
        calls.append((text, timeout_seconds))
        return {'is_hate_speech': text == health.TEI_HEALTH_CASES[0][1]}

    monkeypatch.setattr(health, 'predict', fake_predict)

    assert health.tei_health_check() is True
    assert len(calls) == 3
    assert all(timeout == 5.0 for _, timeout in calls)


def test_tei_health_check_fails_when_any_example_is_wrong(monkeypatch):
    calls = []

    def fake_predict(text, timeout_seconds):
        calls.append(text)
        return {'is_hate_speech': True}

    monkeypatch.setattr(health, 'predict', fake_predict)

    assert health.tei_health_check() is False
    assert len(calls) == 3


def test_tei_health_check_fails_when_tei_raises(monkeypatch):
    def fake_predict(_text, timeout_seconds):
        raise RuntimeError('sidecar unavailable')

    monkeypatch.setattr(health, 'predict', fake_predict)

    assert health.tei_health_check() is False
