import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.workers.tasks.scanner_tasks import _scan_shodan_async, _scan_urlscan_async, _scan_github_async, _scan_fofa_async
# Mocking services for safe testing without API keys
from unittest.mock import AsyncMock, patch

async def test_scans():
    print("🧪 Testing Async Scanners (Mocked)...")
    
    with patch("app.workers.tasks.scanner_tasks.shodan.search", new_callable=AsyncMock) as mock_shodan:
        mock_shodan.return_value = [{"token": "123456789:AAtesttoken123", "ip_str": "127.0.0.1"}]
        print("  - Running Shodan Scan...")
        res = await _scan_shodan_async(query="test")
        print(f"  ✅ Shodan Result: {res}")

    with patch("app.workers.tasks.scanner_tasks.urlscan.search", new_callable=AsyncMock) as mock_urlscan:
        mock_urlscan.return_value = [{"token": "123456789:AAtesttoken456", "page": {"url": "http://test.com"}}]
        print("  - Running URLScan...")
        res = await _scan_urlscan_async(query="test")
        print(f"  ✅ URLScan Result: {res}")
        
    print("🎉 All Async Task Wrappers Executed Successfully.")

if __name__ == "__main__":
    try:
        asyncio.run(test_scans())
    except Exception as e:
        print(f"❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()
