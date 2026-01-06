"""
Validate configuration and dependencies at startup.
Run this before deploying to catch configuration issues early.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def validate_config():
    """Validate configuration settings"""
    print("1. Validating configuration...")
    try:
        from app.core.config import settings
        print(f"   ✅ Configuration loaded: {settings.PROJECT_NAME}")
        print(f"   ✅ Environment: {settings.ENV}")
        print(f"   ✅ Supabase URL: {settings.SUPABASE_URL[:30]}...")
        print(f"   ✅ Redis URL: {settings.REDIS_URL[:20]}...")
        return True
    except Exception as e:
        print(f"   ❌ Config validation failed: {e}")
        return False

def validate_database():
    """Validate database connection"""
    print("\n2. Validating database connection...")
    try:
        from app.core.database import db
        # Try a simple query
        result = db.table("discovered_credentials").select("id").limit(1).execute()
        print(f"   ✅ Database connected successfully")
        return True
    except Exception as e:
        print(f"   ❌ Database connection failed: {e}")
        return False

def validate_redis():
    """Validate Redis connection"""
    print("\n3. Validating Redis connection...")
    try:
        import redis
        from app.core.config import settings
        client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        client.ping()
        print(f"   ✅ Redis connected successfully")
        return True
    except Exception as e:
        print(f"   ❌ Redis connection failed: {e}")
        return False

def validate_telegram_api():
    """Validate Telegram Bot API"""
    print("\n4. Validating Telegram Bot API...")
    try:
        import requests
        from app.core.config import settings
        
        url = f"https://api.telegram.org/bot{settings.MONITOR_BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200 and response.json().get('ok'):
            bot_info = response.json()['result']
            print(f"   ✅ Bot API connected: @{bot_info.get('username')}")
            return True
        else:
            print(f"   ❌ Bot API failed: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Telegram API validation failed: {e}")
        return False

def validate_optional_services():
    """Check optional API keys"""
    print("\n5. Checking optional services...")
    from app.core.config import settings
    
    services = {
        "Shodan": settings.SHODAN_KEY,
        "URLScan": settings.URLSCAN_KEY,
        "GitHub": settings.GITHUB_TOKEN,
        "FOFA": settings.FOFA_KEY and settings.FOFA_EMAIL,
    }
    
    for name, has_key in services.items():
        status = "✅" if has_key else "⚠️ "
        print(f"   {status} {name}: {'Configured' if has_key else 'Not configured'}")
    
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Telegram Hunter - Startup Validation")
    print("=" * 60)
    
    results = [
        validate_config(),
        validate_database(),
        validate_redis(),
        validate_telegram_api(),
        validate_optional_services()
    ]
    
    print("\n" + "=" * 60)
    if all(results[:4]):  # Only require first 4 to pass
        print("✅ All critical validations passed!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ Some validations failed. Please fix configuration.")
        print("=" * 60)
        sys.exit(1)
