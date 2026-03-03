import threading
import concurrent.futures
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List

class LoadBalancer:
    """
    Enhanced rate limiting and load balancing system.
    Maintains request counts per executor and provides the least loaded one.
    """
    def __init__(self):
        self._request_counts = defaultdict(int)
        self._last_reset = datetime.now()
        self._lock = threading.RLock()
    
    def get_least_loaded_executor(self, executors: List[concurrent.futures.ThreadPoolExecutor]) -> concurrent.futures.ThreadPoolExecutor:
        """
        Finds and returns the executor with the least number of active threads.
        Resets internal counters every minute.
        """
        with self._lock:
            # Reset counters every minute
            if datetime.now() - self._last_reset > timedelta(minutes=1):
                self._request_counts.clear()
                self._last_reset = datetime.now()
            
            # Find executor with least active requests
            best_executor = min(executors, key=lambda e: len(e._threads) if e._threads else 0)
            executor_id = id(best_executor)
            self._request_counts[executor_id] += 1
            
            return best_executor