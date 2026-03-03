import asyncio
import threading
from typing import Callable, Any

class RequestDeduplicator:
    """
    System to prevent redundant identical requests from executing simultaneously.
    If a request for the same key is in progress, subsequent calls wait for its result.
    """
    def __init__(self):
        self._active_requests = {}
        self._lock = threading.RLock()
    
    async def get_or_execute(self, key: str, coroutine_func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Executes the coroutine function with the provided key, or waits for an existing one.
        """
        with self._lock:
            if key in self._active_requests:
                # Wait for existing request to complete
                print(f"[DEDUP] Waiting for existing request: {key}")
                return await self._active_requests[key]
        
        # Create new request
        print(f"[DEDUP] Creating new request: {key}")
        future = asyncio.create_task(coroutine_func(*args, **kwargs))
        
        with self._lock:
            self._active_requests[key] = future
        
        try:
            result = await future
            return result
        finally:
            with self._lock:
                self._active_requests.pop(key, None)