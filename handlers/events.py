"""
Obsługa zdarzeń ChatMemberUpdated - dołączanie i wychodzenie użytkowników
POPRAWIONA WERSJA 2: Fix 'BANNED' attribute error + logic split
"""
import asyncio
import logging
import html
import time
from aiogram import Router, Bot
from aiogram.types import ChatMemberUpdated, ChatJoinRequest
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.fsm.context import FSMContext

from config import settings
from database.connection import db_manager
from utils.states import SubscriptionManagement
from utils.helpers import (
    create_tier_keyboard
)

logger = logging.getLogger("handlers")
events_router = Router()

# --- Anti-spam: throttle powiadomień o leadach (żeby nie zalać API i nie dostać bana) ---
_lead_notify_lock = asyncio.Lock()
_lead_notify_last_ts: dict[int, float] = {}  # owner_id -> ostatni wysłany timestamp
_lead_notify_timestamps: dict[int, list[float]] = {}  # owner_id -> lista czasów w ostatnich 60s
_LEAD_MIN_INTERVAL = 2.0  # min. sekund między powiadomieniami do tego samego ownera
_LEAD_MAX_PER_MINUTE = 25  # max powiadomień o leadzie na ownera na minutę

# --- Oczekujące join requesty (tylko free) — do odrzucenia z menu ---
# channel_id -> lista dictów {"user_id": int, "username": str, "full_name": str}
_pending_join_requests: dict[int, list[dict]] = {}


def get_pending_join_requests(channel_id: int) -> list[dict]:
    """Zwraca listę oczekujących join requestów dla kanału (kopia)."""
    return list(_pending_join_requests.get(channel_id, []))


def pop_pending_join_request(channel_id: int, user_id: int) -> bool:
    """Usuwa pierwszy pasujący join request (channel_id, user_id). Zwraca True jeśli usunięto."""
    lst = _pending_join_requests.get(channel_id, [])
    for i, r in enumerate(lst):
        if r["user_id"] == user_id:
            lst.pop(i)
            if not lst:
                _pending_join_requests.pop(channel_id, None)
            return True
    return False


@events_router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest, bot: Bot):
    """Zapisuje join request dla kanałów free, żeby właściciel mógł odrzucić z menu."""
    try:
        chat_id = event.chat.id
        user = event.from_user
        user_id = user.id
        username = (user.username or "—")[:32]
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"

        connection = await db_manager.get_connection()
        async with connection.execute(
            "SELECT owner_id, type FROM channels WHERE channel_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row["type"] != "free":
            return
        _pending_join_requests.setdefault(chat_id, []).append({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
        })
        logger.info("Join request (free) zapisany: channel=%s user_id=%s", chat_id, user_id)
    except Exception as e:
        logger.error("handle_chat_join_request: %s", e, exc_info=True)


@events_router.chat_member()
async def handle_chat_member_update(event: ChatMemberUpdated, bot: Bot, state: FSMContext):
    """
    Główny handler dla zmian statusu członków (Join/Leave/Ban)
    Multi-user compatible.
    """
    try:
        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status

        # --- DEFINICJE ZDARZEŃ ---
        is_joining = (
            new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR] and
            old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED]
        )

        is_leaving = (
            old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR] and
            new_status == ChatMemberStatus.LEFT
        )

        is_banned = new_status == ChatMemberStatus.KICKED

        # --- DANE UŻYTKOWNIKA ---
        user = event.new_chat_member.user
        user_id = user.id
        username = user.username or "brak"
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        safe_full_name = html.escape(full_name)
        safe_username = html.escape(username)

        chat_id = event.chat.id
        
        # --- IDENTYFIKACJA WŁAŚCICIELA ---
        from database.models import ChannelManager, SettingsManager
        
        # Find who owns this channel
        # We need a method to get owner by channel_id, or we can assume it's one of the configured channels
        # For efficiency, we might want to cache this or query quickly
        # But wait, ChannelManager doesn't have get_owner_by_channel(chat_id) yet?
        # Let's add it or do a raw query here for now (or assume it's premium/free registered)
        
        # Let's try to find the channel in DB
        connection = await db_manager.get_connection()
        async with connection.execute("SELECT owner_id, type FROM channels WHERE channel_id = ?", (chat_id,)) as cursor:
            channel_row = await cursor.fetchone()
            
        if not channel_row:
            # Kanał nie jest zarejestrowany w systemie -> ignorujemy
            return

        owner_id = channel_row['owner_id']
        channel_type = channel_row['type']

        # --- OBSŁUGA DOŁĄCZENIA ---
        if is_joining:
            logger.info(f"🟢 User JOINED: {user_id} do kanału {chat_id} (Owner: {owner_id}, Type: {channel_type})")

            # Premium Channel Join
            if channel_type == 'premium':
                await handle_premium_channel_join(bot, user_id, safe_username, safe_full_name, owner_id, chat_id)

            # Free Channel (Watchdog)
            elif channel_type == 'free':
                from database.models import SubscriptionManager
                from datetime import datetime

                # Zapisujemy leada w bazie (tier='free', end_date=9999)
                # Dzięki temu mamy created_at i możemy robić statystyki
                await SubscriptionManager.create_subscription(
                    user_id=user_id,
                    owner_id=owner_id,
                    channel_id=chat_id,
                    username=username,
                    full_name=full_name,
                    tier='free',
                    end_date=datetime(9999, 12, 31)
                )

                await handle_free_channel_join(bot, user_id, safe_username, safe_full_name, owner_id)

        # --- OBSŁUGA OPUSZCZENIA (AUTO-REMOVE) ---
        elif is_leaving:
            logger.info(f"🔴 User LEFT: {user_id} z kanału {chat_id} (Owner: {owner_id}, Type: {channel_type})")

            if channel_type == 'premium':
                from database.models import SubscriptionManager

                # Check subscription specific to this channel
                subscription = await SubscriptionManager.get_subscription(user_id, chat_id)
                if subscription:
                    await SubscriptionManager.update_subscription_status(user_id, chat_id, "left")
                    msg_text = (
                        f"👋 <b>Użytkownik opuścił Twój kanał Premium</b>\n\n"
                        f"👤 <a href='tg://user?id={user_id}'>{safe_full_name}</a>\n"
                        f"🏷️ User: @{safe_username}\n"
                        f"💎 Tier: {subscription.tier}\n"
                        f"ℹ️ <b>Status zmieniony na 'left'</b>"
                    )
                else:
                    msg_text = (
                        f"👋 <b>Użytkownik opuścił Twój kanał Premium</b>\n\n"
                        f"👤 <a href='tg://user?id={user_id}'>{safe_full_name}</a>\n"
                        f"ℹ️ Nie miał aktywnej subskrypcji w tym kanale."
                    )

                try:
                    await bot.send_message(
                        chat_id=owner_id,
                        text=msg_text,
                        parse_mode=ParseMode.HTML,
                        disable_notification=True
                    )
                except Exception as e:
                    logger.warning(f"Could not notify owner {owner_id}: {e}")

        # --- OBSŁUGA BANA ---
        elif is_banned:
            logger.info(f"🚫 User BANNED: {user_id} z kanału {chat_id} (Owner: {owner_id})")

            if channel_type == 'premium':
                from database.models import SubscriptionManager

                # Aktualizacja statusu w bazie (powiadomienie ownerowi tylko z schedulera – „Auto-Ban: Użytkownik usunięty…”, bez duplikatu)
                if await SubscriptionManager.get_subscription(user_id, chat_id):
                    await SubscriptionManager.update_subscription_status(user_id, chat_id, "banned")

    except Exception as e:
        logger.error(f"Błąd obsługi chat member update: {e}", exc_info=True)


async def handle_premium_channel_join(
    bot: Bot,
    user_id: int,
    username: str,
    full_name: str,
    owner_id: int,
    channel_id: int
):
    """
    Obsługa dołączenia do Premium Channel
    Wysyła panel sterowania do Właściciela (Admina)
    """
    try:
        notification_text = (
            f"👋 <b>Nowy użytkownik na Premium!</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{full_name}</a>\n"
            f"🏷️ Username: @{username}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"⚙️ <b>Wybierz kategorię subskrypcji:</b>"
        )

        await bot.send_message(
            chat_id=owner_id,
            text=notification_text,
            reply_markup=create_tier_keyboard(user_id, channel_id),
            parse_mode=ParseMode.HTML
        )

        logger.info(f"Wysłano powiadomienie o dołączeniu do właściciela {owner_id}")

    except Exception as e:
        logger.error(f"Błąd obsługi Premium join: {e}", exc_info=True)


async def handle_free_channel_join(
    bot: Bot,
    user_id: int,
    username: str,
    full_name: str,
    owner_id: int
):
    """Obsługa dołączenia do Free Channel (Watchdog). Z throttle'em, żeby nie zalać API."""
    try:
        async with _lead_notify_lock:
            now = time.time()
            # Ograniczenie: max N powiadomień na minutę na ownera
            if owner_id not in _lead_notify_timestamps:
                _lead_notify_timestamps[owner_id] = []
            # Usuń stare wpisy (starsze niż 60s)
            _lead_notify_timestamps[owner_id] = [
                t for t in _lead_notify_timestamps[owner_id] if t > now - 60
            ]
            if len(_lead_notify_timestamps[owner_id]) >= _LEAD_MAX_PER_MINUTE:
                logger.warning(
                    f"Lead notification skipped (rate limit): owner_id={owner_id}, "
                    f"max {_LEAD_MAX_PER_MINUTE}/min"
                )
                return
            # Odstęp min. _LEAD_MIN_INTERVAL sekund od ostatniego wysłania do tego ownera
            last = _lead_notify_last_ts.get(owner_id, 0)
            if now - last < _LEAD_MIN_INTERVAL:
                await asyncio.sleep(_LEAD_MIN_INTERVAL - (now - last))
                now = time.time()
            _lead_notify_last_ts[owner_id] = now
            _lead_notify_timestamps[owner_id].append(now)

        # Zawsze link tg://user?id=... (działa w Telegramie); nazwa i @ w jednym linku
        user_link = f"tg://user?id={user_id}"
        if username and username != "brak":
            username_display = f"<a href=\"{user_link}\">@{username}</a>"
        else:
            username_display = f"<a href=\"{user_link}\">Napisz do leada</a>"
        notification_text = (
            f"🔔 <b>Nowy lead</b> (Free Channel)\n\n"
            f"👤 <a href='{user_link}'>{full_name}</a>\n"
            f"🏷️ {username_display}\n\n"
            f"💬 <i>Pisz, póki ciepły.</i>"
        )

        await bot.send_message(
            chat_id=owner_id,
            text=notification_text,
            parse_mode=ParseMode.HTML,
            disable_notification=True
        )

    except Exception as e:
        logger.error(f"Błąd obsługi Free join: {e}", exc_info=True)


@events_router.my_chat_member()
async def on_bot_added_to_channel(event: ChatMemberUpdated):
    """Bot został dodany do kanału lub usunięty"""
    try:
        new_status = event.new_chat_member.status
        from database.models import ChannelManager

        # Bot został administratorem
        if new_status == ChatMemberStatus.ADMINISTRATOR:
            chat = event.chat
            owner_id = event.from_user.id

            if chat.type == "channel":
                # Domyślna rejestracja
                success = await ChannelManager.create_channel(
                    owner_id=owner_id,
                    channel_id=chat.id,
                    title=chat.title,
                    type="premium" # Default to premium, user can change later
                )

                if success:
                    try:
                        await event.bot.send_message(
                            chat_id=owner_id,
                            text=f"✅ **Pomyślnie połączono kanał!**\n\n📢 {chat.title}\n\nWpisz /start aby nim zarządzać."
                        )
                    except:
                        pass
    except Exception as e:
        logger.error(f"Błąd on_bot_added_to_channel: {e}")