"""
Handler do zarządzania subskrypcjami - FSM dla wyboru tier/duration
POPRAWIONA WERSJA: Multi-Channel Support
"""
import logging
import html
from datetime import datetime

from aiogram import Router, Bot, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from config import settings
from database.models import SubscriptionManager, ChannelManager
from utils.states import SubscriptionManagement
from utils.helpers import (
    create_duration_keyboard,
    get_tier_duration_from_callback,
    parse_end_date_from_text,
    create_tier_keyboard
)

logger = logging.getLogger("handlers")
admin_subs_router = Router()

# =================================================================================================
# MANUALNY DODAWANIE UŻYTKOWNIKA (START)
# =================================================================================================

@admin_subs_router.callback_query(F.data.startswith("add_user_to_"))
async def add_user_to_channel_start(callback: CallbackQuery, state: FSMContext):
    """Rozpoczęcie dodawania użytkownika do konkretnego kanału"""
    try:
        # format: add_user_to_CHANNELID
        channel_id = int(callback.data.split("_")[-1])
        
        await state.update_data(active_channel_id=channel_id)
        await state.set_state(SubscriptionManagement.waiting_user_id)
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Anuluj", callback_data=f"manage_channel_{channel_id}")
        ]])
        await callback.message.edit_text(
            f"➕ **Dodawanie użytkownika**\n\n"
            f"Podaj **ID użytkownika** (Telegram ID), któremu chcesz nadać subskrypcję.\n"
            f"Możesz też przekazać (forward) wiadomość od tego użytkownika tutaj.",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Błąd add_user_start: {e}")
        await callback.answer("Błąd")

@admin_subs_router.message(SubscriptionManagement.waiting_user_id)
async def handle_user_id_input(message: Message, state: FSMContext):
    """Odbiór ID użytkownika (tekst lub forward)"""
    try:
        user_id = None
        user_name = "Unknown"
        full_name = "Manual Added"
        
        # 1. Sprawdź czy to forward
        if message.forward_from:
            user_id = message.forward_from.id
            user_name = message.forward_from.username or "brak"
            full_name = f"{message.forward_from.first_name} {message.forward_from.last_name or ''}".strip()
        
        # 2. Sprawdź czy to tekst (ID)
        elif message.text and message.text.isdigit():
            user_id = int(message.text)
            # Spróbujemy pobrać info (może się nie udać jeśli bot nie zna usera)
            # Ale zapiszmy ID
        
        if not user_id:
            await message.reply("❌ Nieprawidłowe ID. Wyślij liczbę lub przekaż wiadomość.")
            return

        # Zapisz dane
        await state.update_data(
            target_user_id=user_id,
            target_username=user_name,
            target_full_name=full_name
        )
        
        # Pobierz channel_id ze stanu
        data = await state.get_data()
        channel_id = data.get('active_channel_id')
        
        if not channel_id:
            await message.reply("❌ Błąd kontekstu kanału. Zacznij od nowa.")
            await state.clear()
            return
            
        # Przejdź do wyboru Tieru
        # Używamy helpera z channel_id
        await message.reply(
            f"✅ Użytkownik: `{user_id}`\n"
            f"Wybierz kategorię subskrypcji:",
            reply_markup=create_tier_keyboard(user_id, channel_id)
        )
        # Nie musimy ustawiać waiting_tier, bo callback 'tier_...' obsłuży resztę
        # Ale możemy wyczyścić stan waiting_user_id
        await state.set_state(None) # Reset state to handle generic callbacks? 
        # Actually handle_tier_selection expects state content if partial.
        # But here we pass everything in callback data (tier_Tier_UserId_ChannelId).
        # So we can clear state or keep it.
        # Let's keep data in state just in case.
        
    except Exception as e:
        logger.error(f"Błąd user input: {e}")
        await message.reply("❌ Wystąpił błąd.")

# =================================================================================================
# CIĄG DALSZY (TIER -> DURATION -> CREATE)
# =================================================================================================

@admin_subs_router.callback_query(F.data.startswith("tier_"))
async def handle_tier_selection(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Obsługa wyboru kategorii subskrypcji"""
    try:
        # Parsowanie callback_data: tier_Bronze_USERID_CHANNELID
        parts = callback.data.split("_")
        
        if len(parts) >= 4:
            tier = parts[1]
            user_id = int(parts[2])
            channel_id = int(parts[3])
        elif len(parts) == 3: # Legacy / Fallback (active join flow usually sends 3: tier_Tier_UserId but WITHOUT ChannelId if using old helper)
             # Ale my zaktualizowaliśmy helpera i events.py, więc powinno być 4.
             # Jednak dla bezpieczeństwa:
             tier = parts[1]
             user_id = int(parts[2])
             # Spróbuj pobrać channel_id ze stanu, jeśli możliwe, lub fallback
             data = await state.get_data()
             channel_id = data.get('active_channel_id')
        else:
            await callback.answer("❌ Błąd danych przycisku", show_alert=True)
            return

        if not channel_id:
             await callback.answer("❌ Błąd: Brak ID kanału", show_alert=True)
             return

        logger.info(f"Wybrano tier: {tier} dla user {user_id} w kanale {channel_id}")

        # Pobierz info o użytkowniku z Telegram API (dla pewności)
        username = "unknown"
        full_name = "Unknown User"
        try:
            user_info = await bot.get_chat(user_id)
            username = user_info.username or "brak"
            full_name = f"{user_info.first_name} {user_info.last_name or ''}".strip()
        except Exception as e:
            # Jeśli manual add, mogliśmy zapisać w stanie wcześniej
            data = await state.get_data()
            username = data.get('target_username', username)
            full_name = data.get('target_full_name', full_name)

        safe_full_name = html.escape(full_name)

        # Aktualizacja wiadomości
        await callback.message.edit_text(
            text=(
                f"✅ Wybrano kategorię: <b>{tier}</b>\n"
                f"👤 Użytkownik: {safe_full_name}\n"
                f"📢 Kanał ID: `{channel_id}`\n\n"
                f"⏰ <b>Wybierz czas trwania subskrypcji:</b>"
            ),
            reply_markup=create_duration_keyboard(user_id), # Duration keyboard is generic (duration_30_USERID)
            parse_mode=ParseMode.HTML
        )

        # Zapisanie Danych w FSM (Kluczowe dla kroku Duration)
        await state.update_data(
            tier=tier,
            target_user_id=user_id,     # Ujednolicenie klucza
            active_channel_id=channel_id,
            target_username=username,
            target_full_name=full_name
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Błąd wyboru kategorii: {e}", exc_info=True)
        await callback.answer(f"❌ Błąd: {str(e)[:50]}", show_alert=True)


@admin_subs_router.callback_query(
    F.data.startswith("duration_") & ~F.data.startswith("duration_custom_")
)
async def handle_duration_selection(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Obsługa wyboru czasu trwania subskrypcji"""
    try:
        # Parsowanie callback_data: duration_30_USERID
        parts = callback.data.split("_")
        if len(parts) >= 3:
            duration_str = parts[1]
            user_id = int(parts[2])

            if duration_str == "lifetime":
                duration = 36500
            else:
                duration = int(duration_str)
        else:
            await callback.answer("❌ Błąd danych w przycisku", show_alert=True)
            return

        # Pobranie danych z FSM
        data = await state.get_data()
        tier = data.get("tier")
        channel_id = data.get("active_channel_id")
        
        # UserID z callbacka powinien pasować do tego z session, ale ufamy callbackowi lub session
        # Użyjmy danych z sesji dla spójności
        username = data.get("target_username", "brak")
        full_name = data.get("target_full_name", "Unknown User")
        
        owner_id = callback.from_user.id # Admin wykonujący akcję

        if not tier or not channel_id:
            await callback.answer("❌ Błąd sesji: brak tier lub channel_id", show_alert=True)
            return

        # Utworzenie subskrypcji w bazie
        success = await SubscriptionManager.create_subscription(
            user_id=user_id,
            owner_id=owner_id,
            channel_id=channel_id, # FIX: Pass channel_id
            username=username,
            full_name=full_name,
            tier=tier,
            duration_days=duration
        )

        if success:
            subscription = await SubscriptionManager.get_subscription(user_id, channel_id) # FIX: Pass channel_id

            if subscription:
                end_date_str = subscription.end_date.strftime('%d.%m.%Y %H:%M')
                safe_full_name = html.escape(full_name)

                # Pobranie info o kanale dla linku
                channel_info_str = f"`{channel_id}`"
                try:
                    chat = await bot.get_chat(channel_id)
                    if chat.username:
                        channel_info_str = f"[{chat.title}](https://t.me/{chat.username})"
                    elif chat.invite_link:
                        channel_info_str = f"[{chat.title}]({chat.invite_link})"
                    else:
                        channel_info_str = f"{chat.title} (ID: `{channel_id}`)"
                except Exception as e:
                    logger.warning(f"Failed to fetch chat info for success msg: {e}")

                # Potwierdzenie dla admina
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                await callback.message.edit_text(
                    text=(
                        f"✅ <b>Subskrypcja utworzona!</b>\n\n"
                        f"👤 <a href='tg://user?id={user_id}'>{safe_full_name}</a>\n"
                        f"📢 Kanał: {channel_info_str}\n"
                        f"💎 Tier: <b>{tier}</b>\n"
                        f"📅 Wygasa: <code>{end_date_str}</code>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🔙 Panel kanału", callback_data=f"manage_channel_{channel_id}"),
                        InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")
                    ]])
                )

                # Powiadomienie użytkownika
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🎉 <b>Witaj w Premium!</b>\n\n"
                            f"Twoja subskrypcja <b>{tier}</b> jest aktywna do "
                            f"<code>{end_date_str}</code>\n\n"
                            f"Ciesz się ekskluzywną zawartością! 🌟"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.warning(f"Nie można wysłać powitania do {user_id}: {e}")

            await callback.answer("✅ Sukces!")

        else:
            await callback.message.edit_text("❌ Błąd bazy danych przy tworzeniu subskrypcji.")
            await callback.answer("❌ Błąd bazy danych", show_alert=True)

        await state.clear()

    except Exception as e:
        logger.error(f"Błąd duration: {e}", exc_info=True)
        await callback.answer("❌ Błąd krytyczny")
        await state.clear()


@admin_subs_router.callback_query(F.data.startswith("duration_custom_"))
async def handle_custom_date_request(callback: CallbackQuery, state: FSMContext):
    """Obsługa wyboru niestandardowej daty"""
    try:
        # duration_custom_USERID
        parts = callback.data.split("_")
        user_id = int(parts[2])
        
        await state.update_data(target_user_id=user_id)
        await state.set_state(SubscriptionManagement.waiting_custom_date)
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        data = await state.get_data()
        ch_id = data.get("active_channel_id")
        back_btn = [InlineKeyboardButton(text="🔙 Anuluj", callback_data=f"manage_channel_{ch_id}")] if ch_id else []
        await callback.message.edit_text(
            text=(
                f"📅 <b>Wpisz datę zakończenia subskrypcji</b>\n\n"
                f"Format: `YYYY-MM-DD HH:MM` (np. 2026-05-20 18:00)\n"
                f"Wpisz datę w wiadomości:"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_btn]) if back_btn else None
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Custom date error: {e}")
        await callback.answer("Błąd")


@admin_subs_router.message(SubscriptionManagement.waiting_custom_date)
async def handle_custom_date_input(message: Message, state: FSMContext, bot: Bot):
    """Obsługa wpisanej daty custom"""
    try:
        data = await state.get_data()
        user_id = data.get("target_user_id")
        tier = data.get("tier")
        channel_id = data.get("active_channel_id")
        username = data.get("target_username", "brak")
        full_name = data.get("target_full_name", "Unknown")
        owner_id = message.from_user.id

        if not user_id or not tier or not channel_id:
            await message.reply("❌ Błąd sesji. Rozpocznij od nowa.")
            await state.clear()
            return

        end_date = parse_end_date_from_text(message.text)
        if not end_date:
            await message.reply("❌ Nieprawidłowy format daty.")
            return

        if end_date < datetime.now():
            await message.reply("⚠️ Data musi być w przyszłości!")
            return

        success = await SubscriptionManager.create_subscription(
            user_id=user_id,
            owner_id=owner_id,
            channel_id=channel_id,
            username=username,
            full_name=full_name,
            tier=tier,
            end_date=end_date
        )

        if success:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            await message.reply(
                f"✅ <b>Subskrypcja Custom Utworzona!</b>\n"
                f"Do: {end_date.strftime('%Y-%m-%d %H:%M')}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Panel kanału", callback_data=f"manage_channel_{channel_id}"),
                    InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")
                ]])
            )
        else:
            await message.reply("❌ Błąd bazy danych.")

        await state.clear()

    except Exception as e:
        logger.error(f"Custom date input error: {e}")
        await message.reply("❌ Błąd.")
        await state.clear()