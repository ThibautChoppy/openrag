def with_retry(max_attempts=3, base_wait=1.0):
    """No-op stub — real tenacity implementation replaces this later."""

    def decorator(fn):
        return fn

    return decorator
