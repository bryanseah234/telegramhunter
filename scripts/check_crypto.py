from app.core.config import settings
from app.core.security import security
from cryptography.fernet import Fernet

def check_crypto():
    print("🔐 Checking Encryption Configuration...")
    
    key = settings.ENCRYPTION_KEY
    print(f"  - Key Loaded: {'Yes' if key else 'No'}")
    if key:
        print(f"  - Key Length: {len(key)}")
        print(f"  - Key Preview: {key[:5]}...{key[-5:]}")
    else:
        print("  ❌ NO KEY FOUND! Decryption checks will fail.")
        return

    # 1. Test Self-Correction (Encrypt/Decrypt)
    try:
        test_str = "Hello World"
        encrypted = security.encrypt(test_str)
        decrypted = security.decrypt(encrypted)
        
        if decrypted == test_str:
            print("  ✅ Local Encrypt/Decrypt Check: PASSED")
        else:
            print("  ❌ Local Encrypt/Decrypt Check: FAILED (Value mismatch)")
    except Exception as e:
        print(f"  ❌ Local Encrypt/Decrypt Check: EXCEPTION: {e}")

    # 2. Explain the Error
    print("\nℹ️  DIAGNOSIS:")
    print("If the above check passed, your `ENCRYPTION_KEY` is valid for *new* data.")
    print("The errors in your logs (`Decryption failed:`) indicate that the data in your Database")
    print("was encrypted with a DIFFERENT key than the one currently in your `.env` file.")
    print("Frequency: If you see this for ALL credentials, the key has changed.")
    print("If only for some, you might have mixed keys (e.g. from an old deployment).")

if __name__ == "__main__":
    try:
        check_crypto()
    except Exception as e:
        print(f"❌ Script failed: {e}")
