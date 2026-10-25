"""Print a new VAPID key pair for Web Push.

    python app/scripts/gen_vapid_keys.py

Put both lines in the API's environment. Changing the pair later signs every
device out of notifications (their subscriptions are tied to the old public
key), so generate it once per environment and keep it with the other secrets.
"""

from base64 import urlsafe_b64encode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate() -> tuple[str, str]:
    """(public, private): the uncompressed P-256 point browsers take as their
    applicationServerKey, and the raw 32-byte private scalar pywebpush reads."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url(public), b64url(private)


if __name__ == "__main__":
    public, private = generate()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
