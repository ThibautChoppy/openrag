"""Re-export from canonical location for backwards compatibility."""

from core.utils.external_errors import (
    EXTERNAL_ERROR_CODES,
    EXTERNAL_ERROR_INDICATORS,
    is_external_resource_error,
)

__all__ = [
    "EXTERNAL_ERROR_CODES",
    "EXTERNAL_ERROR_INDICATORS",
    "is_external_resource_error",
]
