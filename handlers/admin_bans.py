"""
Handler do zarządzania zbanowanymi użytkownikami
Komenda /banned i callback do unbana
"""
import logging
import html
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import settings
from database.models import db_manager, SubscriptionManager

logger = logging.getLogger("handlers")
admin_bans_router = Router()


@admin_bans_router.message(Command("banned"))
async def cmd_list_banned(message: Message):
    """Lista zbanowanych użytkowników"""
    try:
        user_id = message.from_user.id
        # Removed global ADMIN_ID check

        connection = await db_manager.get_connection()

        # Pobieramy tylko tych ze statusem 'banned', sortując od najnowszego (wg end_date)
        # Filter by owner_id
        async with connection.execute("""
            SELECT * FROM subscriptions 
            WHERE status = 'banned' AND owner_id = ?
            ORDER BY end_date DESC
            LIMIT 50
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await message.reply("✅ <b>Brak zbanowanych użytkowników</b>", parse_mode=ParseMode.HTML)
            return

        response = f"🚫 <b>Lista zbanowanych ({len(rows)}):</b>\n\n"

        keyboard_builder = []

        for row in rows[:10]:  # Limit 10 przycisków żeby nie zaśmiecić
            uid = row['user_id']
            ch_id = row['channel_id']
            name = row['full_name'][:15]  # Przycinamy długie nazwy
            keyboard_builder.append([
                InlineKeyboardButton(
                    text=f"🔓 Odbanuj: {name}",
                    callback_data=f"unban_{uid}_{ch_id}"
                )
            ])

            safe_name = html.escape(row['full_name'])
            safe_user = html.escape(row['username'] or "brak")
            end_date = row['end_date'][:16]

            response += (
                f"👤 <b>{safe_name}</b> (@{safe_user})\n"
                f"🆔 <code>{uid}</code> | 📅 Wygasł: {end_date}\n\n"
            )

        if len(rows) > 10:
            response += f"<i>... i {len(rows) - 10} więcej (pokazuję 10 najnowszych)</i>"

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_builder)

        await message.reply(
            text=response,
            reply_markup=markup,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Błąd listy banów: {e}", exc_info=True)
        await message.reply("❌ Błąd pobierania listy")


@admin_bans_router.callback_query(F.data.startswith("unban_"))
async def handle_unban_callback(callback: CallbackQuery, bot: Bot):
    """Obsługa przycisku odbanowania (callback: unban_USERID_CHANNELID)"""
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("❌ Błąd danych przycisku.", show_alert=True)
            return
        user_id = int(parts[1])
        channel_id = int(parts[2])
        owner_id = callback.from_user.id

        # 1. Odbanowanie na Telegramie (na tym kanale)
        try:
            await bot.unban_chat_member(
                chat_id=channel_id,
                user_id=user_id,
                only_if_banned=True
            )
        except Exception as e:
            logger.warning(f"Telegram unban error for {user_id}: {e}")

        # 2. Aktualizacja bazy (status -> 'left') dla tego kanału
        await SubscriptionManager.update_subscription_status(user_id, channel_id, "left")

        # 3. Info dla admina
        sub = await SubscriptionManager.get_subscription(user_id, channel_id)
        name = html.escape(sub.full_name if sub else "User")

        await callback.message.edit_text(
            f"✅ <b>Odbanowano użytkownika</b>\n\n"
            f"👤 {name}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"📝 Status w bazie: <b>left</b> (może dołączyć ponownie)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Menu główne", callback_data="refresh_channels")
            ]])
        )
        await callback.answer("✅ Użytkownik odbanowany")

    except Exception as e:
        logger.error(f"Błąd unban callback: {e}", exc_info=True)
        await callback.answer(f"❌ Błąd: {e}", show_alert=True)