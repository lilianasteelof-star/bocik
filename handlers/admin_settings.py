"""
Handler do zarządzania ustawieniami bota przez admina
Multi-user support enabled.
"""
import logging
import html
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from database.models import SettingsManager, ChannelManager
from config import settings

logger = logging.getLogger("handlers")

# Utworzenie routera dla ustawień
admin_settings_router = Router(name="admin_settings")


@admin_settings_router.message(Command("getchannels"))
async def cmd_get_channels(message: Message):
    """
    Komenda do wyświetlenia aktualnie skonfigurowanych kanałów użytkownika
    """
    try:
        user_id = message.from_user.id
        
        # Pobranie kanałów z bazy
        channels = await ChannelManager.get_user_channels(user_id)
        
        response = "📋 <b>Twoje kanały:</b>\n\n"
        
        if channels:
            for ch in channels:
                icon = "🥇" if ch['type'] == 'premium' else "🆓"
                response += f"{icon} <b>{ch['title']}</b>\n"
                response += f"ID: <code>{ch['channel_id']}</code> | Typ: {ch['type']}\n\n"
        else:
            response += "❌ Nie masz jeszcze skonfigurowanych kanałów.\n\n"
            response += (
                "<b>Jak dodać kanał?</b>\n"
                "1. Dodaj bota jako admina do kanału.\n"
                "2. Wyślij tam wiadomość.\n"
                "3. Przekaż (forward) ją tutaj."
            )

        await message.reply(response, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Błąd komendy /getchannels: {e}", exc_info=True)
        await message.reply("❌ Wystąpił błąd podczas pobierania konfiguracji")


from utils.states import ChannelSetup

@admin_settings_router.message(Command("addchannel"))
async def cmd_add_channel(message: Message, state: FSMContext):
    """Rozpoczęcie procedury dodawania kanału"""
    await message.reply(
        "➕ **Dodawanie nowego kanału**\n\n"
        "1. Upewnij się, że dodałeś mnie (@EwhorWatchdogBot) jako Administratora do kanału.\n"
        "2. Wyślij dowolną wiadomość na tym kanale.\n"
        "3. **Przekaż (forward) tę wiadomość tutaj.**\n\n"
        "Czekam na forward...",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(ChannelSetup.waiting_for_channel_forward)


@admin_settings_router.message(ChannelSetup.waiting_for_channel_forward, F.forward_from_chat)
async def handle_forwarded_message(message: Message, state: FSMContext):
    """
    Obsługa forwardowanej wiadomości z kanału (tylko w stanie ChannelSetup)
    Automatyczne wykrywanie ID kanału i propozycja dodania do użytkownika
    """
    try:
        # Sprawdzenie czy wiadomość jest z kanału
        if not message.forward_from_chat or message.forward_from_chat.type != "channel":
            await message.reply("⚠️ To nie jest wiadomość z kanału. Spróbuj ponownie.")
            return

        user_id = message.from_user.id
        channel_id = message.forward_from_chat.id
        channel_title = message.forward_from_chat.title or "Nieznany kanał"
        safe_title = html.escape(channel_title)
        
        # Sprawdź czy bot jest adminem w tym kanale (prosta weryfikacja)
        try:
            member = await message.bot.get_chat_member(channel_id, message.bot.id)
            if member.status not in ["administrator", "creator"]:
                await message.reply("⚠️ Bot nie jest administratorem w tym kanale! Dodaj mnie najpierw.")
                return
        except Exception as e:
            await message.reply("⚠️ Nie mogę sprawdzić uprawnień w tym kanale. Upewnij się, że mnie tam dodałeś.")
            return

        # Zapisanie danych w FSM state
        await state.update_data(
            pending_channel_id=channel_id, 
            pending_channel_title=channel_title
        )

        # Sprawdzenie limitów użytkownika
        user_channels = await ChannelManager.get_user_channels(user_id)
        has_premium = any(ch['type'] == 'premium' for ch in user_channels)
        has_free = any(ch['type'] == 'free' for ch in user_channels)

        keyboard_buttons = []

        if not has_premium:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🥇 Ustaw jako kanał Premium",
                    callback_data=f"setup_channel_premium"
                )
            ])
        else:
             keyboard_buttons.append([
                InlineKeyboardButton(
                    text="❌ Limit osiągnięty (Max 1 Premium)",
                    callback_data="limit_reached_premium"
                )
            ])

        if not has_free:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🆓 Ustaw jako kanał Free / Feed",
                    callback_data=f"setup_channel_free"
                )
            ])
        else:
             keyboard_buttons.append([
                InlineKeyboardButton(
                    text="❌ Limit osiągnięty (Max 1 Free)",
                    callback_data="limit_reached_free"
                )
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(
                text="❌ Anuluj",
                callback_data="set_channel_cancel"
            )
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await message.reply(
            f"📺 <b>Wykryto kanał!</b>\n\n"
            f"Nazwa: <b>{safe_title}</b>\n"
            f"ID: <code>{channel_id}</code>\n\n"
            f"Czy chcesz go przypisać do siebie?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        # Nie czyścimy stanu tutaj, czekamy na wybór typu
        
    except Exception as e:
        logger.error(f"Błąd obsługi forwardowanej wiadomości: {e}", exc_info=True)
        await message.reply("❌ Nie udało się przetworzyć forwardowanej wiadomości")


@admin_settings_router.callback_query(F.data.startswith("limit_reached_"))
async def handle_limit_info(callback: CallbackQuery):
    """Informacja o limicie"""
    await callback.answer("🚫 Możesz posiadać tylko 1 kanał tego typu.", show_alert=True)


@admin_settings_router.callback_query(F.data.startswith("setup_channel_"))
async def handle_setup_channel(callback: CallbackQuery, state: FSMContext):
    """Finalizacja dodawania kanału"""
    try:
        data = await state.get_data()
        channel_id = data.get("pending_channel_id")
        title = data.get("pending_channel_title")
        user_id = callback.from_user.id
        
        if not channel_id:
            await callback.answer("Brak danych, spróbuj ponownie", show_alert=True)
            return

        channel_type = "premium" if "premium" in callback.data else "free"
        
        # Security: Re-check limits
        user_channels = await ChannelManager.get_user_channels(user_id)
        has_type = any(ch['type'] == channel_type for ch in user_channels)
        
        if has_type:
             await callback.answer(f"🚫 Masz już kanał typu {channel_type}! Limit: 1.", show_alert=True)
             return
        
        # Dodanie kanału do bazy (ChannelManager)
        success = await ChannelManager.create_channel(
            owner_id=user_id,
            channel_id=channel_id,
            title=title,
            type=channel_type
        )

        if success:
            await callback.message.edit_text(
                f"✅ <b>Sukces!</b>\n\n"
                f"Dodano kanał: <b>{html.escape(title)}</b>\n"
                f"Typ: {channel_type}\n\n"
                f"Teraz możesz wybrać go w menu /start",
                parse_mode=ParseMode.HTML
            )
            # Opcjonalnie: Ustawienie jako aktywny od razu?
            await state.clear()
        else:
            await callback.message.edit_text("❌ Błąd bazy danych.")

        await callback.answer()

    except Exception as e:
        logger.error(f"Błąd setup channel: {e}", exc_info=True)
        await callback.answer("❌ Wystąpił błąd", show_alert=True)


@admin_settings_router.callback_query(F.data == "set_channel_cancel")
async def handle_cancel_channel_setup(callback: CallbackQuery, state: FSMContext):
    """Anulowanie"""
    await callback.message.edit_text("❌ Anulowano.")
    await state.clear()
    await callback.answer()
