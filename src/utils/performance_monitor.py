import time
import logging
from functools import wraps
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class PerformanceMonitor:
    """Performance monitoring class"""
    
    def __init__(self):
        self.latency_records: Dict[str, List[float]] = {}
        self.start_times: Dict[str, float] = {}
    
    def start_timer(self, operation: str) -> None:
        """Start a timer"""
        self.start_times[operation] = time.time()
    
    def end_timer(self, operation: str) -> Optional[float]:
        """Stop the timer and record latency"""
        if operation not in self.start_times:
            return None
        
        elapsed = time.time() - self.start_times[operation]
        
        if operation not in self.latency_records:
            self.latency_records[operation] = []
        
        self.latency_records[operation].append(elapsed)
        
        # Keep only the most recent 100 entries
        if len(self.latency_records[operation]) > 100:
            self.latency_records[operation].pop(0)
        
        del self.start_times[operation]
        return elapsed
    
    def get_average_latency(self, operation: str) -> Optional[float]:
        """Compute average latency"""
        if operation not in self.latency_records or not self.latency_records[operation]:
            return None
        
        return sum(self.latency_records[operation]) / len(self.latency_records[operation])
    
    def get_metrics(self) -> Dict[str, Dict[str, float]]:
        """Return all metrics"""
        metrics = {}
        for operation, latencies in self.latency_records.items():
            if latencies:
                metrics[operation] = {
                    'average': sum(latencies) / len(latencies),
                    'min': min(latencies),
                    'max': max(latencies),
                    'count': len(latencies)
                }
        return metrics
    
    def log_metrics(self) -> None:
        """Log metrics"""
        metrics = self.get_metrics()
        for operation, stats in metrics.items():
            logger.info(
                f"Performance metrics for {operation}: "
                f"avg={stats['average']:.3f}s, "
                f"min={stats['min']:.3f}s, "
                f"max={stats['max']:.3f}s, "
                f"count={stats['count']}"
            )

# Global instance
performance_monitor = PerformanceMonitor()

def measure_performance(operation_name: str):
    """Performance measurement decorator"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            performance_monitor.start_timer(operation_name)
            try:
                result = await func(*args, **kwargs)
                elapsed = performance_monitor.end_timer(operation_name)
                if elapsed:
                    logger.debug(f"{operation_name} took {elapsed:.3f}s")
                return result
            except Exception as e:
                performance_monitor.end_timer(operation_name)
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            performance_monitor.start_timer(operation_name)
            try:
                result = func(*args, **kwargs)
                elapsed = performance_monitor.end_timer(operation_name)
                if elapsed:
                    logger.debug(f"{operation_name} took {elapsed:.3f}s")
                return result
            except Exception as e:
                performance_monitor.end_timer(operation_name)
                raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

import asyncio