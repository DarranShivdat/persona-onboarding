"""Refresh-token encryption at rest (AES-GCM, key from env). No DB, no network."""
import pytest

pytest.importorskip("cryptography")

from agent.gmail.crypto import ENV_KEY, TokenCipher, TokenKeyError, generate_key  # noqa: E402

SID = "5f1a0b7e-0000-4000-8000-000000000001"
TOKEN = "1//0g-FIXTURE-REFRESH-TOKEN"


def test_roundtrip_and_ciphertext_hides_plaintext():
    c = TokenCipher(generate_key())
    blob = c.encrypt(TOKEN, aad=SID)
    assert TOKEN.encode() not in blob and blob.startswith(b"v1")
    assert c.decrypt(blob, aad=SID) == TOKEN
    assert c.encrypt(TOKEN, aad=SID) != blob  # fresh nonce each time


def test_ciphertext_is_bound_to_session_and_key():
    c = TokenCipher(generate_key())
    blob = c.encrypt(TOKEN, aad=SID)
    with pytest.raises(TokenKeyError):
        c.decrypt(blob, aad="another-session")
    with pytest.raises(TokenKeyError):
        TokenCipher(generate_key()).decrypt(blob, aad=SID)
    with pytest.raises(TokenKeyError):
        c.decrypt(b"v1" + b"\x00" * 30, aad=SID)


@pytest.mark.parametrize("bad", ["not base64!!", "c2hvcnQ="])
def test_bad_keys_rejected_without_echoing_key(bad):
    with pytest.raises(TokenKeyError) as e:
        TokenCipher(bad)
    assert bad not in str(e.value)


def test_from_env_and_repr(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert TokenCipher.from_env() is None
    key = generate_key()
    monkeypatch.setenv(ENV_KEY, key)
    c = TokenCipher.from_env()
    assert c is not None and key not in repr(c) and c.key_id in repr(c)
