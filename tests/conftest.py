import pytest
import os
import sys
from pathlib import Path

# Add project root to sys.path so we can import 'app'
sys.path.append(str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

# Mock Environment Variables BEFORE importing app
os.environ["PROJECT_NAME"] = "Test Hunter"
os.environ["ENV"] = "test"
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "mock-key"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["ENCRYPTION_KEY"] = "A" * 32 + "=" # Invalid but length correct-ish, need valid fernet key
# Generate a valid key for testing
from cryptography.fernet import Fernet
valid_key = Fernet.generate_key().decode()
os.environ["ENCRYPTION_KEY"] = valid_key

os.environ["MONITOR_BOT_TOKEN"] = "123:ABC,456:DEF,789:GHI"
os.environ["MONITOR_GROUP_ID"] = "-100123"
os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "abc"

from app.api.main import app

@pytest.fixture(scope="module")
def client():
    # Use TestClient for API tests
    return TestClient(app)
