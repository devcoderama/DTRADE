"""Uji keandalan provider AI: klasifikasi error & retry post_with_retry."""
import requests

from core.ai_client import is_retryable_status, post_with_retry


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = ""


class _Session:
    """Session palsu: memutar ulang urutan hasil yang disiapkan."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retryable_classification():
    assert is_retryable_status(429) and is_retryable_status(500) and is_retryable_status(503)
    assert not is_retryable_status(400) and not is_retryable_status(401) and not is_retryable_status(200)


def test_retries_on_500_then_succeeds():
    s = _Session([_Resp(500), _Resp(200)])
    resp = post_with_retry(s, "http://x/chat", provider="uji", backoff=(0.01, 0.01))
    assert resp.status_code == 200 and s.calls == 2


def test_no_retry_on_400():
    s = _Session([_Resp(400)])
    resp = post_with_retry(s, "http://x/chat", provider="uji", backoff=(0.01, 0.01))
    assert resp.status_code == 400 and s.calls == 1


def test_retries_on_network_error_then_succeeds():
    s = _Session([requests.ConnectionError("putus"), _Resp(200)])
    resp = post_with_retry(s, "http://x/chat", provider="uji", backoff=(0.01, 0.01))
    assert resp.status_code == 200 and s.calls == 2


def test_network_error_exhausts_and_raises():
    s = _Session([requests.Timeout("t"), requests.Timeout("t"), requests.Timeout("t")])
    try:
        post_with_retry(s, "http://x/chat", provider="uji", backoff=(0.01, 0.01))
        raise AssertionError("seharusnya melempar Timeout")
    except requests.Timeout:
        pass
    assert s.calls == 3  # 1 awal + 2 retry, lalu menyerah dengan jujur
