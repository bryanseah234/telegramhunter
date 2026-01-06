"""
Metrics collection for monitoring task performance.
Tracks execution times, success/failure rates, and service health.
"""
import time
from typing import Dict, Optional
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import functools
from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MetricData:
    """Container for metric data"""
    count: int = 0
    total_time: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    last_execution: Optional[float] = None
    min_time: float = float('inf')
    max_time: float = 0.0
    
    def record_success(self, duration: float):
        """Record successful execution"""
        self.count += 1
        self.success_count += 1
        self.total_time += duration
        self.last_execution = time.time()
        self.min_time = min(self.min_time, duration)
        self.max_time = max(self.max_time, duration)
    
    def record_failure(self, duration: float):
        """Record failed execution"""
        self.count += 1
        self.failure_count += 1
        self.total_time += duration
        self.last_execution = time.time()
    
    @property
    def avg_time(self) -> float:
        """Calculate average execution time"""
        return self.total_time / self.count if self.count > 0 else 0.0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        return (self.success_count / self.count * 100) if self.count > 0 else 0.0


class MetricsCollector:
    """
    Centralized metrics collection.
    Thread-safe for basic operations.
    """
    
    def __init__(self):
        self._metrics: Dict[str, MetricData] = defaultdict(MetricData)
    
    def track(self, metric_name: str):
        """
        Decorator to track function execution metrics.
        
        Example:
            @metrics.track("scan_shodan")
            def scan_shodan():
                # ... code ...
        """
        def decorator(func):
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    duration = time.time() - start_time
                    self.record_success(metric_name, duration)
                    return result
                except Exception as e:
                    duration = time.time() - start_time
                    self.record_failure(metric_name, duration)
                    raise
            
            # Support async functions
            import asyncio
            if asyncio.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    start_time = time.time()
                    try:
                        result = await func(*args, **kwargs)
                        duration = time.time() - start_time
                        self.record_success(metric_name, duration)
                        return result
                    except Exception as e:
                        duration = time.time() - start_time
                        self.record_failure(metric_name, duration)
                        raise
                return async_wrapper
            
            return sync_wrapper
        return decorator
    
    def record_success(self, metric_name: str, duration: float):
        """Record successful operation"""
        self._metrics[metric_name].record_success(duration)
        logger.debug(f"Metric [{metric_name}] success in {duration:.2f}s")
    
    def record_failure(self, metric_name: str, duration: float):
        """Record failed operation"""
        self._metrics[metric_name].record_failure(duration)
        logger.warning(f"Metric [{metric_name}] failure after {duration:.2f}s")
    
    def get_metric(self, metric_name: str) -> Optional[MetricData]:
        """Get specific metric data"""
        return self._metrics.get(metric_name)
    
    def get_all_metrics(self) -> Dict[str, dict]:
        """Get all metrics as dict"""
        return {
            name: {
                "count": data.count,
                "success_count": data.success_count,
                "failure_count": data.failure_count,
                "success_rate": round(data.success_rate, 2),
                "avg_time": round(data.avg_time, 2),
                "min_time": round(data.min_time, 2) if data.min_time != float('inf') else None,
                "max_time": round(data.max_time, 2),
                "last_execution": datetime.fromtimestamp(data.last_execution).isoformat() if data.last_execution else None
            }
            for name, data in self._metrics.items()
        }
    
    def reset(self):
        """Reset all metrics"""
        self._metrics.clear()
        logger.info("All metrics reset")
    
    def get_summary(self) -> dict:
        """Get summary statistics"""
        total_executions = sum(m.count for m in self._metrics.values())
        total_successes = sum(m.success_count for m in self._metrics.values())
        total_failures = sum(m.failure_count for m in self._metrics.values())
        
        return {
            "total_executions": total_executions,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "overall_success_rate": round((total_successes / total_executions * 100) if total_executions > 0 else 0, 2),
            "tracked_functions": len(self._metrics)
        }


# Global metrics instance
metrics = MetricsCollector()
