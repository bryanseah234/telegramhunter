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

    def acquire_lock(self, key: str, ttl_seconds: int, owner: str = "1") -> bool:
        if ttl_seconds <= 0:
            ttl_seconds = 60
        return bool(self.client.set(f"lock:{key}", owner, nx=True, ex=ttl_seconds))

    def release_lock(self, key: str, owner: str = "1"):
        """Release lock only if we still own it (fencing via Lua CAS)."""
        lua = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
        self.client.eval(lua, 1, f"lock:{key}", owner)

    def incr_key(self, key: str, ttl_seconds: int | None = None) -> int:
        pipe = self.client.pipeline()
        pipe.incr(f"counter:{key}")
        if ttl_seconds:
            pipe.expire(f"counter:{key}", ttl_seconds)
        results = pipe.execute()
        return int(results[0])

    def reset_key(self, key: str):
        self.client.delete(f"counter:{key}")

redis_srv = RedisService()
