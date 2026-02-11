"""This file initializes a global singleton logger instance for the entire application."""

import logging
import os

DEFAULT_LOG_FORMAT = (
    "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
)

_logger = None
_logger_init_in_progress = False
_logger_file_handlers: dict[str, logging.FileHandler] = {}


def _get_logger():
    """Get the project logger, initializing on first access."""
    global _logger, _logger_init_in_progress
    if _logger is not None:
        return _logger
    if _logger_init_in_progress:
        # Recursive call during init (e.g. load_env_var logging a warning).
        # Return the same underlying logger object by name; handlers added
        # after init completes will apply since it's the same instance.
        return logging.getLogger("bizbench_judge")

    _logger_init_in_progress = True
    from .misc_utils import load_env_var

    _logger = logging.getLogger(
        load_env_var("MISCS_LOGGER_NAME", default="bizbench_judge")
    )
    _logger.setLevel(logging.DEBUG)

    if str(load_env_var("MISCS_LOG_TO_TERMINAL", default="true")).lower() == "true":
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.DEBUG)
        stream_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
        _logger.addHandler(stream_handler)

    _logger_init_in_progress = False
    return _logger


def __getattr__(name):
    """Module-level __getattr__ so `from .logger import logger` works lazily."""
    if name == "logger":
        return _get_logger()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def add_log_file(file_path: str, level: int = logging.DEBUG) -> None:
    """Add a file handler for the given path. Skips if already registered."""
    if file_path in _logger_file_handlers:
        return
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    handler = logging.FileHandler(file_path)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    _get_logger().addHandler(handler)
    _logger_file_handlers[file_path] = handler


def remove_log_file(file_path: str) -> None:
    """Remove and close the file handler for the given path. No-op if not registered."""
    handler = _logger_file_handlers.pop(file_path, None)
    if handler is None:
        return
    _get_logger().removeHandler(handler)
    handler.close()


if __name__ == "__main__":
    test_log_path = os.path.join(os.path.dirname(__file__), "test.log")

    # Test the logger
    add_log_file(test_log_path)
    _get_logger().info("This is a test log message.")
    remove_log_file(test_log_path)

    _get_logger().info("This message should not be in the file.")
