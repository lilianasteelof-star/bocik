"""
Planowanie postów – wybór kanału, treść, przyciski, data publikacji.
Limit zaplanowanych postów jest konfigurowalny per użytkownik (domyślnie 10).
"""
import json
import logging
from datetime import datetime

from aiogram import Router, Bot, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ContentType,
)
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
import html

from database.models import (
    ChannelManager,
    PostManager,
    SettingsManager,
)
from utils.states import PostPlanning
from utils.helpers import (
    parse_buttons_text,
    parse_datetime_from_text,
    create_inline_keyboard_from_buttons,
)
from handlers.admin_posts import send_post_to_channel


def _schedule_keyboard() -> InlineKeyboardMarkup:
    """Klawiatura planowania (callbacki pp_* żeby nie kolidować z /newpost)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Wyślij teraz", callback_data=CB_SCHEDULE_NOW)],
        [InlineKeyboardButton(text="⏰ Zaplanuj na później", callback_data=CB_SCHEDULE_LATER)],
        [InlineKeyboardButton(text="❌ Anuluj", callback_data=CB_SCHEDULE_CANCEL)],
    ])


def _buttons_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Dodaj przyciski", callback_data=CB_BUTTONS_ADD)],
        [InlineKeyboardButton(text="➡️ Pomiń przyciski", callback_data=CB_BUTTONS_SKIP)],
        [InlineKeyboardButton(text="❌ Anuluj", callback_data=CB_BUTTONS_CANCEL)],
    ])

logger = logging.getLogger("handlers")
post_planning_router = Router(name="post_planning")

# Callback prefixy
CB_CHANNEL = "pp_ch_"
CB_BACK = "pp_back"
CB_BUTTONS_ADD = "pp_btn_add"
CB_BUTTONS_SKIP = "pp_btn_skip"
CB_BUTTONS_CANCEL = "pp_btn_cancel"
CB_SCHEDULE_NOW = "pp_sched_now"
CB_SCHEDULE_LATER = "pp_sched_later"
CB_SCHEDULE_CANCEL = "pp_sched_cancel"
CB_LIST = "pp_list"
CB_NEW_POST = "pp_new_post"
CB_LIST_PAGE = "pp_list_page_"
CB_DELETE = "pp_del_"
POSTS_PER_PAGE = 5


def _h(s: str) -> str:
    """Escape dla HTML (treść od użytkownika)."""
    if not s:
        return ""
    return html.escape(str(s), quote=False)


def _keyboard_back_to_channels() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Wstecz", callback_data=CB_BACK)]
    ])


def _keyboard_back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Do menu", callback_data="refresh_channels")]
    ])


# ——— Wejście: menu planera (Nowy post / Zaplanowane posty) ———

async def _show_planer_menu(callback: CallbackQuery):
    """Pokazuje menu planera: Nowy post, Zaplanowane posty, Menu."""
    keyboard = [
        [InlineKeyboardButton(text="➕ Nowy post", callback_data=CB_NEW_POST)],
        [InlineKeyboardButton(text="📋 Zaplanowane posty", callback_data=CB_LIST)],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="refresh_channels")],
    ]
    await callback.message.edit_text(
        "📅 <b>Planer postów</b>\n\n"
        "Tu zaplanujesz publikacje na wybrany kanał. Limit to max postów <b>w kolejce jednocześnie</b> — po publikacji lub usunięciu miejsce się zwalnia.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML,
    )


@post_planning_router.callback_query(F.data == "post_planning_start")
async def post_planning_start(callback: CallbackQuery, state: FSMContext):
    """Wejście do planera – menu z wyborem: Nowy post / Zaplanowane posty."""
    await state.clear()
    await _show_planer_menu(callback)
    await callback.answer()


@post_planning_router.callback_query(F.data == CB_NEW_POST)
async def post_planning_new_post(callback: CallbackQuery, state: FSMContext):
    """Nowy post – wybór kanału."""
    user_id = callback.from_user.id
    channels = await ChannelManager.get_user_channels(user_id)
    if not channels:
        await callback.message.edit_text(
            "❌ Nie masz żadnych kanałów. Najpierw dodaj kanał (/start → Dodaj kanał).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Planer postów", callback_data="post_planning_start")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    keyboard = []
    for ch in channels:
        emoji = "💎" if ch["type"] == "premium" else "🆓"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {ch['title'].upper()}",
                callback_data=f"{CB_CHANNEL}{ch['channel_id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton(text="« Wstecz", callback_data=CB_BACK)])

    await callback.message.edit_text(
        "📅 <b>Planer postów</b> → Nowy post\n\n"
        "Wybierz kanał, na którym chcesz utworzyć post.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PostPlanning.choosing_channel)
    await callback.answer()


@post_planning_router.callback_query(F.data == CB_BACK, PostPlanning.choosing_channel)
async def post_planning_back_to_planer(callback: CallbackQuery, state: FSMContext):
    """Powrót z wyboru kanału do menu planera."""
    await state.clear()
    await _show_planer_menu(callback)
    await callback.answer()


@post_planning_router.callback_query(F.data.startswith(CB_CHANNEL), PostPlanning.choosing_channel)
async def post_planning_channel_selected(callback: CallbackQuery, state: FSMContext):
    """Wybrano kanał – prośba o treść posta."""
    try:
        # Telegram channel_id jest ujemny (np. -1001234567890) – wymuszamy int
        channel_id = int(callback.data.replace(CB_CHANNEL, "").strip())
        user_id = callback.from_user.id
        if not await ChannelManager.is_owner(user_id, channel_id):
            await callback.answer("❌ To nie Twój kanał.", show_alert=True)
            return

        channel = await ChannelManager.get_channel(channel_id)
        title = channel["title"] if channel else str(channel_id)

        await state.update_data(
            planning_channel_id=channel_id,
            planning_channel_title=title,
        )
        await state.set_state(PostPlanning.waiting_content)

        await callback.message.edit_text(
            f"✅ Kanał: <b>{_h(title)}</b>\n\n"
            "Wyślij treść posta: tekst, zdjęcie, wideo lub sticker. "
            "Możesz wysłać jedną wiadomość lub kilka.",
            reply_markup=_keyboard_back_to_channels(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"post_planning channel selected: {e}")
        await callback.answer("Błąd.", show_alert=True)


# ——— Treść posta ———

@post_planning_router.message(PostPlanning.waiting_content, F.content_type.in_({
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.DOCUMENT,
    ContentType.STICKER,
}))
async def post_planning_content_received(message: Message, state: FSMContext):
    """Odebrano treść – zapis i pytanie o przyciski."""
    try:
        content_data = {}
        if message.text:
            content_data = {
                "content_type": "text",
                "content": message.text,
                "caption": None,
            }
        elif message.photo:
            photo = message.photo[-1]
            content_data = {
                "content_type": "photo",
                "content": photo.file_id,
                "caption": message.caption,
            }
        elif message.video:
            content_data = {
                "content_type": "video",
                "content": message.video.file_id,
                "caption": message.caption,
            }
        elif message.document:
            content_data = {
                "content_type": "document",
                "content": message.document.file_id,
                "caption": message.caption,
            }
        elif message.sticker:
            content_data = {
                "content_type": "sticker",
                "content": message.sticker.file_id,
                "caption": None,
            }
        else:
            await message.reply(
                "❌ Nieobsługiwany typ. Wyślij tekst, zdjęcie, wideo, dokument lub sticker."
            )
            return

        await state.update_data(**content_data)
        await state.set_state(PostPlanning.waiting_buttons)

        await message.reply(
            "✅ Treść zapisana.\n\n"
            "🔘 Chcesz dodać przyciski (URL) do posta?",
            reply_markup=_buttons_keyboard(),
        )
    except Exception as e:
        logger.error(f"post_planning content: {e}")
        await message.reply("❌ Błąd zapisu treści.")


@post_planning_router.message(PostPlanning.waiting_content)
async def post_planning_content_invalid(message: Message):
    await message.reply(
        "Wyślij treść posta: <b>tekst</b>, <b>zdjęcie</b>, <b>wideo</b>, <b>dokument</b> lub <b>sticker</b>.",
        parse_mode=ParseMode.HTML,
    )


# ——— Przyciski ———

@post_planning_router.callback_query(F.data == CB_BUTTONS_ADD, PostPlanning.waiting_buttons)
async def post_planning_buttons_add(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔘 <b>Dodawanie przycisków</b>\n\n"
        "Wyślij przyciski w formacie:\n"
        "<code>Tekst - Link</code>\n"
        "<code>Inny - https://example.com</code>\n\n"
        "Każdy przycisk w nowej linii.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@post_planning_router.callback_query(F.data == CB_BUTTONS_SKIP, PostPlanning.waiting_buttons)
async def post_planning_buttons_skip(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "⏰ <b>Planowanie publikacji</b>\n\n"
        "Kiedy opublikować post?",
        reply_markup=_schedule_keyboard(),
    )
    await state.set_state(PostPlanning.waiting_schedule)
    await callback.answer()


@post_planning_router.callback_query(F.data == CB_BUTTONS_CANCEL, PostPlanning.waiting_buttons)
async def post_planning_buttons_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Anulowano. Możesz zacząć od nowa z Planera postów.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Do menu", callback_data="refresh_channels")],
        ]),
    )
    await callback.answer()


@post_planning_router.message(PostPlanning.waiting_buttons)
async def post_planning_buttons_text(message: Message, state: FSMContext):
    if not message.text:
        await message.reply("Wyślij tekst z przyciskami w formacie: Tekst - Link")
        return
    buttons = parse_buttons_text(message.text)
    if not buttons:
        await message.reply(
            "❌ Nie znaleziono prawidłowych przycisków. Format: <code>Tekst - Link</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await state.update_data(buttons=buttons)
    buttons_preview = "\n".join([f"• {_h(b['text'])} → {_h(b['url'])}" for b in buttons])
    await message.reply(
        f"✅ <b>Przyciski dodane:</b>\n\n{buttons_preview}\n\n"
        "⏰ Kiedy opublikować post?",
        reply_markup=_schedule_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(PostPlanning.waiting_schedule)


# ——— Harmonogram i zapis ———

@post_planning_router.callback_query(F.data == CB_SCHEDULE_NOW, PostPlanning.waiting_schedule)
async def post_planning_send_now(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Wysłanie posta natychmiast."""
    try:
        data = await state.get_data()
        user_id = callback.from_user.id
        channel_id = data.get("planning_channel_id")
        post_data = {
            "content_type": data["content_type"],
            "content": data["content"],
            "caption": data.get("caption"),
            "buttons": data.get("buttons"),
        }
        success = await send_post_to_channel(bot, post_data, user_id, channel_id=channel_id)
        if success:
            await callback.message.edit_text(
                "✅ <b>Post opublikowany.</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🔙 Dashboard", callback_data="refresh_channels"),
                        InlineKeyboardButton(text="➕ Zaplanuj kolejny", callback_data="post_planning_start"),
                    ],
                ]),
                parse_mode=ParseMode.HTML,
            )
        else:
            await callback.message.edit_text("❌ Błąd publikacji. Sprawdź uprawnienia bota na kanale.")
        await state.clear()
        await callback.answer()
    except Exception as e:
        logger.error(f"post_planning send now: {e}")
        await callback.answer("Błąd.", show_alert=True)


@post_planning_router.callback_query(F.data == CB_SCHEDULE_CANCEL, PostPlanning.waiting_schedule)
async def post_planning_schedule_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Anulowano. Możesz zaplanować inny post z Planera.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Do menu", callback_data="refresh_channels")],
            [InlineKeyboardButton(text="📅 Planer postów", callback_data="post_planning_start")],
        ]),
    )
    await callback.answer()


@post_planning_router.callback_query(F.data == CB_SCHEDULE_LATER, PostPlanning.waiting_schedule)
async def post_planning_schedule_later(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📅 <b>Data i godzina publikacji</b>\n\n"
        "Wyślij datę i czas w formacie:\n"
        "<code>DD.MM.YYYY HH:MM</code> lub <code>YYYY-MM-DD HH:MM</code>\n\n"
        "Przykład: <code>15.02.2026 14:30</code>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@post_planning_router.message(PostPlanning.waiting_schedule)
async def post_planning_schedule_date(message: Message, state: FSMContext):
    if not message.text:
        return
    publish_date = parse_datetime_from_text(message.text)
    if not publish_date:
        await message.reply(
            "❌ Nieprawidłowy format daty. Użyj np. `DD.MM.YYYY HH:MM` lub `YYYY-MM-DD HH:MM`"
        )
        return

    user_id = message.from_user.id
    data = await state.get_data()
    channel_id = data.get("planning_channel_id")
    max_posts = await SettingsManager.get_max_scheduled_posts(user_id)
    current_count = await PostManager.count_pending_posts(user_id)

    if current_count >= max_posts:
        await message.reply(
            f"❌ Masz już maksymalną liczbę postów w kolejce ({max_posts}).\n\n"
            "Limit to liczba postów zaplanowanych <b>jednocześnie</b>. "
            "Usuń któryś z listy lub poczekaj, aż się opublikuje – wtedy zwolni się miejsce.",
            parse_mode=ParseMode.HTML,
        )
        return

    post_id = await PostManager.create_scheduled_post(
        owner_id=user_id,
        channel_id=int(channel_id),
        content_type=data["content_type"],
        content=data["content"],
        publish_date=publish_date,
        caption=data.get("caption"),
        buttons=data.get("buttons"),
    )
    if post_id:
        await message.reply(
            f"✅ <b>Post zaplanowany</b>\n\n"
            f"📅 Publikacja: {publish_date.strftime('%d.%m.%Y %H:%M')}\n"
            f"📝 Typ: {data['content_type']}\n\n"
            f"W kolejce: <b>{current_count + 1} / {max_posts}</b> postów <i>(limit = max jednocześnie; po publikacji lub usunięciu miejsce się zwalnia)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔙 Dashboard", callback_data="refresh_channels"),
                    InlineKeyboardButton(text="➕ Zaplanuj kolejny", callback_data="post_planning_start"),
                ],
            ]),
        )
    else:
        await message.reply("❌ Błąd zapisu zaplanowanego posta.")
    await state.clear()


# ——— Lista zaplanowanych i ustawienia ———

@post_planning_router.callback_query(F.data == CB_LIST)
async def post_planning_list(callback: CallbackQuery, state: FSMContext):
    """Lista zaplanowanych postów z paginacją."""
    await state.clear()
    user_id = callback.from_user.id
    posts = await PostManager.get_scheduled_posts(user_id)
    max_posts = await SettingsManager.get_max_scheduled_posts(user_id)

    if not posts:
        await callback.message.edit_text(
            "📋 <b>Zaplanowane posty</b>\n\n"
            "Brak postów w kolejce.\n\n"
            f"<i>Limit: do <b>{max_posts}</b> postów jednocześnie. Po publikacji lub usunięciu miejsce się zwalnia.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Nowy post", callback_data=CB_NEW_POST)],
                [InlineKeyboardButton(text="🔙 Planer postów", callback_data="post_planning_start")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    page = 0
    total_pages = (len(posts) + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    chunk = posts[page * POSTS_PER_PAGE : (page + 1) * POSTS_PER_PAGE]
    type_lbl = {"photo": "Zdjęcie", "video": "Wideo", "document": "Dokument", "sticker": "Sticker", "text": "Tekst"}
    lines = []
    for p in chunk:
        if p.content_type == "text" and p.content:
            preview = (p.content[:50] + "…") if len(p.content) > 50 else p.content
        elif p.caption:
            preview = (p.caption[:50] + "…") if len(p.caption) > 50 else p.caption
        else:
            preview = type_lbl.get(p.content_type, p.content_type)
        lines.append(f"• <b>{p.publish_date.strftime('%d.%m %H:%M')}</b> — {_h(preview)}")
    text = (
        f"📋 <b>Zaplanowane posty</b> — w kolejce: <b>{len(posts)} / {max_posts}</b>\n\n"
        + "\n".join(lines)
    )
    keyboard = []
    for p in chunk:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🗑 Usuń",
                callback_data=f"{CB_DELETE}{p.post_id}",
            )
        ])
    keyboard.append([
        InlineKeyboardButton(text="➕ Nowy post", callback_data=CB_NEW_POST),
    ])
    keyboard.append([InlineKeyboardButton(text="🔙 Planer postów", callback_data="post_planning_start")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@post_planning_router.callback_query(F.data.startswith(CB_DELETE))
async def post_planning_delete(callback: CallbackQuery, state: FSMContext):
    """Usunięcie zaplanowanego posta."""
    try:
        post_id = int(callback.data.replace(CB_DELETE, ""))
        user_id = callback.from_user.id
        post = await PostManager.get_post_by_id(post_id, owner_id=user_id)
        if not post:
            await callback.answer("❌ Nie znaleziono posta.", show_alert=True)
            return
        await PostManager.delete_post(post_id)
        await callback.answer("✅ Post usunięty.", show_alert=True)
        callback.data = CB_LIST
        await post_planning_list(callback, state)
    except Exception as e:
        logger.error(f"post_planning delete: {e}")
        await callback.answer("Błąd.", show_alert=True)


# Limit postów zmieniany tylko z panelu admina (przyszła funkcja).
