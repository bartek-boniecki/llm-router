"""
Structured JSON logging with structlog.
Beginners: JSON logs are easier for machines to parse and for you to filter.
"""

import logging
import structlog
from app.config import settings

_configured = False


def configure_logging():
    global _configured
    if _configured:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    _configured = True


def get_logger():
    return structlog.get_logger(settings.SERVICE_NAME)
