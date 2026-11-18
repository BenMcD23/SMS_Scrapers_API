"""Stored Bader passwords: encrypted at rest, and a bad key never crashes a read."""

import importlib

import pytest
from cryptography.fernet import Fernet

from core import crypto


def test_a_password_round_trips_and_is_not_stored_in_the_clear():
    token = crypto.encrypt_password("Pässwörd 317!")

    assert "Pässwörd" not in token
    assert crypto.decrypt_password(token) == "Pässwörd 317!"


def test_encrypting_twice_gives_different_tokens():
    # Fernet adds a random IV, so equal passwords don't show as equal rows.
    assert crypto.encrypt_password("same") != crypto.encrypt_password("same")


@pytest.mark.parametrize("empty", [None, ""])
def test_no_password_is_stored_as_none(empty):
    assert crypto.encrypt_password(empty) is None
    assert crypto.decrypt_password(empty) is None


def test_a_token_from_another_key_reads_as_none_not_an_error(caplog):
    # After the key is rotated, old rows must read as "no password" (the user
    # re-enters it) rather than 500 every page that touches them.
    foreign = Fernet(Fernet.generate_key()).encrypt(b"secret").decode()

    assert crypto.decrypt_password(foreign) is None
    assert "ENCRYPTION_KEY" in caplog.text
    assert "secret" not in caplog.text


def test_garbage_reads_as_none():
    assert crypto.decrypt_password("not-a-fernet-token") is None


def test_the_api_refuses_to_start_without_a_key(monkeypatch):
    # Starting without one would store passwords nobody can ever decrypt.
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    try:
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            importlib.reload(crypto)
    finally:
        monkeypatch.undo()
        importlib.reload(crypto)
