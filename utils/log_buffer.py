"""
Bufor ostatnich logów w pamięci – do podglądu w panelu super-admina (bez obciążania bota/pliku).
"""
import logging
from collections import deque
from threading import Lock

# Ostatnie N linii (np. 100)
MAX_LINES = 100
_lines: deque = deque(maxlen=MAX_LINES)
_lock = Lock()


class BufferHandler(logging.Handler):
    """Handler zapisujący logi do bufora w pamięci."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with _lock:
                _lines.append(msg)
        except Exception:
            self.handleError(record)


def get_recent_lines(n: int = 40) -> list[str]:
    """Zwraca ostatnie n linii z bufora (od najstarszego do najnowszego)."""
    with _lock:
        return list(_lines)[-n:]


def setup_buffer_handler(logger_name: str = None) -> None:
    """Dodaje BufferHandler do loggera (domyślnie root)."""
    log = logging.getLogger(logger_name)
    for h in log.handlers:
        if isinstance(h, BufferHandler):
            return
    handler = BufferHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
    log.addHandler(handler)
