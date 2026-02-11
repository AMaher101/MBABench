"""This file initializes a global singleton logger instance for the entire application."""

import logging
import os

DEFAULT_LOG_FORMAT = (
    "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
)

logger = logging.getLogger(os.environ.get("LOGGER_NAME", "bizbench_judge"))
logger.setLevel(logging.DEBUG)
_logger_file_handlers: dict[str, logging.FileHandler] = {}

if os.environ.get("LOG_TO_TERMINAL", "true").lower() == "true":
    _stream_handler = logging.StreamHandler()
    _stream_handler.setLevel(logging.DEBUG)
    _stream_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    logger.addHandler(_stream_handler)


def add_log_file(file_path: str, level: int = logging.DEBUG) -> None:
    """Add a file handler for the given path. Skips if already registered."""
    if file_path in _logger_file_handlers:
        return
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    handler = logging.FileHandler(file_path)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _logger_file_handlers[file_path] = handler


def remove_log_file(file_path: str) -> None:
    """Remove and close the file handler for the given path. No-op if not registered."""
    handler = _logger_file_handlers.pop(file_path, None)
    if handler is None:
        return
    logger.removeHandler(handler)
    handler.close()


if __name__ == "__main__":
    test_log_path = os.path.join(os.path.dirname(__file__), "test.log")

    # Test the logger
    add_log_file(test_log_path)
    logger.info("This is a test log message.")
    remove_log_file(test_log_path)

    logger.info("This message should not be in the file.")
