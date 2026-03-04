import os
from cryptography.fernet import Fernet

# Pull the key from environment
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
    raise ValueError("CRITICAL: ENCRYPTION_KEY not found in .env file!")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

def encrypt_password(password: str) -> str:
    if not password: 
        return None
    return cipher_suite.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str) -> str:
    if not encrypted_password: 
        return None
    try:
        return cipher_suite.decrypt(encrypted_password.encode()).decode()
    except Exception as e:
        print(f"Decryption failed: {e}. Check if ENCRYPTION_KEY has changed.")
        return None