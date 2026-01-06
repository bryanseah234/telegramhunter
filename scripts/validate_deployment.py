"""
Comprehensive validation script for deployment readiness.
Checks imports, syntax, and core functionality.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_core_imports():
    """Test all core module imports"""
    print("1. Testing core imports...")
    try:
        from app.core.config import settings
        from app.core.database import db
        from app.core.logger import get_logger
        from app.core.retry import retry
        from app.core.db_retry import with_db_retry, DatabaseHealth
        from app.core.circuit_breaker import get_circuit_breaker, CircuitBreaker
        from app.core.metrics import metrics
        from app.core.audit import AuditLogger
        print("   ✅ All core imports successful")
        return True
    except Exception as e:
        print(f"   ❌ Core import failed: {e}")
        return False

def test_service_imports():
    """Test service imports"""
    print("\n2. Testing service imports...")
    try:
        from app.services.broadcaster_srv import BroadcasterService
        from app.services.scanners import ShodanService, UrlScanService, GithubService, FofaService
        from app.services.scraper_srv import ScraperService
        print("   ✅ All service imports successful")
        return True
    except Exception as e:
        print(f"   ❌ Service import failed: {e}")
        return False

def test_task_imports():
    """Test Celery task imports"""
    print("\n3. Testing task imports...")
    try:
        from app.workers.celery_app import app as celery_app
        from app.workers.tasks.scanner_tasks import scan_shodan, scan_urlscan, scan_github
        from app.workers.tasks.flow_tasks import system_heartbeat, exfiltrate_chat
        print("   ✅ All task imports successful")
        return True
    except Exception as e:
        print(f"   ❌ Task import failed: {e}")
        return False

def test_api_imports():
    """Test API imports"""
    print("\n4. Testing API imports...")
    try:
        from app.api.main import app
        from app.api.routers import monitor, scan, health
        print("   ✅ All API imports successful")
        return True
    except Exception as e:
        print(f"   ❌ API import failed: {e}")
        return False

def test_helper_imports():
    """Test helper utilities"""
    print("\n5. Testing helper utilities...")
    try:
        from app.utils.helpers import is_valid_telegram_token, extract_chat_id
        print("   ✅ Helper utilities imported")
        return True
    except Exception as e:
        print(f"   ❌ Helper import failed: {e}")
        return False

def test_config_validation():
    """Test configuration"""
    print("\n6. Testing configuration...")
    try:
        from app.core.config import settings
        assert settings.PROJECT_NAME is not None
        assert settings.SUPABASE_URL is not None
        assert settings.REDIS_URL is not None
        assert len(settings.TARGET_COUNTRIES) > 0
        print(f"   ✅ Config valid ({len(settings.TARGET_COUNTRIES)} countries configured)")
        return True
    except Exception as e:
        print(f"   ❌ Config validation failed: {e}")
        return False

def test_new_features():
    """Test new Phase 1-4 features"""
    print("\n7. Testing new features...")
    try:
        from app.core.logger import get_logger
        logger = get_logger("test")
        
        from app.core.retry import retry
        @retry(max_attempts=1)
        def test_func():
            return True
        assert test_func() == True
        
        from app.core.circuit_breaker import get_circuit_breaker
        breaker = get_circuit_breaker("test")
        assert breaker is not None
        
        from app.core.metrics import metrics
        assert metrics is not None
        
        print("   ✅ All new features working")
        return True
    except Exception as e:
        print(f"   ❌ Feature test failed: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Telegram Hunter - Deployment Validation")
    print("=" * 60)
    
    results = [
        test_core_imports(),
        test_service_imports(),
        test_task_imports(),
        test_api_imports(),
        test_helper_imports(),
        test_config_validation(),
        test_new_features()
    ]
    
    print("\n" + "=" * 60)
    if all(results):
        print("✅ All validation checks passed!")
        print("✅ Ready for deployment")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ Some checks failed")
        print("=" * 60)
        sys.exit(1)
