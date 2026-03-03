import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

class AdvancedCache:
    """
    In-memory caching system with TTL and LRU eviction policy.
    """
    def __init__(self, max_size: int = 1000, ttl_minutes: int = 30):
        self._cache = {}
        self._access_times = {}
        self._max_size = max_size
        self._ttl = timedelta(minutes=ttl_minutes)
        self._lock = threading.RLock()
        self._hits = 0
        self._requests = 0
        
    def get(self, key: str) -> Optional[Any]:
        """Get an item from the cache."""
        with self._lock:
            self._requests += 1
            if key in self._cache:
                # Check if expired
                if datetime.now() - self._access_times[key] > self._ttl:
                    del self._cache[key]
                    del self._access_times[key]
                    return None
                
                # Update access time for LRU
                self._access_times[key] = datetime.now()
                self._hits += 1
                
                # Handle dictionary copying if the value is a dict
                value = self._cache[key]
                if isinstance(value, dict):
                    return value.copy()
                return value
            return None
    
    def set(self, key: str, value: Any):
        """Set an item in the cache."""
        with self._lock:
            # Clean up expired entries
            self._cleanup_expired()
            
            # If cache is full, remove oldest entries
            if len(self._cache) >= self._max_size:
                self._evict_lru()
            
            # Handle dictionary copying
            if isinstance(value, dict):
                self._cache[key] = value.copy()
            else:
                self._cache[key] = value
                
            self._access_times[key] = datetime.now()
    
    def _cleanup_expired(self):
        """Internal method to remove expired entries."""
        current_time = datetime.now()
        expired_keys = [
            key for key, access_time in self._access_times.items()
            if current_time - access_time > self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]
            del self._access_times[key]
    
    def _evict_lru(self):
        """Internal method to evict the least recently used entries (20% of total)."""
        # Remove 20% of oldest entries
        items_to_remove = max(1, len(self._cache) // 5)
        sorted_items = sorted(self._access_times.items(), key=lambda x: x[1])
        for key, _ in sorted_items[:items_to_remove]:
            del self._cache[key]
            del self._access_times[key]
    
    def clear(self):
        """Clear all entries from the cache."""
        with self._lock:
            self._cache.clear()
            self._access_times.clear()
            self._hits = 0
            self._requests = 0
    
    def stats(self) -> Dict[str, Any]:
        """Returns cache usage statistics."""
        with self._lock:
            hit_ratio = self._hits / max(self._requests, 1)
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hit_ratio": round(hit_ratio, 2),
                "hits": self._hits,
                "requests": self._requests
            }