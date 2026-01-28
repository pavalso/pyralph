import sys
import threading
from io import StringIO


# Thread-local storage to track active captures and detect nesting
_capture_state = threading.local()


class CaptureStdout:
    """Context manager for capturing stdout with proper restoration.

    Captures all stdout output during the context and provides access
    to the captured content. Restores original stdout even on exception.

    Raises:
        RuntimeError: If nested capture is attempted (not supported).
    """

    def __init__(self):
        self._captured = StringIO()
        self._original_stdout = None

    def __enter__(self):
        # Check for nested capture
        if getattr(_capture_state, 'active', False):
            raise RuntimeError(
                "Nested stdout capture is not supported. "
                "Ensure previous capture context is closed before starting a new one."
            )
        _capture_state.active = True
        self._original_stdout = sys.stdout
        sys.stdout = self._captured
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._original_stdout
        _capture_state.active = False
        return False  # Don't suppress exceptions

    def getvalue(self):
        """Return captured stdout content as string.

        Returns empty string if nothing was captured.
        """
        return self._captured.getvalue()

    @property
    def output(self):
        """Alias for getvalue() for convenience."""
        return self.getvalue()
