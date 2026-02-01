# handlers/start.py
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from config import settings
from database.models import ChannelManager, BotUsersManager
from utils.states import ChannelSetup
from aiogram.enums import ParseMode

start_router = Router(name="start")

# HTML daje pewne formatowanie; w treści od użytkownika escapuj < > &
def _h(s: str) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

@start_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Główne menu - wybór kanału"""
    await BotUsersManager.ensure_user(message.from_user.id)
    await show_main_menu(message, message.from_user.id, state)

async def show_main_menu(message: Message, user_id: int, state: FSMContext):
    """Logika wyświetlania głównego menu"""
    await state.clear()

    # Pobierz kanały użytkownika
    channels = await ChannelManager.get_user_channels(user_id)

    if not channels:
        welcome_text = (
            "👋 <b>Witaj w EWH-WatchDog!</b>\n\n"
            "Jestem botem do zarządzania <b>płatnymi kanałami</b> i subskrypcjami. "
            "Każdy może ze mną pracować — bez skomplikowanej konfiguracji.\n\n"
            "✨ <b>Co zyskujesz?</b>\n"
            "• <b>Automatyczne zarząrdzanie zubskrybcjami</b> — nowy user na kanale premium -> dostajesz powiadomienie i ustawiasz rodzaj i czas subskrybcji, a bot sam pilnuje jej terminu\n"
            "• <b>Przydatne powiadomienia</b> — powiadomienia pomagające Ci wyciągnąć max z leadów\n"
            "• <b>Planer postów</b> — publikuj treści o wybranej godzinie na dowolnym kanale\n\n"
            "⚡|<b>Powered by @thunder_threads</b>\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Dodaj kanał", callback_data="add_new_channel_help")]
        ])
        await message.answer(welcome_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    # Budowanie klawiatury z kanałami
    # Sortowanie kanałów
    premium_channels = [ch for ch in channels if ch['type'] == 'premium']
    free_channels = [ch for ch in channels if ch['type'] == 'free']

    # Tekst główny — HTML dla pewnego formatowania
    msg_text = (
        "✨ <b>Witaj w centrum dowodzenia</b>\n\n"
        "Subskrypcje, planer postów i statystyki w jednym miejscu\n\n"
        "<i>(Przez ograniczenia telegrama bot <b>nie</b> widzi użytkowników, którzy byli na kanale przed dołączeniem bota)</i>\n\n"
    )
    if premium_channels or free_channels:
        if premium_channels:
            msg_text += "💎 <b>Premium</b> \n"
            msg_text += "1. <i>Gdy ktoś nowy dołączy do kanału, bot wyśle Ci powiadomienie i zapyta o rodzaj i czas subskrybcji</i> \n"
            msg_text += "2. <i>Gdy subskrybcja wygasa, bot automatycznie usuwa użytkownika z premium i Cię o tym powiadamia</i> \n\n"
        if free_channels:
            msg_text += "🆓 <b>Free</b> \n"
            msg_text += "- <i>Gdy ktoś nowy dołączy do kanału, bot Cię o tym informuje, a Ty możesz szybko rozpocząć konwersację :)</i> \n"
        msg_text += "\n👇 Kliknij przycisk poniżej:"
    else:
        msg_text += "👇 Wybierz akcję:"

    # Klawiatura: premium i free w dwóch kolumnach obok siebie
    keyboard = []
    max_rows = max(len(premium_channels), len(free_channels)) or 1
    for i in range(max_rows):
        row = []
        if i < len(premium_channels):
            ch = premium_channels[i]
            row.append(InlineKeyboardButton(
                text=f"💎 {ch['title'][:28]}",
                callback_data=f"manage_channel_{ch['channel_id']}"
            ))
        if i < len(free_channels):
            ch = free_channels[i]
            row.append(InlineKeyboardButton(
                text=f"🆓 {ch['title'][:28]}",
                callback_data=f"manage_channel_{ch['channel_id']}"
            ))
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(text="📅 Planer postów", callback_data="post_planning_start"),
        InlineKeyboardButton(text="📢 SFS System", callback_data="sfs_start"),
    ])
    keyboard.append([InlineKeyboardButton(text="📊 Statystyki", callback_data="general_stats")])
    keyboard.append([InlineKeyboardButton(text="➕ Dodaj kanał", callback_data="add_new_channel_help")])
    if settings.is_superadmin(user_id):
        keyboard.append([InlineKeyboardButton(text="🔐 Super-Admin", callback_data="superadmin_panel")])

    await message.answer(
        msg_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )

@start_router.callback_query(F.data == "refresh_channels")
async def refresh_channels(callback: CallbackQuery, state: FSMContext):
    """Odświeżenie listy kanałów"""
    try:
        await callback.message.delete()
    except:
        pass
    # Tutaj poprawka: używamy callback.from_user.id zamiast callback.message.from_user.id
    await show_main_menu(callback.message, callback.from_user.id, state)

@start_router.callback_query(F.data == "add_new_channel_help")
async def add_new_channel_help(callback: CallbackQuery, state: FSMContext):
    """Pomoc przy dodawaniu kanału"""
    await state.set_state(ChannelSetup.waiting_for_channel_forward)

    text = (
        "<b>DODAWANIE NOWEGO KANAŁU</b> ➕\n\n"
        "1. Dodaj bota jako Administratora do swojego kanału.\n"
        "2. Wyślij tam dowolną wiadomość.\n"
        "3. Przekaż ją tutaj.\n"
        "4. Wybierz typ kanału (Premium lub Free).\n\n"
        "<b>UWAGA</b>: Bot NIE potrzebuje <b>ŻADNYCH</b> uprawnień do kanału, ale jeśli chcesz zachować pełną funkcjonalność managera subskrybcji, SFS i powiadomień, to zachęcamy włączyć zarządzanie członkami i publikowanie wiadomości."
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Wróć", callback_data="refresh_channels")
    ]])

    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        if "business connection" in str(e).lower():
            await callback.answer()
            await callback.message.answer(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            raise

@start_router.callback_query(F.data.startswith("select_channel_"))
async def select_channel(callback: CallbackQuery, state: FSMContext):
    """Wybór kanału -> ustawienie w State"""
    try:
        channel_id = int(callback.data.split("_")[-1])
        user_id = callback.from_user.id

        # Security check
        if not await ChannelManager.is_owner(user_id, channel_id):
            await callback.answer("🚫 To nie Twój kanał!", show_alert=True)
            return

        # Zapisz aktywny kanał w sesji
        await state.update_data(active_channel_id=channel_id)

        text = (
            "✅ <b>Wybrany kanał</b> 🎯\n\n"
            "Wszystkie akcje dotyczą teraz tego kanału.\n\n"
            "<b>Narzędzia:</b> 🛠️\n"
            "/start — panel kanału (użytkownicy, statystyki, ustawienia)\n"
            "/newpost — nowy post\n"
            "/stats — statystyki"
        )
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Zmień kanał", callback_data="refresh_channels")
        ]])
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except TelegramBadRequest as e:
            if "business connection" in str(e).lower():
                await callback.answer()
                await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            else:
                raise
    except Exception as e:
        await callback.answer("Błąd wyboru kanału")
