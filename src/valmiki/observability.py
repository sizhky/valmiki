"""Production logging for HTTP requests and reader events."""

import os
import sys
import time
import uuid
from pathlib import Path

from loguru import logger


def configure_logging(default_log_dir: Path) -> None:
    """Send searchable JSON logs to stderr and rotating files."""
    logger.remove()
    level = os.getenv("VALMIKI_LOG_LEVEL", "INFO").upper()
    logger.add(
        sys.stderr,
        level=level,
        serialize=True,
        backtrace=False,
        diagnose=False,
    )
    log_dir = Path(os.getenv("VALMIKI_LOG_DIR", default_log_dir))
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "valmiki.jsonl",
        level=level,
        serialize=True,
        rotation="1 day",
        retention="14 days",
        compression="gz",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )


def install_request_logging(app) -> None:
    """Log one completion or failure event for every HTTP request."""

    @app.middleware("http")
    async def log_request(request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        context = {
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        }
        with logger.contextualize(**context):
            try:
                response = await call_next(request)
            except Exception:
                logger.bind(
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                ).exception("request_failed")
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers["x-request-id"] = request_id
            completed = logger.bind(
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                htmx=request.headers.get("hx-request") == "true",
            )
            if response.status_code >= 500:
                completed.error("request_completed")
            elif response.status_code >= 400:
                completed.warning("request_completed")
            else:
                completed.info("request_completed")
            return response
