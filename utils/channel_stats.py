"""
Opcjonalna średnia wyświetleń na post z kanału (ostatnie N postów).
Bez Telethon/Pyrogram zwracane jest None – Bot API nie udostępnia historii postów kanału.
Aby włączyć: zainstaluj Telethon, ustaw TELEGRAM_API_ID i TELEGRAM_API_HASH w .env,
i zaimplementuj get_channel_avg_views_telethon() (get_messages + message.views).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def get_channel_avg_views(channel_id: int, limit: int = 10) -> Optional[int]:
    """
    Średnia wyświetleń na post z ostatnich do `limit` postów na kanale.
    Zwraca None, jeśli nie skonfigurowano Telethon (Bot API nie ma dostępu do historii kanału).
    """
    # Opcjonalna integracja Telethon: jeśli w config są API_ID/API_HASH,
    # można tu wywołać client.get_messages(channel_id, limit=limit) i uśrednić message.views.
    return None
