"""
Inbox: przekazywanie do super-admina wiadomości od użytkowników (prywatne, tekst, nie komenda).
Router powinien być rejestrowany NA KOŃCU, żeby łapać tylko wiadomości nieobsłużone przez inne handlery.
"""
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import BaseFilter

from config import settings
from database.models import InboxMuted

logger = logging.getLogger("handlers")
inbox_router = Router(name="inbox")

ADMIN_ID = settings.ADMIN_ID


def _escape_html(s: str) -> str:
    """Escapuje znaki HTML (treść użytkownika)."""
    if not s:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class NotCommandFilter(BaseFilter):
    """Wiadomość ma tekst i nie jest komendą (nie zaczyna się od /)."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return not message.text.strip().startswith("/")


@inbox_router.message(F.chat.type == "private", F.text, NotCommandFilter())
async def inbox_forward_to_admin(message: Message, bot: Bot):
    """
    Łapie prywatne wiadomości tekstowe, które nie są komendą (żaden wcześniejszy handler ich nie obsłużył).
    Przekazuje do admina z przyciskami Odpowiedz / Wycisz (jeśli user nie jest wyciszony).
    """
    if not message.text or not message.from_user:
        return
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        return
    if await InboxMuted.is_muted(user_id):
        return
    username = _escape_html((message.from_user.username or "—")[:30])
    full_name = _escape_html((message.from_user.full_name or "—")[:50])
    text_preview = _escape_html((message.text or "")[:300])
    if len(message.text or "") > 300:
        text_preview += "..."
    admin_text = (
        "📩 <b>Wiadomość od użytkownika</b>\n\n"
        f"👤 user_id: <code>{user_id}</code>\n"
        f"📛 @{username} | {full_name}\n\n"
        f"💬 {text_preview}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Odpowiedz", callback_data=f"inbox_reply_{user_id}")],
        [InlineKeyboardButton(text="🔇 Wycisz powiadomienia", callback_data=f"inbox_mute_{user_id}")],
    ])
    try:
        await bot.send_message(
            ADMIN_ID,
            admin_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("inbox forward to admin: %s", e)
