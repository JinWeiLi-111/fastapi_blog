from cryptography.fernet import Fernet

from fastapi_blog.config import settings

_fernet = Fernet(settings.ai_encryption_key.get_secret_value())  # pyright: ignore[reportOptionalMemberAccess]


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
