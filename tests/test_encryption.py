"""Tests for the encryption module."""

from app.encryption import encrypt, decrypt


class TestEncryption:
    def test_round_trip(self):
        plaintext = "my-secret-api-key-12345"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext
        assert decrypt(ciphertext) == plaintext

    def test_empty_string(self):
        assert encrypt("") == ""
        assert decrypt("") == ""

    def test_different_inputs_different_outputs(self):
        a = encrypt("key_one")
        b = encrypt("key_two")
        assert a != b

    def test_unicode(self):
        plaintext = "passphrase-with-special-chars-@#$%^&*()"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_long_string(self):
        plaintext = "a" * 10000
        assert decrypt(encrypt(plaintext)) == plaintext
