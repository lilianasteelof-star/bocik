"""
Panel super-admina (tylko ADMIN_ID): Dashboard, Kanały/Użytkownicy, Broadcast, Ochrona, Narzędzia, Eksport.
"""
import io
import json
import logging
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ContentType,
    BufferedInputFile,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from config import settings
from database.models import (
    ChannelManager,
    SubscriptionManager,
    GlobalBlacklist,
    SettingsManager,
    BotUsersManager,
    InboxMuted,
    UserInteractionLog,
)
from utils.states import SuperAdminBroadcast, SuperAdminBlacklist, SuperAdminInbox, SuperAdminChatUser
from utils.scheduler import BotScheduler
from handlers.events import get_pending_join_requests, pop_pending_join_request

logger = logging.getLogger("handlers")
superadmin_router = Router(name="superadmin")

ADMIN_ID = settings.ADMIN_ID
PER_PAGE_CHANNELS = 8
PER_PAGE_USERS = 15
PER_PAGE_BLACKLIST = 15
PER_PAGE_CHAT_USERS = 12


def _is_admin(user_id: int) -> bool:
    return settings.is_superadmin(user_id)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Dashboard", callback_data="superadmin_dashboard")],
        [InlineKeyboardButton(text="📋 Kanały i użytkownicy", callback_data="superadmin_channels_menu")],
        [InlineKeyboardButton(text="💬 Aktywni użytkownicy (chat)", callback_data="superadmin_chat_users")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="superadmin_broadcast")],
        [InlineKeyboardButton(text="📩 Inbox / Wiadomości", callback_data="superadmin_inbox_info")],
        [InlineKeyboardButton(text="🛡️ Ochrona", callback_data="superadmin_protection")],
        [InlineKeyboardButton(text="🔧 Narzędzia", callback_data="superadmin_tools")],
        [InlineKeyboardButton(text="📜 Konsolka (logi)", callback_data="superadmin_console")],
        [InlineKeyboardButton(text="⚠️ Strefa niebezpieczna", callback_data="superadmin_danger")],
    ])


@superadmin_router.message(Command("superadmin"))
async def cmd_superadmin(message: Message):
    """Wejście do panelu super-admina – tylko ADMIN_ID."""
    if not _is_admin(message.from_user.id):
        await message.reply("🚫 Brak dostępu.")
        return
    await message.reply(
        "🔐 **Panel Super-Admina**\n\nWybierz sekcję:",
        reply_markup=_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@superadmin_router.callback_query(F.data == "superadmin_panel")
async def callback_superadmin_panel(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔐 **Panel Super-Admina**\n\nWybierz sekcję:",
        reply_markup=_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


# ---------- Dashboard ----------
@superadmin_router.callback_query(F.data == "superadmin_dashboard")
async def superadmin_dashboard(callback: CallbackQuery, scheduler: BotScheduler):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        channels_count = await ChannelManager.count_all_channels()
        channels_premium = await ChannelManager.count_all_channels("premium")
        channels_free = await ChannelManager.count_all_channels("free")
        subs_count = await SubscriptionManager.count_subscriptions()
        blacklist_count = await GlobalBlacklist.count()
        status = scheduler.get_scheduler_status() if scheduler else {}
        status_text = "✅ Aktywny" if status.get("running") else "❌ Nieaktywny"
        job_count = status.get("job_count", 0)
        text = (
            "📊 **Dashboard Super-Admina**\n\n"
            f"📢 Kanały: **{channels_count}** (Premium: {channels_premium}, Free: {channels_free})\n"
            f"👥 Subskrypcje (łącznie): **{subs_count}**\n"
            f"🚫 Czarna lista: **{blacklist_count}**\n\n"
            f"⏱ Scheduler: {status_text} ({job_count} zadań)"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("superadmin_dashboard: %s", e)
        await callback.answer("Błąd generowania dashboardu", show_alert=True)
    await callback.answer()


# ---------- Kanały i użytkownicy (shady dane) ----------
@superadmin_router.callback_query(F.data == "superadmin_channels_menu")
async def superadmin_channels_menu(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Lista kanałów", callback_data="superadmin_channels_list")],
        [InlineKeyboardButton(text="👥 Lista użytkowników", callback_data="superadmin_users_choice")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
    ])
    await callback.message.edit_text(
        "📋 **Kanały i użytkownicy**\n\nWybierz:",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_channels_list")
async def superadmin_channels_list(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await _render_channels_page(callback, 0, None)


@superadmin_router.callback_query(F.data.startswith("superadmin_channels_filter_"))
async def superadmin_channels_filter(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    part = callback.data.replace("superadmin_channels_filter_", "")
    if part == "all":
        filt = None
    elif part == "premium":
        filt = "premium"
    elif part == "free":
        filt = "free"
    else:
        await callback.answer()
        return
    await _render_channels_page(callback, 0, filt)


@superadmin_router.callback_query(F.data.startswith("superadmin_channels_page_"))
async def superadmin_channels_page(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        parts = callback.data.split("_")
        page = int(parts[-1])
        filt = parts[-2] if len(parts) >= 5 else None
        if filt == "all":
            filt = None
        elif filt not in ("premium", "free"):
            filt = None
        await _render_channels_page(callback, page, filt)
    except (IndexError, ValueError):
        await _render_channels_page(callback, 0, None)
    await callback.answer()


async def _render_channels_page(callback: CallbackQuery, page: int, type_filter: str | None):
    total = await ChannelManager.count_all_channels(type_filter)
    channels = await ChannelManager.get_all_channels(page, PER_PAGE_CHANNELS, type_filter)
    lines = []
    for ch in channels:
        cid = ch.get("channel_id")
        title = (ch.get("title") or "?")[:30]
        typ = ch.get("type") or "?"
        owner = ch.get("owner_id")
        lines.append(f"• **{title}** | {typ} | ID: `{cid}` | owner: `{owner}`")
    text = (
        "📺 **Lista kanałów**\n\n"
        + ("\n".join(lines) if lines else "_Brak kanałów_")
        + f"\n\nStrona {page + 1}/{(max(1, total) + PER_PAGE_CHANNELS - 1) // PER_PAGE_CHANNELS or 1} (łącznie: {total})"
    )
    kb = []
    kb.append([
        InlineKeyboardButton(text="Wszystkie", callback_data="superadmin_channels_filter_all"),
        InlineKeyboardButton(text="Premium", callback_data="superadmin_channels_filter_premium"),
        InlineKeyboardButton(text="Free", callback_data="superadmin_channels_filter_free"),
    ])
    npages = max(1, (total + PER_PAGE_CHANNELS - 1) // PER_PAGE_CHANNELS)
    row = []
    if page > 0:
        suf = f"_{type_filter or 'all'}_{page - 1}"
        row.append(InlineKeyboardButton(text="◀", callback_data="superadmin_channels_page" + suf))
    if page < npages - 1:
        suf = f"_{type_filter or 'all'}_{page + 1}"
        row.append(InlineKeyboardButton(text="▶", callback_data="superadmin_channels_page" + suf))
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="🔙 Wróć", callback_data="superadmin_channels_menu")])
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramBadRequest:
        pass  # Treść i klawiatura bez zmian (np. ponowne kliknięcie tego samego filtra)


@superadmin_router.callback_query(F.data == "superadmin_users_choice")
async def superadmin_users_choice(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    channels = await ChannelManager.get_all_channels(0, 50, None)
    kb = [[InlineKeyboardButton(text="👥 Wszyscy użytkownicy", callback_data="superadmin_users_all_0")]]
    for ch in channels[:20]:
        title = (ch.get("title") or "?")[:25]
        cid = ch["channel_id"]
        kb.append([InlineKeyboardButton(text=f"📢 {title}", callback_data=f"superadmin_users_ch_{cid}_0")])
    kb.append([InlineKeyboardButton(text="🔙 Wróć", callback_data="superadmin_channels_menu")])
    await callback.message.edit_text(
        "👥 **Lista użytkowników**\n\nWybierz kanał lub „Wszyscy”:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data.startswith("superadmin_users_all_"))
async def superadmin_users_all(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        page = int(callback.data.split("_")[-1])
    except (IndexError, ValueError):
        page = 0
    await _render_users_page(callback, page, None)


@superadmin_router.callback_query(F.data.startswith("superadmin_users_ch_"))
async def superadmin_users_ch(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        parts = callback.data.split("_")
        channel_id = int(parts[3])
        page = int(parts[4])
    except (IndexError, ValueError):
        await callback.answer("Błąd", show_alert=True)
        return
    await _render_users_page(callback, page, channel_id)


async def _render_users_page(callback: CallbackQuery, page: int, channel_id: int | None):
    total = await SubscriptionManager.count_subscriptions(channel_id)
    subs = await SubscriptionManager.get_all_subscriptions_paginated(channel_id, page, PER_PAGE_USERS)
    lines = []
    for s in subs:
        uid = s.get("user_id")
        uname = (s.get("username") or "—")[:20]
        fname = (s.get("full_name") or "—")[:20]
        tier = s.get("tier") or "?"
        status = s.get("status") or "?"
        end = s.get("end_date")
        end_str = str(end)[:10] if end else "—"
        lines.append(f"• `{uid}` @{uname} | {fname} | {tier} | {status} | do {end_str}")
    title = "Wszyscy" if channel_id is None else f"Kanał {channel_id}"
    text = (
        f"👥 **Użytkownicy ({title})**\n\n"
        + ("\n".join(lines) if lines else "_Brak_")
        + f"\n\nStrona {page + 1}/{(max(1, total) + PER_PAGE_USERS - 1) // PER_PAGE_USERS or 1} (łącznie: {total})"
    )
    npages = max(1, (total + PER_PAGE_USERS - 1) // PER_PAGE_USERS)
    kb = []
    if page > 0:
        if channel_id is not None:
            kb.append([InlineKeyboardButton(text="◀", callback_data=f"superadmin_users_ch_{channel_id}_{page - 1}")])
        else:
            kb.append([InlineKeyboardButton(text="◀", callback_data=f"superadmin_users_all_{page - 1}")])
    if page < npages - 1:
        if channel_id is not None:
            kb.append([InlineKeyboardButton(text="▶", callback_data=f"superadmin_users_ch_{channel_id}_{page + 1}")])
        else:
            kb.append([InlineKeyboardButton(text="▶", callback_data=f"superadmin_users_all_{page + 1}")])
    kb.append([InlineKeyboardButton(text="🔙 Wróć", callback_data="superadmin_users_choice")])
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramBadRequest:
        pass


# ---------- Aktywni użytkownicy (chat) ----------
@superadmin_router.callback_query(F.data == "superadmin_chat_users")
async def superadmin_chat_users(callback: CallbackQuery):
    """Lista użytkowników z interakcjami z botem (otwarty chat)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await _render_chat_users_page(callback, 0)
    await callback.answer()


@superadmin_router.callback_query(F.data.startswith("superadmin_chat_users_page_"))
async def superadmin_chat_users_page(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        page = int(callback.data.split("_")[-1])
    except (IndexError, ValueError):
        page = 0
    await _render_chat_users_page(callback, page)
    await callback.answer()


def _chat_user_label(u: dict) -> str:
    """Etykieta użytkownika: @username, lub imię, lub ID (max 60 znaków na przycisk)."""
    uid = u.get("user_id")
    username = (u.get("last_username") or "").strip()
    full_name = (u.get("last_full_name") or "").strip()
    if username:
        label = f"@{username}"
    elif full_name:
        label = full_name
    else:
        label = str(uid)
    return (label[:60] + "…") if len(label) > 60 else label


def _html_esc(s: str) -> str:
    """Escape dla HTML (żeby @ i znaki specjalne nie psuły wiadomości)."""
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _render_chat_users_page(callback: CallbackQuery, page: int):
    await BotUsersManager.ensure_user(callback.from_user.id)
    await BotUsersManager.update_user_display_info(
        callback.from_user.id,
        username=callback.from_user.username,
        full_name=(callback.from_user.first_name or "") + " " + (callback.from_user.last_name or "").strip(),
    )
    total = await BotUsersManager.count_users_with_activity()
    users = await BotUsersManager.get_users_with_activity(page, PER_PAGE_CHAT_USERS)
    logger.info("Aktywni użytkownicy: total=%s, page=%s, len(users)=%s", total, page, len(users))

    lines = []
    for u in users:
        uid = u.get("user_id")
        label = _chat_user_label(u)
        last = u.get("last_activity")
        last_str = last.strftime("%Y-%m-%d %H:%M") if hasattr(last, "strftime") else (str(last)[:16] if last else "—")
        lines.append(f"• <b>{_html_esc(label)}</b> <code>{uid}</code> — ostatnia aktywność: {_html_esc(last_str)}")
    npages = max(1, (total + PER_PAGE_CHAT_USERS - 1) // PER_PAGE_CHAT_USERS)
    body = "\n".join(lines) if lines else "<i>Brak użytkowników w bazie (bot_users).</i>"
    text = (
        "💬 <b>Aktywni użytkownicy (chat)</b>\n\n"
        "Użytkownicy, którzy nawiązali kontakt z botem (np. /start).\n\n"
        f"{body}\n\n"
        f"Strona {page + 1}/{npages} (łącznie: {total})"
    )
    kb = []
    for u in users:
        uid = u.get("user_id")
        if uid is None:
            continue
        label = _chat_user_label(u)
        kb.append([InlineKeyboardButton(text=f"👤 {label}", callback_data=f"superadmin_chat_user_{uid}")])
    if page > 0:
        kb.append([InlineKeyboardButton(text="◀", callback_data=f"superadmin_chat_users_page_{page - 1}")])
    if page < npages - 1:
        kb.append([InlineKeyboardButton(text="▶", callback_data=f"superadmin_chat_users_page_{page + 1}")])
    kb.append([InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")])

    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest as e:
        logger.warning("Aktywni użytkownicy: edit_text failed: %s", e)
        try:
            await callback.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e2:
            logger.error("Aktywni użytkownicy: answer failed: %s", e2)


@superadmin_router.callback_query(F.data.startswith("superadmin_chat_user_"))
async def superadmin_chat_user_detail(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """Szczegóły użytkownika: ostatnie 20 logów, blok/odblok, napisz jako bot."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    part = callback.data.replace("superadmin_chat_user_", "")
    if "_" in part:
        action, uid_str = part.split("_", 1)
        try:
            uid = int(uid_str)
        except ValueError:
            await callback.answer("Błąd", show_alert=True)
            return
        if action == "block":
            if settings.is_superadmin(uid):
                await callback.answer("Nie możesz zablokować superadmina.", show_alert=True)
                return
            await GlobalBlacklist.add(uid)
            await callback.answer("Użytkownik zablokowany.", show_alert=True)
            await _render_chat_user_detail(callback, uid)
            return
        if action == "unblock":
            await GlobalBlacklist.remove(uid)
            await callback.answer("Użytkownik odblokowany.", show_alert=True)
            await _render_chat_user_detail(callback, uid)
            return
        if action == "msg":
            await callback.answer()
            await callback.message.answer(
                f"Napisz **wiadomość jako bot** do użytkownika `{uid}` (wyślij tekst).\n\n_/start — anuluj_",
                parse_mode=ParseMode.MARKDOWN,
            )
            await state.set_state(SuperAdminChatUser.waiting_message_to_user)
            await state.update_data(chat_user_target_uid=uid)
            return
        await _render_chat_user_detail(callback, uid)
        await callback.answer()
        return
    try:
        uid = int(part)
    except ValueError:
        await callback.answer("Błąd", show_alert=True)
        return
    await _render_chat_user_detail(callback, uid)
    await callback.answer()


async def _render_chat_user_detail(callback: CallbackQuery, user_id: int):
    display = await BotUsersManager.get_user_display(user_id)
    username = (display.get("last_username") or "").strip() if display else ""
    full_name = (display.get("last_full_name") or "").strip() if display else ""
    user_line = f"@{username}" if username else (full_name or f"ID: {user_id}")
    if username and full_name:
        user_line = f"@{username} — {full_name}"
    elif full_name:
        user_line = full_name

    logs = await UserInteractionLog.get_last_for_user(user_id, 20)
    is_banned = await GlobalBlacklist.is_banned(user_id)
    log_lines = []
    for L in logs:
        created = L.get("created_at")
        ts = created.strftime("%m-%d %H:%M") if hasattr(created, "strftime") else str(created)[:16] if created else "?"
        typ = L.get("event_type") or "?"
        prev = (L.get("content_preview") or "")[:60].replace("\n", " ")
        log_lines.append(f"  {ts} [{typ}] {prev}")
    log_block = "\n".join(log_lines) if log_lines else "  (brak logów)"
    if len(log_block) > 2500:
        log_block = log_block[-2500:]
    status = "🚫 **Zablokowany**" if is_banned else "✅ Aktywny"
    text = (
        f"👤 **Użytkownik** {user_line}\n"
        f"🆔 ID: `{user_id}`\n\n"
        f"Status: {status}\n\n"
        "**Ostatnie 20 logów:**\n```\n" + log_block + "\n```"
    )
    kb = []
    if is_banned:
        kb.append([InlineKeyboardButton(text="✅ Odblokuj", callback_data=f"superadmin_chat_user_unblock_{user_id}")])
    else:
        kb.append([InlineKeyboardButton(text="🚫 Zablokuj", callback_data=f"superadmin_chat_user_block_{user_id}")])
    kb.append([InlineKeyboardButton(text="✉️ Napisz jako bot", callback_data=f"superadmin_chat_user_msg_{user_id}")])
    kb.append([InlineKeyboardButton(text="🔙 Lista", callback_data="superadmin_chat_users")])
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramBadRequest:
        pass


@superadmin_router.message(StateFilter(SuperAdminChatUser.waiting_message_to_user), F.text)
async def superadmin_chat_user_send_message(message: Message, state: FSMContext, bot: Bot):
    """Wysłanie wiadomości jako bot do wybranego użytkownika. /start = anuluj."""
    if not _is_admin(message.from_user.id):
        return
    if message.text and message.text.strip() == "/start":
        await state.clear()
        await message.reply("Anulowano. Wiadomość nie została wysłana.")
        return
    data = await state.get_data()
    uid = data.get("chat_user_target_uid")
    await state.clear()
    if uid is None:
        await message.reply("Sesja wygasła.")
        return
    try:
        await bot.send_message(uid, message.text or "-", parse_mode=ParseMode.MARKDOWN)
        await message.reply(f"✅ Wysłano wiadomość do użytkownika `{uid}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning("chat_user send_message: %s", e)
        await message.reply(f"❌ Nie udało się wysłać: {e}")


# ---------- Ochrona ----------
@superadmin_router.callback_query(F.data == "superadmin_protection")
async def superadmin_protection(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    maintenance = await SettingsManager.get_maintenance_mode()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Czarna lista", callback_data="superadmin_blacklist_list")],
        [InlineKeyboardButton(
            text=f"🔧 Konserwacja: {'WYŁ' if maintenance else 'WŁ'}",
            callback_data="superadmin_maintenance_toggle"
        )],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
    ])
    await callback.message.edit_text(
        "🛡️ **Ochrona**\n\n"
        f"Tryb konserwacji: **{'włączony' if maintenance else 'wyłączony'}**",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_maintenance_toggle")
async def superadmin_maintenance_toggle(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    current = await SettingsManager.get_maintenance_mode()
    await SettingsManager.set_maintenance_mode(not current)
    await callback.answer(f"Konserwacja: {'włączona' if not current else 'wyłączona'}", show_alert=True)
    await superadmin_protection(callback)


@superadmin_router.callback_query(F.data == "superadmin_blacklist_list")
async def superadmin_blacklist_list(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await _render_blacklist_page(callback, 0)


@superadmin_router.callback_query(F.data.startswith("superadmin_blacklist_page_"))
async def superadmin_blacklist_page(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        page = int(callback.data.split("_")[-1])
    except (IndexError, ValueError):
        page = 0
    await _render_blacklist_page(callback, page)
    await callback.answer()


async def _render_blacklist_page(callback: CallbackQuery, page: int):
    total = await GlobalBlacklist.count()
    rows = await GlobalBlacklist.get_all(page, PER_PAGE_BLACKLIST)
    lines = [f"• `{r['user_id']}` — {r.get('reason') or '—'}" for r in rows]
    text = (
        "🚫 **Czarna lista**\n\n"
        + ("\n".join(lines) if lines else "_Pusta_")
        + f"\n\nStrona {page + 1}/{(max(1, total) + PER_PAGE_BLACKLIST - 1) // PER_PAGE_BLACKLIST or 1} (łącznie: {total})"
    )
    npages = max(1, (total + PER_PAGE_BLACKLIST - 1) // PER_PAGE_BLACKLIST)
    kb = []
    if page > 0:
        kb.append([InlineKeyboardButton(text="◀", callback_data=f"superadmin_blacklist_page_{page - 1}")])
    if page < npages - 1:
        kb.append([InlineKeyboardButton(text="▶", callback_data=f"superadmin_blacklist_page_{page + 1}")])
    kb.append([InlineKeyboardButton(text="➕ Dodaj user_id", callback_data="superadmin_blacklist_add")])
    kb.append([InlineKeyboardButton(text="➕ Ban + opuść kanały", callback_data="superadmin_blacklist_add_full")])
    for r in rows:
        uid = r["user_id"]
        kb.append([InlineKeyboardButton(text=f"❌ Usuń {uid}", callback_data=f"superadmin_blacklist_remove_{uid}")])
    kb.append([InlineKeyboardButton(text="🔙 Wróć", callback_data="superadmin_protection")])
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramBadRequest:
        pass


@superadmin_router.callback_query(F.data == "superadmin_blacklist_add")
async def superadmin_blacklist_add_start(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await state.set_state(SuperAdminBlacklist.waiting_user_id)
    await callback.message.edit_text("Podaj **user_id** (liczbę) do dodania do czarnej listy:", parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@superadmin_router.message(StateFilter(SuperAdminBlacklist.waiting_user_id), F.text)
async def superadmin_blacklist_add_apply(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
        if settings.is_superadmin(uid):
            await message.reply("Nie możesz zbanować superadmina.")
            return
        await GlobalBlacklist.add(uid)
        await state.clear()
        await message.reply(f"✅ Dodano `{uid}` do czarnej listy.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.reply("Podaj poprawną liczbę (user_id).")


@superadmin_router.callback_query(F.data == "superadmin_blacklist_add_full")
async def superadmin_blacklist_add_full_start(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await state.set_state(SuperAdminBlacklist.waiting_user_id_full)
    await callback.message.edit_text(
        "Podaj **user_id** (liczbę): użytkownik zostanie dodany do czarnej listy, "
        "a bot opuści wszystkie jego kanały.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.message(StateFilter(SuperAdminBlacklist.waiting_user_id_full), F.text)
async def superadmin_blacklist_add_full_apply(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
        if settings.is_superadmin(uid):
            await message.reply("Nie możesz zbanować superadmina.")
            await state.clear()
            return
        await GlobalBlacklist.add(uid)
        channels = await ChannelManager.get_user_channels(uid)
        left = 0
        for ch in channels:
            try:
                cid = ch.get("channel_id")
                if cid:
                    await bot.leave_chat(cid)
                    left += 1
            except Exception as e:
                logger.warning("leave_chat %s: %s", cid, e)
        await state.clear()
        await message.reply(
            f"✅ Zbanowano `{uid}` i opuszczono **{left}** kanałów.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValueError:
        await message.reply("Podaj poprawną liczbę (user_id).")


@superadmin_router.callback_query(F.data.startswith("superadmin_blacklist_remove_"))
async def superadmin_blacklist_remove(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        uid = int(callback.data.split("_")[-1])
        await GlobalBlacklist.remove(uid)
        await callback.answer("Usunięto z czarnej listy.", show_alert=True)
        await _render_blacklist_page(callback, 0)
    except (ValueError, IndexError):
        await callback.answer("Błąd", show_alert=True)


# ---------- Broadcast (tylko użytkownicy bota) ----------
@superadmin_router.callback_query(F.data == "superadmin_broadcast")
async def superadmin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await state.clear()
    user_ids = await BotUsersManager.get_all_user_ids()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Anuluj", callback_data="superadmin_panel")],
    ])
    await callback.message.edit_text(
        f"📢 **Broadcast**\n\nOdbiorcy: **użytkownicy bota** (wszyscy, którzy kiedykolwiek użyli /start) — **{len(user_ids)}** osób.\n\nWyślij treść wiadomości (tekst lub zdjęcie z podpisem):",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )
    await state.set_state(SuperAdminBroadcast.waiting_message)
    await callback.answer()


@superadmin_router.message(StateFilter(SuperAdminBroadcast.waiting_message), F.content_type.in_({ContentType.TEXT, ContentType.PHOTO}))
async def superadmin_broadcast_message_received(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    if message.content_type == ContentType.PHOTO:
        photo = message.photo[-1]
        caption = message.caption or ""
        await state.update_data(broadcast_photo_file_id=photo.file_id, broadcast_text=caption)
    else:
        await state.update_data(broadcast_photo_file_id=None, broadcast_text=message.text or "")
    await state.set_state(SuperAdminBroadcast.waiting_confirm)
    user_ids = await BotUsersManager.get_all_user_ids()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Wyślij", callback_data="superadmin_bc_confirm_yes")],
        [InlineKeyboardButton(text="❌ Anuluj", callback_data="superadmin_bc_confirm_no")],
    ])
    await message.reply(
        f"Odbiorcy: **użytkownicy bota** ({len(user_ids)} osób). Potwierdź wysyłkę:",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )


@superadmin_router.callback_query(StateFilter(SuperAdminBroadcast.waiting_confirm), F.data == "superadmin_bc_confirm_yes")
async def superadmin_broadcast_confirm_yes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.answer("Wysyłam…")
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    photo_id = data.get("broadcast_photo_file_id")
    user_ids = await BotUsersManager.get_all_user_ids()
    await callback.message.edit_text("Wysyłam…")
    sent = 0
    failed = 0
    import asyncio
    for uid in user_ids:
        try:
            if photo_id:
                await bot.send_photo(uid, photo_id, caption=text or None, parse_mode=ParseMode.MARKDOWN)
            else:
                await bot.send_message(uid, text or "-", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Broadcast zakończony.\nWysłano: **{sent}**, nieudane: **{failed}**",
        parse_mode=ParseMode.MARKDOWN,
    )


@superadmin_router.callback_query(StateFilter(SuperAdminBroadcast.waiting_confirm), F.data == "superadmin_bc_confirm_no")
async def superadmin_broadcast_confirm_no(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("Anulowano.")
    await callback.answer()


# ---------- Inbox (info + obsługa Odpowiedz / Wycisz) ----------
@superadmin_router.callback_query(F.data == "superadmin_inbox_info")
async def superadmin_inbox_info(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.message.edit_text(
        "📩 **Inbox**\n\n"
        "Gdy użytkownik pisze do bota wiadomość (prywatnie, nie komendę), "
        "dostaniesz ją tutaj z przyciskami **Odpowiedz** i **Wycisz**.\n\n"
        "• **Odpowiedz** — napiszesz wiadomość, która zostanie wysłana do tego użytkownika.\n"
        "• **Wycisz** — przestaniesz dostawać powiadomienia od tego użytkownika.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data.startswith("inbox_reply_"))
async def inbox_reply_start(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        uid = int(callback.data.replace("inbox_reply_", ""))
        await state.set_state(SuperAdminInbox.waiting_reply_to_user)
        await state.update_data(inbox_reply_to_uid=uid)
        await callback.message.answer(f"Napisz **odpowiedź** dla użytkownika `{uid}` (wyślij wiadomość):", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await callback.answer("Błąd", show_alert=True)
    await callback.answer()


@superadmin_router.message(StateFilter(SuperAdminInbox.waiting_reply_to_user), F.text)
async def inbox_reply_send(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    if message.text and message.text.strip() == "/start":
        await state.clear()
        await message.reply("Anulowano. Odpowiedź nie została wysłana.")
        return
    data = await state.get_data()
    uid = data.get("inbox_reply_to_uid")
    await state.clear()
    if uid is None:
        await message.reply("Sesja wygasła.")
        return
    try:
        await bot.send_message(uid, message.text or "-")
        await message.reply(f"✅ Wysłano odpowiedź do `{uid}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning("inbox reply send: %s", e)
        await message.reply(f"❌ Nie udało się wysłać: {e}")


@superadmin_router.callback_query(F.data.startswith("inbox_mute_"))
async def inbox_mute(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        uid = int(callback.data.replace("inbox_mute_", ""))
        await InboxMuted.add(uid)
        await callback.answer(f"Wyciszono powiadomienia od użytkownika {uid}.", show_alert=True)
    except ValueError:
        await callback.answer("Błąd", show_alert=True)


# ---------- Konsolka (logi) ----------
@superadmin_router.callback_query(F.data == "superadmin_console")
async def superadmin_console(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        from utils.log_buffer import get_recent_lines
        lines = get_recent_lines(40)
        safe = [(l or "").replace("`", "'")[:200] for l in lines]
        block = "\n".join(safe) if safe else "(brak)"
        text = "📜 **Konsolka (ostatnie logi)**\n\n```\n" + block + "\n```"
        if len(text) > 4000:
            text = "📜 **Konsolka**\n\n```\n" + "\n".join(safe[-35:]) + "\n```"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Odśwież", callback_data="superadmin_console")],
            [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("console: %s", e)
        await callback.message.edit_text(f"❌ Błąd: {e}")
    await callback.answer()


# ---------- Narzędzia ----------
@superadmin_router.callback_query(F.data == "superadmin_tools")
async def superadmin_tools(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Sprawdź wygasłe subskrypcje", callback_data="superadmin_tool_check_expired")],
        [InlineKeyboardButton(text="🔄 SFS autofill", callback_data="superadmin_tool_sfs_autofill")],
        [InlineKeyboardButton(text="🚫 Join requesty (free) — sprawdź / usuń", callback_data="superadmin_join_requests_menu")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
    ])
    await callback.message.edit_text("🔧 **Narzędzia**\n\nWybierz akcję:", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_tool_check_expired")
async def superadmin_tool_check_expired(callback: CallbackQuery, scheduler: BotScheduler):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.answer("Sprawdzam…")
    try:
        await scheduler.check_expired_subscriptions()
        await callback.message.answer("✅ Sprawdzenie wygasłych subskrypcji zakończone.")
    except Exception as e:
        logger.exception("check_expired: %s", e)
        await callback.message.answer(f"❌ Błąd: {e}")


@superadmin_router.callback_query(F.data == "superadmin_tool_sfs_autofill")
async def superadmin_tool_sfs_autofill(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.answer("Uruchamiam SFS autofill…")
    try:
        from handlers.sfs import run_update_sfs_members_count
        await run_update_sfs_members_count(bot)
        await callback.message.answer("✅ SFS autofill zakończony.")
    except Exception as e:
        logger.exception("sfs_autofill: %s", e)
        await callback.message.answer(f"❌ Błąd: {e}")


# ---------- Join requesty (free) — tylko własne kanały superadmina ----------
@superadmin_router.callback_query(F.data == "superadmin_join_requests_menu")
async def superadmin_join_requests_menu(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Sprawdź wszystkie (na moich free)", callback_data="superadmin_join_list")],
        [InlineKeyboardButton(text="🚫 Usuń wszystkie (na wybranym free)", callback_data="superadmin_join_decline_all")],
        [InlineKeyboardButton(text="🔙 Narzędzia", callback_data="superadmin_tools")],
    ])
    await callback.message.edit_text(
        "🚫 **Join requesty (free)**\n\nDziałania tylko na **Twoich** kanałach free.\n\n"
        "• **Sprawdź wszystkie** — lista oczekujących wniosków na każdym Twoim free kanale.\n"
        "• **Usuń wszystkie** — odrzucenie wszystkich wniosków na wybranym free kanale.",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_join_list")
async def superadmin_join_list(callback: CallbackQuery):
    """Sprawdź wszystkie oczekujące join requesty na własnych free kanałach."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    user_id = callback.from_user.id
    channels = await ChannelManager.get_user_channels(user_id)
    free_channels = [ch for ch in channels if ch["type"] == "free"]
    lines = []
    for ch in free_channels:
        cid = ch["channel_id"]
        title = (ch.get("title") or "Kanał")[:40]
        pending = get_pending_join_requests(cid)
        lines.append(f"🆓 **{title}** — {len(pending)} wniosków")
        for r in pending[:10]:
            lines.append(f"  • {r.get('full_name', '—')} (@{r.get('username', '—')}) `{r['user_id']}`")
        if len(pending) > 10:
            lines.append(f"  … i {len(pending) - 10} kolejnych")
    text = "📋 **Oczekujące join requesty (Twoje free kanały)**\n\n" + ("\n".join(lines) if lines else "_Brak free kanałów lub brak wniosków._")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Join requesty", callback_data="superadmin_join_requests_menu")],
        [InlineKeyboardButton(text="🔙 Narzędzia", callback_data="superadmin_tools")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_join_decline_all")
async def superadmin_join_decline_all(callback: CallbackQuery):
    """Wybierz własny free kanał, z którego usunąć wszystkie join requesty."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    user_id = callback.from_user.id
    channels = await ChannelManager.get_user_channels(user_id)
    free_channels = [ch for ch in channels if ch["type"] == "free"]
    if not free_channels:
        await callback.message.edit_text(
            "Brak Twoich kanałów free.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Join requesty", callback_data="superadmin_join_requests_menu")],
            ]),
        )
        await callback.answer()
        return
    keyboard = []
    for ch in free_channels:
        cid = ch["channel_id"]
        pending = get_pending_join_requests(cid)
        label = f"🆓 {ch.get('title', 'Kanał')[:28]} ({len(pending)})"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"superadmin_join_decline_ch_{cid}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Wróć", callback_data="superadmin_join_requests_menu")])
    await callback.message.edit_text(
        "Wybierz kanał free, z którego **odrzucić wszystkie** wnioski o dołączenie:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@superadmin_router.callback_query(F.data.startswith("superadmin_join_decline_ch_"))
async def superadmin_join_decline_channel_all(callback: CallbackQuery, bot: Bot):
    """Odrzuć wszystkie join requesty na wybranym własnym free kanale."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    try:
        channel_id = int(callback.data.replace("superadmin_join_decline_ch_", ""))
    except ValueError:
        await callback.answer("Błąd danych.", show_alert=True)
        return
    user_id = callback.from_user.id
    if not await ChannelManager.is_owner(user_id, channel_id):
        await callback.answer("To nie Twój kanał.", show_alert=True)
        return
    pending = get_pending_join_requests(channel_id)
    if not pending:
        await callback.answer("Brak wniosków na tym kanale.", show_alert=True)
        return
    await callback.answer("Odrzucam…")
    declined = 0
    for r in pending:
        try:
            await bot.decline_chat_join_request(chat_id=channel_id, user_id=r["user_id"])
            pop_pending_join_request(channel_id, r["user_id"])
            declined += 1
        except Exception as e:
            logger.warning("decline_chat_join_request %s %s: %s", channel_id, r["user_id"], e)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Join requesty", callback_data="superadmin_join_requests_menu")],
        [InlineKeyboardButton(text="🔙 Narzędzia", callback_data="superadmin_tools")],
    ])
    await callback.message.edit_text(
        f"✅ Odrzucono **{declined}** wniosków na wybranym kanale.",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- Strefa niebezpieczna (eksport) ----------
@superadmin_router.callback_query(F.data == "superadmin_danger")
async def superadmin_danger(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Eksport: Kanały", callback_data="superadmin_export_channels")],
        [InlineKeyboardButton(text="📥 Eksport: Subskrypcje", callback_data="superadmin_export_subs")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="superadmin_panel")],
    ])
    await callback.message.edit_text(
        "⚠️ **Strefa niebezpieczna**\n\nEksport danych (CSV/JSON):",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@superadmin_router.callback_query(F.data == "superadmin_export_channels")
async def superadmin_export_channels(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.answer("Generuję…")
    try:
        channels = await ChannelManager.get_all_channels(0, 10000, None)
        rows = [{"channel_id": c["channel_id"], "owner_id": c["owner_id"], "title": c.get("title"), "type": c.get("type")} for c in channels]
        buf = io.BytesIO(json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"))
        await callback.message.answer_document(BufferedInputFile(buf.getvalue(), filename="channels_export.json"))
    except Exception as e:
        logger.exception("export channels: %s", e)
        await callback.message.answer(f"❌ Błąd: {e}")


@superadmin_router.callback_query(F.data == "superadmin_export_subs")
async def superadmin_export_subs(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("🚫 Brak dostępu.", show_alert=True)
        return
    await callback.answer("Generuję…")
    try:
        subs = await SubscriptionManager.get_all_subscriptions_paginated(None, 0, 10000)
        rows = []
        for s in subs:
            rows.append({
                "user_id": s.get("user_id"),
                "channel_id": s.get("channel_id"),
                "owner_id": s.get("owner_id"),
                "username": s.get("username"),
                "full_name": s.get("full_name"),
                "tier": s.get("tier"),
                "status": s.get("status"),
                "end_date": str(s.get("end_date")) if s.get("end_date") else None,
            })
        buf = io.BytesIO(json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"))
        await callback.message.answer_document(BufferedInputFile(buf.getvalue(), filename="subscriptions_export.json"))
    except Exception as e:
        logger.exception("export subs: %s", e)
        await callback.message.answer(f"❌ Błąd: {e}")
