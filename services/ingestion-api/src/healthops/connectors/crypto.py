"""Encryption for stored provider refresh tokens (Fernet, symmetric).

The key comes from the server environment only. It is never logged, returned by any route or
sent to the phone; rotating it requires re-connecting providers.
"""

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored token cannot be decrypted with the current key.") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()
