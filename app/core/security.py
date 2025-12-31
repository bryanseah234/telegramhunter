from cryptography.fernet import Fernet
from app.core.config import settings

class SecurityService:
    def __init__(self, key: str):
        if not key:
            raise ValueError("ENCRYPTION_KEY is not set.")
        self.fernet = Fernet(key.encode())

    def encrypt(self, data: str) -> str:
        """Encrypts a string and returns the encrypted token as a string."""
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypts a token string and returns the original string."""
        return self.fernet.decrypt(token.encode()).decode()

# Global instance
security = SecurityService(settings.ENCRYPTION_KEY)
