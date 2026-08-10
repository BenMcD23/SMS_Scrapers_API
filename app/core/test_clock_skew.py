"""A token minted seconds ago must verify even if our clock trails Google's.

google-auth defaults `clock_skew_in_seconds` to 0, so a token whose `iat` is one
second ahead of this host is rejected as "Token used too early" — a 401 on the
very token the user just signed in with. On the frontend that showed up as being
sent back to Google a second time, then a "Session expired" badge, then
everything working after a manual refresh a few seconds later.
"""

import time

import pytest
from google.auth import crypt, jwt as google_jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core.security import CLOCK_SKEW_S

# Real drift between two NTP-synced hosts is sub-second; this is the margin we
# promise to tolerate on top of that.
TOLERATED_DRIFT_S = 5


@pytest.fixture(scope="module")
def signing():
    """A throwaway RSA key plus the cert map that verifies tokens it signs."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certs = {
        "kid1": key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    }
    return crypt.RSASigner.from_string(pem, "kid1"), certs


def decode(signing, issued_at_offset: int, skew: int):
    """Verify a token issued `issued_at_offset` seconds from now."""
    signer, certs = signing
    now = int(time.time())
    token = google_jwt.encode(signer, {
        "iss": "https://accounts.google.com",
        "aud": "test-audience",
        "iat": now + issued_at_offset,
        "exp": now + 3600,
    })
    return google_jwt.decode(
        token, certs=certs, audience="test-audience", clock_skew_in_seconds=skew
    )


def test_our_skew_accepts_a_token_from_a_slightly_faster_clock(signing):
    assert decode(signing, TOLERATED_DRIFT_S, CLOCK_SKEW_S)["aud"] == "test-audience"


def test_zero_skew_is_what_broke_it(signing):
    # Pins the reason CLOCK_SKEW_S exists: drop it back to google-auth's default
    # and the same token is refused.
    with pytest.raises(ValueError, match="Token used too early"):
        decode(signing, TOLERATED_DRIFT_S, 0)


def test_skew_is_generous_enough_to_matter():
    assert CLOCK_SKEW_S >= TOLERATED_DRIFT_S


def test_skew_does_not_outlive_the_token():
    # Tolerating drift must not turn into accepting long-dead tokens: Google's
    # id_tokens live an hour, so the margin has to stay far below that.
    assert CLOCK_SKEW_S <= 300
