from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID

class CredentialBase(BaseModel):
    source: str
    meta: Dict[str, Any] = {}

class CredentialCreate(CredentialBase):
    token: str

class CredentialOut(CredentialBase):
    id: UUID
    chat_id: Optional[int] = None
    status: str
    created_at: datetime
    updated_at: datetime
    
    # Exclude bot_token from default output for security
    model_config = ConfigDict(from_attributes=True)

class MessageOut(BaseModel):
    id: UUID
    credential_id: UUID
    telegram_msg_id: int
    sender_name: Optional[str] = None
    content: Optional[str] = None
    media_type: str
    is_broadcasted: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ScanRequest(BaseModel):
    source: str = "shodan"
    query: str
    
class StatsOut(BaseModel):
    credentials_total: int
    credentials_active: int
    messages_exfiltrated: int
    messages_broadcasted: int
