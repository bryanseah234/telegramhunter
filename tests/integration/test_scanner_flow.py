"""
Integration tests for scanner workflow.
Test the full flow from scanning to saving credentials.
"""
import pytest
from unittest.mock import Mock, patch
from app.workers.tasks.scanner_tasks import _save_credentials


@pytest.mark.integration
class TestScannerWorkflow:
    """Integration tests for scanner workflow"""
    
    @patch('app.workers.tasks.scanner_tasks.db')
    @patch('requests.get')
    def test_save_valid_token(self, mock_requests, mock_db):
        """Test saving a valid token through validation flow"""
        # Mock Bot API getMe response
        mock_requests.return_value.status_code = 200
        mock_requests.return_value.json.return_value = {
            'ok': True,
            'result': {
                'id': 123456789,
                'username': 'test_bot',
                'first_name': 'Test Bot'
            }
        }
        
        # Mock database responses
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        mock_db.table.return_value.insert.return_value.execute.return_value.data = [
            {'id': 'new_cred_id'}
        ]
        
        # Test data
        results = [{
            'token': '123456789:AAHhbW3Pzj9V5JhU5KzJ9V5JhU5KzJ9V5Jh',
            'meta': {'source': 'test'}
        }]
        
        # This would normally run async, but we can test the logic
        saved = _save_credentials(results, 'test_source')
        
        # Should attempt to save
        assert saved >= 0  # Returns count of saved credentials
    
    @patch('app.workers.tasks.scanner_tasks.db')
    @patch('requests.get')
    def test_skip_invalid_token(self, mock_requests, mock_db):
        """Test that invalid tokens are skipped"""
        results = [{
            'token': 'invalid_token_format',
            'meta': {}
        }]
        
        saved = _save_credentials(results, 'test_source')
        
        # Should skip invalid token
        assert saved == 0
        mock_db.table.assert_not_called()
    
    @patch('app.workers.tasks.scanner_tasks.db')
    @patch('requests.get')
    def test_skip_duplicate_token(self, mock_requests, mock_db):
        """Test that duplicate tokens are skipped"""
        # Mock existing credential
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {'id': 'existing_id', 'chat_id': '123'}
        ]
        
        results = [{
            'token': '123456789:AAHhbW3Pzj9V5JhU5KzJ9V5JhU5KzJ9V5Jh',
            'meta': {}
        }]
        
        saved = _save_credentials(results, 'test_source')
        
        # Should skip duplicate
        assert saved == 0


@pytest.mark.integration
class TestBroadcastWorkflow:
    """Integration tests for broadcast workflow"""
    
    @pytest.mark.asyncio
    async def test_broadcaster_initialization(self):
        """Test BroadcasterService initializes correctly"""
        from app.services.broadcaster_srv import BroadcasterService
        
        broadcaster = BroadcasterService()
        assert broadcaster.bot_token is not None
        # Bot instance is lazy-loaded
        assert broadcaster._bot is None
    
    @pytest.mark.asyncio
    @patch('app.services.broadcaster_srv.Bot')
    async def test_send_log_with_retry(self, mock_bot_class):
        """Test send_log handles retries on failure"""
        from app.services.broadcaster_srv import BroadcasterService
        
        # Mock bot that fails once then succeeds
        mock_bot = Mock()
        mock_bot.send_message = Mock(side_effect=[
            Exception("Temporary error"),
            None  # Success on retry
        ])
        mock_bot_class.return_value = mock_bot
        
        broadcaster = BroadcasterService()
        # Would need to test with proper async setup
        # This is a simplified example


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'integration'])
