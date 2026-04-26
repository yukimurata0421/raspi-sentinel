from __future__ import annotations

from raspi_sentinel.redaction import redact_text


def test_redact_text_masks_url_credentials_and_query_secret() -> None:
    text = "curl https://user:pass@example.test/hook?token=abcd1234&x=1"
    redacted = redact_text(text)
    assert "user:pass" not in redacted
    assert "token=abcd1234" not in redacted
    assert "https://***:***@" in redacted
    assert "token=***" in redacted


def test_redact_text_masks_auth_headers_and_bearer_tokens() -> None:
    text = "Authorization: Bearer abc.def.ghi X-API-Key: supersecret"
    redacted = redact_text(text)
    assert "abc.def.ghi" not in redacted
    assert "supersecret" not in redacted
    assert "Authorization: ***" in redacted
    assert "X-API-Key: ***" in redacted
