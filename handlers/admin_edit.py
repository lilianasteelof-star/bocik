"""
Handler do edycji istniejących subskrypcji
Komenda /edit <user_id> LUB /edit @username
POPRAWIONA WERSJA: Obsługa ID oraz @username
"""
import logging
import html
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from config import settings
from database.models import SubscriptionManager
from utils.states import SubscriptionEditing
from utils.helpers import parse_end_date_from_text

logger = logging.getLogger("handlers")
admin_edit_router = Router()


@admin_edit_router.message(Command("edit"))
async def cmd_edit_subscription(message: Message, state: FSMContext):
    """
    Panel edycji subskrypcji
    Użycie: /edit 123456789 LUB /edit @nazwa
    """
    try:
        owner_id = message.from_user.id
        # Removed global ADMIN_ID check -> any user can edit their subs

        # Parsowanie argumentów
        args = message.text.split()
        if len(args) != 2:
            await message.reply(
                "❌ <b>Nieprawidłowe użycie</b>\n\n"
                "Poprawnie:\n"
                "• <code>/edit 123456789</code> (ID)\n"
                "• <code>/edit @nazwa_uzytkownika</code>",
                parse_mode=ParseMode.HTML
            )
            return

        identifier = args[1]
        sub = None

        # --- LOGIKA WYSZUKIWANIA ---

        # Opcja 1: Wyszukiwanie po @username
        if identifier.startswith("@"):
            username = identifier
            sub = await SubscriptionManager.get_subscription_by_username_for_owner(username, owner_id)

            if not sub:
                await message.reply(
                    f"❌ <b>Nie znaleziono użytkownika {username} w bazie.</b>\n"
                    f"Upewnij się, że użytkownik jest już w systemie (dołączył do kanału).",
                    parse_mode=ParseMode.HTML
                )
                return

        # Opcja 2: Wyszukiwanie po ID (jeśli to liczba)
        elif identifier.isdigit() or (identifier.startswith("-") and identifier[1:].isdigit()):
            try:
                user_id = int(identifier)
                sub = await SubscriptionManager.get_subscription_for_owner(user_id, owner_id)

                if not sub:
                    await message.reply(f"❌ Nie znaleziono subskrypcji dla ID <code>{user_id}</code>.", parse_mode=ParseMode.HTML)
                    return
            except ValueError:
                await message.reply("❌ Błędny format ID.")
                return
        
        # Opcja 3: Błędny format
        else:
            await message.reply(
                "❌ <b>Błąd formatu</b>\n"
                "Podaj ID (liczba) lub Username (zaczynający się od @).",
                parse_mode=ParseMode.HTML
            )
            return

        # --- WYŚWIETLANIE PANELU ---

        user_id = sub.user_id
        channel_id = sub.channel_id

        # Zapisanie ID w stanie (channel_id potrzebny do update_subscription_details)
        await state.update_data(edit_user_id=user_id, edit_channel_id=channel_id)

        # Przygotowanie widoku
        safe_name = html.escape(sub.full_name)
        safe_username = html.escape(sub.username or "brak")
        end_date_str = sub.end_date.strftime('%Y-%m-%d %H:%M')

        text = (
            f"✏️ <b>Edycja Subskrypcji</b>\n\n"
            f"👤 <b>{safe_name}</b> (@{safe_username})\n"
            f"🆔 <code>{user_id}</code>\n"
            f"💎 Obecny Tier: <b>{sub.tier}</b>\n"
            f"📅 Obecny Koniec: <code>{end_date_str}</code>\n"
            f"📊 Status: {sub.status}\n\n"
            f"Co chcesz zmienić?"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Zmień datę", callback_data="edit_action_date"),
                InlineKeyboardButton(text="💎 Zmień Tier", callback_data="edit_action_tier")
            ],
            [
                InlineKeyboardButton(text="❌ Anuluj", callback_data="edit_action_cancel")
            ]
        ])

        await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Błąd komendy edit: {e}", exc_info=True)
        await message.reply("❌ Błąd systemu")


@admin_edit_router.callback_query(F.data == "edit_action_date")
async def cb_edit_date(callback: CallbackQuery, state: FSMContext):
    """Kliknięto Zmień datę"""
    await state.set_state(SubscriptionEditing.waiting_for_new_date)
    await callback.message.edit_text(
        "📅 <b>Wpisz nową datę zakończenia:</b>\n\n"
        "Format: <code>YYYY-MM-DD HH:MM</code>\n"
        "Np: <code>2026-06-01 12:00</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@admin_edit_router.message(SubscriptionEditing.waiting_for_new_date)
async def process_new_date(message: Message, state: FSMContext, bot: Bot):
    """Przetwarzanie nowej daty"""
    try:
        new_date = parse_end_date_from_text(message.text)
        if not new_date:
            await message.reply("❌ Błędny format. Spróbuj: <code>YYYY-MM-DD HH:MM</code>", parse_mode=ParseMode.HTML)
            return

        data = await state.get_data()
        user_id = data.get("edit_user_id")
        channel_id = data.get("edit_channel_id")

        if not user_id:
            await message.reply("❌ Błąd sesji. Wpisz /edit ponownie.")
            await state.clear()
            return

        # Aktualizacja w bazie (channel_id z kontekstu edycji)
        success = await SubscriptionManager.update_subscription_details(
            user_id, channel_id=channel_id, new_end_date=new_date
        )

        if success:
            await message.reply(
                f"✅ <b>Zaktualizowano datę!</b>\n"
                f"📅 Nowy koniec: <code>{new_date.strftime('%Y-%m-%d %H:%M')}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")
                ]])
            )

            # Powiadomienie usera
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"🔄 <b>Aktualizacja subskrypcji</b>\n\nTwój dostęp został przedłużony do: <code>{new_date.strftime('%Y-%m-%d %H:%M')}</code>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        else:
            await message.reply("❌ Błąd zapisu do bazy.")

        await state.clear()

    except Exception as e:
        logger.error(f"Błąd edycji daty: {e}", exc_info=True)
        await state.clear()


@admin_edit_router.callback_query(F.data == "edit_action_tier")
async def cb_edit_tier(callback: CallbackQuery, state: FSMContext):
    """Kliknięto Zmień Tier"""
    await state.set_state(SubscriptionEditing.waiting_for_new_tier)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥉 Bronze", callback_data="set_tier_Bronze")],
        [InlineKeyboardButton(text="🥈 Silver", callback_data="set_tier_Silver")],
        [InlineKeyboardButton(text="🥇 Gold", callback_data="set_tier_Gold")],
        [InlineKeyboardButton(text="🔙 Powrót", callback_data="edit_action_cancel")]
    ])

    await callback.message.edit_text(
        "💎 <b>Wybierz nowy poziom subskrypcji:</b>",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@admin_edit_router.callback_query(F.data.startswith("set_tier_"), SubscriptionEditing.waiting_for_new_tier)
async def process_new_tier(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Przetwarzanie nowego tieru"""
    try:
        new_tier = callback.data.split("_")[2] # set_tier_Gold -> Gold

        data = await state.get_data()
        user_id = data.get("edit_user_id")
        channel_id = data.get("edit_channel_id")

        if not user_id:
            await callback.answer("❌ Błąd sesji", show_alert=True)
            await state.clear()
            return

        success = await SubscriptionManager.update_subscription_details(
            user_id, channel_id=channel_id, new_tier=new_tier
        )

        if success:
            await callback.message.edit_text(
                f"✅ <b>Zaktualizowano Tier!</b>\n"
                f"💎 Nowy poziom: <b>{new_tier}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")
                ]])
            )

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"🔄 <b>Aktualizacja subskrypcji</b>\n\nTwój pakiet został zmieniony na: <b>{new_tier}</b>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        else:
            await callback.message.edit_text("❌ Błąd zapisu do bazy.")

        await state.clear()
        await callback.answer()

    except Exception as e:
        logger.error(f"Błąd edycji tieru: {e}", exc_info=True)
        await state.clear()


@admin_edit_router.callback_query(F.data == "edit_action_cancel")
async def cb_edit_cancel(callback: CallbackQuery, state: FSMContext):
    """Anulowanie"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Edycja anulowana.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")
        ]])
    )
    await callback.answer()