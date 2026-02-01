"""
System SFS (Shoutout for Shoutout) – lista ogłoszeń z reputacją (łapki), zgłaszanie i odświeżanie.
Statystyka: subów (members_count). Odświeżenie = podbicie ogłoszenia (max 1/dzień).
Reputacja po owner_id – nie resetuje się przy usunięciu ogłoszenia.
"""
import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, Bot, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext

from database.models import ChannelManager, SFSManager

logger = logging.getLogger("handlers")
sfs_router = Router(name="sfs")

SFS_LIST_PAGE_PREFIX = "sfs_list_page_"
SFS_JOIN_CONFIRM = "sfs_join_confirm"
SFS_LEAVE = "sfs_leave"
PER_PAGE = 10
MIN_SUBS_TO_RATE = 100


def _h(s: str) -> str:
    """Escape dla HTML (treść od użytkownika)."""
    if not s:
        return ""
    return html.escape(str(s), quote=False)


def _format_refreshed_at(dt_str) -> str:
    """Format refreshed_at / created_at jako DD.MM HH:MM."""
    if not dt_str:
        return "—"
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00")[:19])
        else:
            dt = dt_str
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "—"


async def _get_sfs_main_content(user_id: int):
    """Tekst i klawiatura ekranu głównego SFS."""
    count = await SFSManager.count_listings()
    channels = await ChannelManager.get_user_channels(user_id)
    free_channels = [ch for ch in channels if ch.get("type") == "free"]
    listing = await SFSManager.get_listing_by_owner(user_id)

    text = (
        "📢 <b>SFS System</b> (Shoutout for Shoutout)\n\n"
        "Lista użytkowników z kanałami free do wymiany shoutoutów. "
        "Możesz się zgłosić, przeglądać listę i oceniać innych (łapki), jeśli Twój kanał free ma min. 100 subów.\n\n"
        f"<b>Aktualnie w SFS:</b> {count} użytkowników"
    )

    keyboard = []
    keyboard.append([InlineKeyboardButton(text="📋 Lista SFS", callback_data="sfs_list_page_0")])
    if listing:
        keyboard.append([InlineKeyboardButton(text="🔄 Odśwież ogłoszenie (podbicie)", callback_data="sfs_refresh")])
        keyboard.append([InlineKeyboardButton(text="🚪 Usuń z SFS", callback_data=SFS_LEAVE)])
    elif free_channels:
        keyboard.append([InlineKeyboardButton(text="📢 Zgłoś się do SFS", callback_data="sfs_register")])
    keyboard.append([InlineKeyboardButton(text="🔙 Powrót do menu", callback_data="refresh_channels")])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _show_sfs_main(callback: CallbackQuery):
    """Ekran główny SFS: opis, statystyka, przyciski."""
    user_id = callback.from_user.id
    text, keyboard = await _get_sfs_main_content(user_id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@sfs_router.callback_query(F.data == "sfs_start")
async def sfs_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Wejście do SFS – ekran główny. Usuń wiadomości listy (jeśli były)."""
    data = await state.get_data()
    msg_ids = data.get("sfs_list_message_ids") or []
    chat_id = callback.message.chat.id
    current_id = callback.message.message_id
    for mid in msg_ids:
        if mid != current_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass
    await state.update_data(sfs_list_message_ids=[], sfs_list_page=0)
    await _show_sfs_main(callback)
    await callback.answer()


@sfs_router.callback_query(F.data == "sfs_register")
async def sfs_register(callback: CallbackQuery, bot: Bot):
    """Ekran zgłoszenia: dane kanału free, Dołącz / Odśwież (jeśli już w SFS)."""
    user_id = callback.from_user.id
    channels = await ChannelManager.get_user_channels(user_id)
    free_channels = [ch for ch in channels if ch.get("type") == "free"]

    if not free_channels:
        await callback.message.edit_text(
            "❌ Nie masz kanału typu Free. Dodaj kanał Free, żeby móc się zgłosić do SFS.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Powrót", callback_data="sfs_start")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    channel = free_channels[0]
    channel_id = channel["channel_id"]
    channel_title = channel.get("title") or "Kanał"
    username = callback.from_user.username or ""
    if username and not username.startswith("@"):
        username = "@" + username

    members_count = 0
    try:
        members_count = await bot.get_chat_member_count(chat_id=channel_id)
    except Exception as e:
        logger.warning("SFS get_chat_member_count: %s", e)

    existing = await SFSManager.get_listing_by_owner(user_id)
    if existing:
        ref_date = _format_refreshed_at(existing.get("refreshed_at"))
        can_refresh = not await SFSManager.was_refreshed_today(user_id)
        text = (
            "📢 <b>Jesteś już na liście SFS</b>\n\n"
            f"Ostatnie odświeżenie (podbicie) ogłoszenia: {ref_date}. "
            "Ogłoszenie możesz odświeżyć <b>max raz dziennie</b> – wtedy wróci na górę listy.\n\n"
            "Możesz też usunąć się z SFS (Twoja reputacja – łapki – zostanie zachowana)."
        )
        keyboard = []
        if can_refresh:
            keyboard.append([InlineKeyboardButton(text="🔄 Odśwież ogłoszenie (podbicie)", callback_data="sfs_refresh")])
        keyboard.append([InlineKeyboardButton(text="🚪 Usuń z SFS", callback_data=SFS_LEAVE)])
        keyboard.append([InlineKeyboardButton(text="🔙 Powrót do menu SFS", callback_data="sfs_start")])
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    text = (
        "📢 <b>Zgłoszenie do SFS</b>\n\n"
        f"Twój darmowy kanał <b>{_h(channel_title)}</b> ma <b>{members_count}</b> subów.\n\n"
        "Po kliknięciu <b>Dołącz</b> zostaniesz dodany na listę SFS z tą liczbą subów. "
        "Odświeżenie ogłoszenia (max raz dziennie) podbije je na górę listy."
    )
    keyboard = [
        [InlineKeyboardButton(text="✅ Dołącz", callback_data=SFS_JOIN_CONFIRM)],
        [InlineKeyboardButton(text="🔙 Wróć do menu SFS", callback_data="sfs_start")],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@sfs_router.callback_query(F.data == SFS_JOIN_CONFIRM)
async def sfs_join_confirm(callback: CallbackQuery, bot: Bot):
    """Dołącz – tworzenie wpisu SFS z subami, od razu na listę."""
    user_id = callback.from_user.id
    channels = await ChannelManager.get_user_channels(user_id)
    free_channels = [ch for ch in channels if ch.get("type") == "free"]
    if not free_channels:
        await callback.answer("Brak kanału Free.", show_alert=True)
        return

    channel = free_channels[0]
    channel_id = channel["channel_id"]
    channel_title = channel.get("title") or "Kanał"
    username = callback.from_user.username or ""
    if username and not username.startswith("@"):
        username = "@" + username

    members_count = 0
    try:
        members_count = await bot.get_chat_member_count(chat_id=channel_id)
    except Exception as e:
        logger.warning("SFS get_chat_member_count (join): %s", e)

    ok = await SFSManager.create_listing(
        owner_id=user_id,
        channel_id=channel_id,
        username=username,
        channel_title=channel_title,
        avg_views_per_post=0,
        members_count=members_count,
    )
    if not ok:
        await callback.answer("Błąd zapisu. Spróbuj ponownie.", show_alert=True)
        return

    await callback.message.edit_text(
        "✅ <b>Dodano do listy SFS</b>\n\nTwoje ogłoszenie jest na liście (subów: " + str(members_count) + ").",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Lista SFS", callback_data="sfs_list_page_0")],
            [InlineKeyboardButton(text="🔙 Menu SFS", callback_data="sfs_start")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@sfs_router.callback_query(F.data == SFS_LEAVE)
async def sfs_leave(callback: CallbackQuery):
    """Usuń wpis z listy SFS (reputacja użytkownika zostaje)."""
    user_id = callback.from_user.id
    ok = await SFSManager.delete_listing(user_id)
    if ok:
        await callback.answer("Usunięto z SFS. Twoja reputacja (łapki) została zachowana.", show_alert=True)
    else:
        await callback.answer("Nie jesteś na liście SFS.", show_alert=True)
    await _show_sfs_main(callback)


@sfs_router.callback_query(F.data == "sfs_refresh")
async def sfs_refresh(callback: CallbackQuery, bot: Bot):
    """Odświeżenie (podbicie) ogłoszenia – max raz dziennie."""
    user_id = callback.from_user.id
    if await SFSManager.was_refreshed_today(user_id):
        await callback.answer("Możesz odświeżyć ogłoszenie raz dziennie.", show_alert=True)
        return

    listing = await SFSManager.get_listing_by_owner(user_id)
    if not listing:
        await callback.answer("Brak wpisu SFS.", show_alert=True)
        await _show_sfs_main(callback)
        return

    channel_id = listing["channel_id"]
    members_count = listing.get("members_count") or 0
    try:
        members_count = await bot.get_chat_member_count(chat_id=channel_id)
    except Exception as e:
        logger.warning("SFS refresh get_chat_member_count: %s", e)

    now = datetime.now()
    await SFSManager.update_listing_refresh(
        owner_id=user_id,
        refreshed_at=now,
        avg_views_per_post=0,
        members_count=members_count,
    )
    await callback.answer("✅ Ogłoszenie odświeżone (podbicie)!", show_alert=True)
    await _show_sfs_main(callback)


def _format_listing_card(row: dict) -> str:
    """Karta ogłoszenia SFS – czytelny układ z etykietami i odstępami."""
    username = (row.get("username") or "").strip() or "—"
    if username != "—" and not username.startswith("@"):
        username = "@" + username
    username = _h(username)
    channel_title = _h((row.get("channel_title") or "—").strip())
    members_count = row.get("members_count") or 0
    subs_str = str(members_count) if members_count > 0 else "—"
    ref = _format_refreshed_at(row.get("refreshed_at") or row.get("created_at"))
    return (
        f"<b>{username}</b>\n"
        f"📺 {channel_title}\n"
        f"👥 <b>{subs_str}</b> subów  ·  🕐 {ref}"
    )


@sfs_router.callback_query(F.data.startswith(SFS_LIST_PAGE_PREFIX))
async def sfs_list_page(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Lista SFS – każde ogłoszenie osobna wiadomość, max 10 na stronę, paginacja, reputacja po owner_id."""
    try:
        page_str = callback.data.replace(SFS_LIST_PAGE_PREFIX, "").strip()
        page = int(page_str)
    except ValueError:
        page = 0

    total = await SFSManager.get_listings_total()
    if total == 0:
        await callback.message.edit_text(
            "📋 <b>Lista SFS</b>\n\nBrak ogłoszeń. Bądź pierwszy – zgłoś się do SFS!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Powrót do menu SFS", callback_data="sfs_start")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    listings = await SFSManager.get_listings_page(page, PER_PAGE)
    total_pages = (total + PER_PAGE - 1) // PER_PAGE if total else 1
    chat_id = callback.message.chat.id

    # Usuń poprzednie wiadomości listy (jeśli były)
    data = await state.get_data()
    prev_ids = data.get("sfs_list_message_ids") or []
    for mid in prev_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

    sent_ids = []
    # Każde ogłoszenie – osobna wiadomość (karta) z przyciskami reputacji
    for row in listings:
        owner_id = row["owner_id"]
        card_text = _format_listing_card(row)
        thumbs_up = row.get("thumbs_up") or 0
        thumbs_down = row.get("thumbs_down") or 0
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"👍 {thumbs_up}", callback_data=f"sfs_rate_{owner_id}_up"),
                InlineKeyboardButton(text=f"👎 {thumbs_down}", callback_data=f"sfs_rate_{owner_id}_down"),
            ],
        ])
        msg = await bot.send_message(
            chat_id=chat_id,
            text=card_text,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        sent_ids.append(msg.message_id)

    # Wiadomość z paginacją i powrotem
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Poprzednia", callback_data=f"{SFS_LIST_PAGE_PREFIX}{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Następna ▶", callback_data=f"{SFS_LIST_PAGE_PREFIX}{page + 1}"))
    pagination_text = (
        f"📋 <b>Lista SFS</b>\n"
        f"Strona <b>{page + 1}</b> z <b>{total_pages}</b>"
    )
    if nav:
        pagination_kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    else:
        pagination_kb = InlineKeyboardMarkup(inline_keyboard=[])
    pagination_kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Powrót do menu SFS", callback_data="sfs_start")])
    pag_msg = await bot.send_message(
        chat_id=chat_id,
        text=pagination_text,
        reply_markup=pagination_kb,
        parse_mode=ParseMode.HTML,
    )
    sent_ids.append(pag_msg.message_id)

    await state.update_data(sfs_list_message_ids=sent_ids, sfs_list_page=page)
    # Usuń oryginalną wiadomość z przycisku "Lista SFS" (żeby nie duplikować)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@sfs_router.callback_query(F.data.startswith("sfs_rate_"))
async def sfs_rate(callback: CallbackQuery):
    """Ocena (łapka) – po owner_id. Tylko użytkownik z min. 100 subów na kanale free."""
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer()
        return
    try:
        owner_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    direction = parts[3]
    vote = 1 if direction == "up" else -1

    if not await SFSManager.can_user_rate(user_id):
        await callback.answer(
            f"Potrzebujesz min. {MIN_SUBS_TO_RATE} subów na swoim kanale free, żeby oceniać.",
            show_alert=True,
        )
        return

    await SFSManager.set_rating(owner_id, user_id, vote)

    # Odśwież tylko tę wiadomość (reputacja tego użytkownika)
    up, down = await SFSManager.get_rating_counts(owner_id)
    listing = await SFSManager.get_listing_by_owner(owner_id)
    if listing:
        card_text = _format_listing_card(listing)
    else:
        card_text = "—  ·  Ogłoszenie usunięte  ·  reputacja: 👍 {} 👎 {}".format(up, down)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"👍 {up}", callback_data=f"sfs_rate_{owner_id}_up"),
            InlineKeyboardButton(text=f"👎 {down}", callback_data=f"sfs_rate_{owner_id}_down"),
        ],
    ])
    try:
        await callback.message.edit_text(card_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer("✅ Ocena zapisana.")


async def run_update_sfs_members_count(bot: Bot) -> None:
    """Aktualizacja members_count (subów) dla wszystkich wpisów SFS. Wywoływane przez scheduler / komendę."""
    try:
        listings = await SFSManager.get_all_listings()
        if not listings:
            return
        logger.info("SFS: aktualizacja subów dla %d wpisów", len(listings))
        for item in listings:
            owner_id = item.get("owner_id")
            channel_id = item.get("channel_id")
            if owner_id is None or channel_id is None:
                continue
            try:
                members_count = await bot.get_chat_member_count(chat_id=channel_id)
                if members_count >= 0:
                    await SFSManager.update_listing_members_count(owner_id, members_count)
            except Exception as e:
                logger.debug("SFS get_chat_member_count channel_id=%s: %s", channel_id, e)
            await asyncio.sleep(2)
    except Exception as e:
        logger.warning("SFS run_update_sfs_members_count: %s", e)
