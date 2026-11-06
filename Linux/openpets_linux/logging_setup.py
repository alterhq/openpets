"""Per-process OpenPets logs with rotation and private file permissions."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time


LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
_HANDLER_MARKER = "_openpets_file_handler"


def default_log_directory() -> Path:
    override = os.environ.get("OPENPETS_LOG_DIR")
    if override:
        return Path(override).expanduser()
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "openpets" / "logs"


def configure_logging(role: str, directory: str | os.PathLike | None = None) -> Path:
    """Configure root logging to a private, rotating file unique to this process."""
    role = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(role)).strip(".-") or "process"
    log_dir = Path(directory).expanduser() if directory is not None else default_log_directory()
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        log_dir.chmod(0o700)
    except OSError:
        pass

    log_path = log_dir / f"openpets-{role}-{os.getpid()}.log"
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s pid=%(process)d thread=%(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    try:
        log_path.chmod(0o600)
    except OSError:
        pass
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)

    _install_exception_hooks()
    logging.getLogger("openpets").info(
        "logging initialized role=%s file=%s", role, log_path
    )
    return log_path


def _install_exception_hooks() -> None:
    if not getattr(sys, "_openpets_excepthook_installed", False):
        previous = sys.excepthook

        def log_uncaught(exc_type, exc_value, exc_traceback):
            logging.getLogger("openpets").critical(
                "uncaught main-thread exception",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            previous(exc_type, exc_value, exc_traceback)

        sys.excepthook = log_uncaught
        sys._openpets_excepthook_installed = True

    if hasattr(threading, "excepthook") and not getattr(
        threading, "_openpets_excepthook_installed", False
    ):
        previous_thread_hook = threading.excepthook

        def log_thread_exception(args):
            logging.getLogger("openpets").error(
                "uncaught worker-thread exception thread=%s",
                args.thread.name if args.thread else "unknown",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous_thread_hook(args)

        threading.excepthook = log_thread_exception
        threading._openpets_excepthook_installed = True


def log_process_memory(logger: logging.Logger, *, role: str) -> None:
    """Log Linux process memory counters without a third-party dependency."""
    values = {}
    try:
        with open("/proc/self/status", encoding="ascii") as status_file:
            for line in status_file:
                key, separator, value = line.partition(":")
                if separator and key in {
                    "VmRSS", "RssAnon", "RssFile", "RssShmem", "VmSwap"
                }:
                    values[key] = value.strip().replace("\t", " ")
        logger.info("process memory role=%s %s", role, " ".join(
            f"{key}={values.get(key, 'n/a')}" for key in
            ("VmRSS", "RssAnon", "RssFile", "RssShmem", "VmSwap")
        ))
    except OSError:
        logger.warning("could not read /proc/self/status for memory snapshot", exc_info=True)


def elapsed_ms(started: float) -> float:
    """Return elapsed milliseconds from a ``time.perf_counter()`` value."""
    return (time.perf_counter() - started) * 1000.0
