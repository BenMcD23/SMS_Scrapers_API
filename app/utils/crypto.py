import os
from cryptography.fernet import Fernet

# In your .env file: ENCRYPTION_KEY=some_generated_key
# To generate a key: Fernet.generate_key().decode()
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
cipher_suite = Fernet(ENCRYPTION_KEY.encode())

def encrypt_password(password: str) -> str:
    if not password: return None
    return cipher_suite.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str) -> str:
    if not encrypted_password: return None
    return cipher_suite.decrypt(encrypted_password.encode()).decode()