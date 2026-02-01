"""
Middleware do autoryzacji admina i logowania zapytań
"""
import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import settings
from utils.helpers import validate_admin_command

logger = logging.getLogger("middlewares")


class AuthMiddleware(BaseMiddleware):
    """
    Middleware autoryzacji.
    - Czarna lista globalna: zbanowani nie przechodzą (ADMIN_ID pomijany).
    - Tryb konserwacji: tylko ADMIN_ID ma dostęp.
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        from database.models import GlobalBlacklist, SettingsManager
        
        user_id = getattr(getattr(event, "from_user", None), "id", None)
        if user_id is None:
            return await handler(event, data)
        
        # Super-admin zawsze ma dostęp
        if settings.is_superadmin(user_id):
            return await handler(event, data)
        
        # Czarna lista (nie blokujemy superadminów)
        if await GlobalBlacklist.is_banned(user_id):
            if isinstance(event, Message):
                await event.reply("🚫 Jesteś zablokowany.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Jesteś zablokowany.", show_alert=True)
            return
        
        # Tryb konserwacji
        if await SettingsManager.get_maintenance_mode():
            if isinstance(event, Message):
                await event.reply("🔧 Bot w konserwacji. Spróbuj później.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🔧 Bot w konserwacji.", show_alert=True)
            return
        
        return await handler(event, data)


class LoggingMiddleware(BaseMiddleware):
    """
    Middleware do szczegółowego logowania wszystkich zdarzeń
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Logowanie zdarzeń"""
        
        event_type = type(event).__name__
        
        # Szczegółowe logowanie różnych typów zdarzeń
        try:
            if hasattr(event, 'chat') and event.chat:
                chat_info = f"chat_id={event.chat.id}, chat_type={event.chat.type}"
            else:
                chat_info = "no_chat"
            
            if hasattr(event, 'from_user') and event.from_user:
                user_info = f"user_id={event.from_user.id}, username={event.from_user.username}"
            else:
                user_info = "no_user"
            
            logger.debug(f"{event_type}: {user_info}, {chat_info}")
            
            # Wywołanie handlera
            result = await handler(event, data)
            
            # Dane wyświetlania (@, imię) – dla wszystkich, żeby w panelu „Aktywni użytkownicy” było widać etykietę
            try:
                user_id = getattr(getattr(event, "from_user", None), "id", None)
                from_user = getattr(event, "from_user", None)
                if user_id and from_user:
                    from database.models import BotUsersManager
                    username = from_user.username if from_user else None
                    full_name = ((from_user.first_name or "") + " " + (from_user.last_name or "")).strip() if from_user else None
                    if not full_name and from_user:
                        full_name = from_user.first_name or None
                    await BotUsersManager.ensure_user(user_id)
                    await BotUsersManager.update_user_display_info(user_id, username=username, full_name=full_name)
            except Exception as upd_err:
                logger.debug("update_user_display_info skip: %s", upd_err)
            # Log interakcji tylko dla nie-superadminów (żeby nie zaśmiecać logów)
            try:
                user_id = getattr(getattr(event, "from_user", None), "id", None)
                if not user_id or settings.is_superadmin(user_id):
                    pass
                else:
                    from database.models import UserInteractionLog
                    if isinstance(event, Message):
                        chat = getattr(event, "chat", None)
                        if chat and getattr(chat, "type", None) == "private":
                            preview = (event.text or event.caption or "")[:200] if (event.text or event.caption) else f"[{getattr(event.content_type, 'value', event.content_type)}]"
                            await UserInteractionLog.add(user_id, "message", preview)
                    elif isinstance(event, CallbackQuery):
                        preview = (event.data or "")[:200]
                        await UserInteractionLog.add(user_id, "callback", preview)
            except Exception as log_err:
                logger.debug("Interaction log skip: %s", log_err)
            
            # Logowanie pomyślnego przetworzenia
            logger.debug(f"{event_type} przetworzony pomyślnie")
            
            return result
            
        except Exception as e:
            # Logowanie błędów
            logger.error(f"Błąd przetwarzania {event_type}: {e}")
            
            # Wysłanie informacji o błędzie do admina jeśli to możliwe
            if hasattr(event, 'from_user') and event.from_user and event.from_user.id == settings.ADMIN_ID:
                try:
                    bot = data.get('bot')
                    if bot and isinstance(event, Message):
                        await bot.send_message(
                            chat_id=settings.ADMIN_ID,
                            text=f"⚠️ **Błąd systemu:**\n`{str(e)[:200]}`",
                            parse_mode="Markdown"
                        )
                except Exception:
                    pass  # Nie logujemy błędów logowania błędów
            
            raise


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware do ograniczania liczby zapytań (rate limiting)
    """
    
    def __init__(self, max_requests_per_minute: int = 20):
        super().__init__()
        self.max_requests = max_requests_per_minute
        self.user_requests = {}  # {user_id: [timestamp, timestamp, ...]}
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Rate limiting logic"""
        
        import time
        current_time = time.time()
        
        # Pobranie user_id
        user_id = None
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
        
        if not user_id:
            # Brak user_id, kontynuuj bez rate limitingu
            return await handler(event, data)
        
        # Superadmin jest wyłączony z rate limitingu
        if settings.is_superadmin(user_id):
            return await handler(event, data)
        
        # Inicjalizacja listy zapytań dla użytkownika
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Usunięcie starych zapytań (sprzed minuty)
        minute_ago = current_time - 60
        self.user_requests[user_id] = [
            req_time for req_time in self.user_requests[user_id] 
            if req_time > minute_ago
        ]
        
        # Sprawdzenie czy przekroczono limit
        if len(self.user_requests[user_id]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded dla użytkownika {user_id}")
            
            if isinstance(event, Message):
                await event.reply(
                    "⏱️ Zbyt wiele zapytań. Poczekaj chwilę przed kolejną akcją."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⏱️ Zbyt wiele zapytań",
                    show_alert=True
                )
            
            return  # Blokowanie zapytania
        
        # Dodanie aktualnego zapytania do listy
        self.user_requests[user_id].append(current_time)
        
        # Kontynuacja przetwarzania
        return await handler(event, data)


class DatabaseMiddleware(BaseMiddleware):
    """
    Middleware zapewniające dostęp do bazy danych w handlerach
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Dodanie połączenia z bazą danych do kontekstu"""
        
        from database.connection import db_manager
        
        try:
            # Zapewnienie połączenia z bazą danych
            connection = await db_manager.get_connection()
            data['db_connection'] = connection
            
            # Wywołanie handlera
            return await handler(event, data)
            
        except Exception as e:
            logger.error(f"Błąd middleware bazy danych: {e}")
            raise




