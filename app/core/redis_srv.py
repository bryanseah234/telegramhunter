import redis
from app.core.config import settings

class RedisService:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = redis.from_url(
                settings.REDIS_URL, 
                decode_responses=True # Ensure we get strings back
            )
        return self._client

    def set_cooldown(self, key: str, seconds: int):
        """Sets a cooldown in Redis that expires automatically."""
        if seconds <= 0:
            return
        self.client.set(f"cooldown:{key}", "active", ex=seconds)

    def is_on_cooldown(self, key: str) -> bool:
        """Checks if a key is currently on cooldown."""
        return self.client.exists(f"cooldown:{key}") > 0

    def get_cooldown_remaining(self, key: str) -> int:
        """Returns remaining seconds for a cooldown, or 0."""
        ttl = self.client.ttl(f"cooldown:{key}")
        return max(0, ttl)

    def get_next_rotation_index(self, key: str, max_val: int) -> int:
        """Atomically increments and returns the next index modulo max_val."""
        if max_val <= 0: return 0
        idx = self.client.incr(f"rotation_index:{key}")
        return idx % max_val

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            ttl_seconds = 60
        return bool(self.client.set(f"lock:{key}", "1", nx=True, ex=ttl_seconds))

    def release_lock(self, key: str):
        self.client.delete(f"lock:{key}")

    def incr_key(self, key: str, ttl_seconds: int | None = None) -> int:
        new_val = self.client.incr(f"counter:{key}")
        if ttl_seconds:
            self.client.expire(f"counter:{key}", ttl_seconds)
        return int(new_val)

    def reset_key(self, key: str):
        self.client.delete(f"counter:{key}")

redis_srv = RedisService()
