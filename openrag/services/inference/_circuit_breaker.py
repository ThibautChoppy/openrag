def with_circuit_breaker(name: str, fail_max: int = 5, timeout_duration: float = 30.0):
    """No-op stub — real aiobreaker implementation replaces this later."""

    def decorator(fn):
        return fn

    return decorator
