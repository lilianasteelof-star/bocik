"""
Handler dla skrótów komend /premium i /free
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from database.models import SettingsManager, ChannelManager
from handlers.admin_stats import send_channel_stats
from utils.scheduler import BotScheduler

logger = logging.getLogger("handlers")
shortcuts_router = Router(name="shortcuts")

@shortcuts_router.message(Command("premium"))
async def cmd_premium_shortcut(message: Message, state: FSMContext, scheduler: BotScheduler = None):
    """Skrót do obsługi kanału Premium"""
    await _handle_channel_shortcut(message, state, "premium", scheduler)

@shortcuts_router.message(Command("free"))
async def cmd_free_shortcut(message: Message, state: FSMContext, scheduler: BotScheduler = None):
    """Skrót do obsługi kanału Free"""
    await _handle_channel_shortcut(message, state, "free", scheduler)

async def _handle_channel_shortcut(message: Message, state: FSMContext, channel_type: str, scheduler: BotScheduler = None):
    """Wspólna logika dla skrótów"""
    try:
        user_id = message.from_user.id
        args = message.text.split()[1:] if message.text else []
        action = args[0].lower() if args else None
        
        # 1. Rozwiązanie ID kanału
        target_channel_id = None
        if channel_type == "premium":
            target_channel_id = await SettingsManager.get_premium_channel_id(user_id)
        else:
            target_channel_id = await SettingsManager.get_free_channel_id(user_id)
            
        if not target_channel_id:
            await message.reply(
                f"⚠️ Nie masz skonfigurowanego kanału **{channel_type.capitalize()}**.\n"
                f"Użyj /start lub /settings aby to naprawić.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # 2. Ustawienie kontekstu
        await state.update_data(active_channel_id=target_channel_id)
        
        # 3. Obsługa akcji
        if action == "stats":
            # Wywołanie logiki statystyk
            if scheduler:
                await send_channel_stats(message, target_channel_id, scheduler)
            else:
                await message.reply("❌ Błąd systemu: Brak dostępu do schedulera.")
                
        elif action == "settings":
             # Przekierowanie do ustawień (możemy po prostu wyświetlić info o kanale)
             # TODO: Lepiej byłoby wywołać handler settings, ale on jest na callbackach
             await message.reply(
                 f"⚙️ **Ustawienia kanału {channel_type.capitalize()}**\n"
                 f"ID: `{target_channel_id}`\n"
                 f"Aby zmienić, użyj /start -> Wybierz kanał -> Ustawienia"
             )
             
        else:
            # Domyślna akcja: Potwierdzenie wyboru i menu
            # Pobranie tytułu kanału dla ładniejszego komunikatu
            channels = await ChannelManager.get_user_channels(user_id)
            channel_info = next((ch for ch in channels if ch['channel_id'] == target_channel_id), None)
            title = channel_info['title'] if channel_info else "Nieznany"
            
            await message.reply(
                f"✅ **Przełączono na {channel_type.capitalize()}: {title}**\n\n"
                f"Możesz teraz używać komend dla tego kanału:\n"
                f"/users - Subskrybenci\n"
                f"/newpost - Nowy post\n"
                f"/stats - Statystyki",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🏠 Menu Główne", callback_data="refresh_channels")
                ]])
            )
            
    except Exception as e:
        logger.error(f"Błąd skrótu {channel_type}: {e}", exc_info=True)
        await message.reply("❌ Wystąpił błąd podczas przełączania kanału.")
