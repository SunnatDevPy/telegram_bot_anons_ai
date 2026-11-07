"""
Loyihaning markaziy logging konfiguratsiyasi.

Barcha modullar `print()` o'rniga shu yerdagi logger'dan foydalanadi:
    from logging_setup import get_logger
    log = get_logger(__name__)
    log.info("...")

Bu loglarni darajalash, filtrlash va kelajakda tashqi monitoringga
(Sentry, Loki va h.k.) ulash imkonini beradi.
"""
import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """Root logger'ni bir marta sozlash (main.py boshida chaqiriladi)."""
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root.addHandler(handler)
    root.setLevel(level)

    # Ayrim shovqinli kutubxonalar loglarini pasaytirish
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Modul uchun logger olish."""
    setup_logging()
    return logging.getLogger(name)
