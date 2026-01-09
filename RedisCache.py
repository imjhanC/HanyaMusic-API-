
import redis
import json
import logging
from typing import Optional, Dict, Any, Union
from datetime import timedelta

class RedisCache:
    """
    A robust Redis cache implementation for the HanyaMusic API.
    Handles serialization, TTL, and connection errors gracefully.
    """
    
    def __init__(self, host='localhost', port=6379, db=0, prefix='hanya:'):
        self.prefix = prefix
        self.enabled = False
        try:
            self.client = redis.Redis(
                host=host, 
                port=port, 
                db=db, 
                decode_responses=True,
                socket_connect_timeout=2
            )
            # Test connection
            self.client.ping()
            self.enabled = True
            print(f"[REDIS] Connected successfully to {host}:{port}/db{db}")
        except redis.ConnectionError as e:
            print(f"[REDIS] Connection failed: {e}. Caching disabled.")
            self.enabled = False
        except Exception as e:
            print(f"[REDIS] Initialization error: {e}. Caching disabled.")
            self.enabled = False

    def _get_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if miss or error."""
        if not self.enabled:
            return None
            
        try:
            data = self.client.get(self._get_key(key))
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"[REDIS] Error getting key {key}: {e}")
            return None

    def set(self, key: str, value: Any, ttl_minutes: int = 60) -> bool:
        """Set a value in cache with TTL."""
        if not self.enabled:
            return False
            
        try:
            serialized = json.dumps(value)
            full_key = self._get_key(key)
            self.client.setex(
                full_key,
                timedelta(minutes=ttl_minutes),
                serialized
            )
            return True
        except TypeError as e:
            print(f"[REDIS] Serialization error for key {key}: {e}")
            return False
        except Exception as e:
            print(f"[REDIS] Error setting key {key}: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        if not self.enabled:
            return False
            
        try:
            self.client.delete(self._get_key(key))
            return True
        except Exception as e:
            print(f"[REDIS] Error deleting key {key}: {e}")
            return False

    def clear(self) -> bool:
        """Clear all keys with this prefix."""
        if not self.enabled:
            return False
            
        try:
            # Efficiently scan and delete only keys with our prefix
            cursor = '0'
            match_pattern = f"{self.prefix}*"
            while cursor != 0:
                cursor, keys = self.client.scan(cursor=cursor, match=match_pattern, count=100)
                if keys:
                    self.client.delete(*keys)
            print(f"[REDIS] Cleared all keys with prefix {self.prefix}")
            return True
        except Exception as e:
            print(f"[REDIS] Error clearing cache: {e}")
            return False
            
    def stats(self) -> Dict[str, Any]:
        """Get Redis stats."""
        if not self.enabled:
            return {"status": "disabled", "error": "Connection failed"}
            
        try:
            info = self.client.info()
            return {
                "status": "online",
                "used_memory_human": info.get('used_memory_human'),
                "connected_clients": info.get('connected_clients'),
                "uptime_days": info.get('uptime_in_days'),
                "total_keys": self.client.dbsize()
            }
        except Exception:
            return {"status": "error_fetching_stats"}
