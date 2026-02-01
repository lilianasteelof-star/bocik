"""
Handler do zarządzania konkretnym kanałem (Dashboard)
"""
import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext


from database.models import ChannelManager, SubscriptionManager
from utils.states import SubscriptionManagement, SubscriptionEditing

logger = logging.getLogger("handlers")
dashboard_router = Router()

@dashboard_router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Pusty callback dla nagłówków"""
    await callback.answer()

@dashboard_router.callback_query(F.data.startswith("manage_channel_"))
async def show_channel_options(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Pokaż opcje wybranego kanału"""
    try:
        channel_id = int(callback.data.split("_")[-1])
        channel = await ChannelManager.get_channel(channel_id)

        if not channel:
            await callback.answer("❌ Kanał nie istnieje", show_alert=True)
            return
            
        # Pobieranie info o kanale z API Telegrama (dla linku)
        chat_link = "Brak linku"
        try:
            chat = await bot.get_chat(channel_id)
            if chat.username:
                chat_link = f"@{chat.username} (t.me/{chat.username})"
            elif chat.invite_link:
                 chat_link = f"[Link zaproszenia]({chat.invite_link})"
            elif chat.title:
                 chat_link = f"{chat.title}"
        except Exception as e:
            logger.warning(f"Błąd pobierania info o czacie {channel_id}: {e}")
            chat_link = "Nie można pobrać linku"

        # Zapisz ID kanału w stanie (przydatne np. przy dodawaniu usera)
        await state.update_data(active_channel_id=channel_id)

        keyboard = []

        if channel['type'] == 'premium':
            # Opcje Premium
            keyboard.append([InlineKeyboardButton(text="👥 UŻYTKOWNICY", callback_data=f"list_users_{channel_id}")])
            keyboard.append([InlineKeyboardButton(text="🚫 ZBANOWANI", callback_data=f"list_banned_{channel_id}")])

            keyboard.append([InlineKeyboardButton(text="📊 STATYSTYKI", callback_data=f"channel_stats_{channel_id}")])
            keyboard.append([InlineKeyboardButton(text="🗑️ USUŃ KANAŁ", callback_data=f"confirm_delete_{channel_id}")])
        else:
            # Opcje Free
            keyboard.append([InlineKeyboardButton(text="🗑️ USUŃ KANAŁ", callback_data=f"confirm_delete_{channel_id}")])

        keyboard.append([InlineKeyboardButton(text="🔙 POWRÓT DO LISTY", callback_data="refresh_channels")])

        await callback.message.edit_text(
            f"⚙️ **PANEL ZARZĄDZANIA** 🛠️\n\n"
            f"📢 **{channel['title']}**\n"
            f"🔗 {chat_link}\n"
            f"ID: `{channel['channel_id']}`\n"
            f"Typ: `{channel['type'].upper()}`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Błąd dashboardu: {e}", exc_info=True)
        await callback.answer("Błąd wyświetlania opcji")

@dashboard_router.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_channel(callback: CallbackQuery):
    """Potwierdzenie usunięcia kanału"""
    channel_id = int(callback.data.split("_")[-1])
    
    keyboard = [
        [InlineKeyboardButton(text="✅ TAK, USUŃ", callback_data=f"delete_channel_{channel_id}")],
        [InlineKeyboardButton(text="🔙 NIE, ANULUJ", callback_data=f"manage_channel_{channel_id}")]
    ]
    
    await callback.message.edit_text(
        "⚠️ **CZY NA PEWNO CHCESZ USUNĄĆ TEN KANAŁ?**\n\n"
        "Bot opuści kanał, a wszystkie ustawienia zostaną trwale usunięte z bazy danych.\n"
        "Tej operacji nie można cofnąć.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@dashboard_router.callback_query(F.data.startswith("delete_channel_"))
async def delete_channel(callback: CallbackQuery, bot: Bot):
    """Usunięcie kanału"""
    try:
        channel_id = int(callback.data.split("_")[-1])
        
        # 1. Bot wychodzi z kanału
        try:
            await bot.leave_chat(channel_id)
        except Exception as e:
            logger.warning(f"Bot nie mógł wyjść z kanału {channel_id}: {e}")

        # 2. Usuwamy z bazy (musimy dodać metodę do ChannelManager)
        # TODO: Add delete method to ChannelManager or run raw query
        # For now raw query via db_manager
        from database.connection import db_manager
        connection = await db_manager.get_connection()
        async with connection.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,)): pass
        await connection.commit()
        
        await callback.answer("✅ Kanał usunięty!", show_alert=True)

        await callback.message.edit_text(
            "✅ **Kanał został usunięty.**",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Wróć do listy", callback_data="refresh_channels")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Delete channel error: {e}")
        await callback.answer("Błąd usuwania kanału")

@dashboard_router.callback_query(F.data.startswith("list_users_"))
async def list_channel_users(callback: CallbackQuery):
    """Lista użytkowników w kanale"""
    channel_id = int(callback.data.split("_")[-1])
    
    subs = await SubscriptionManager.get_all_active_subscriptions(channel_id)
    
    if not subs:
        await callback.message.edit_text(
            "📭 **Brak aktywnych subskrypcji.**",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"manage_channel_{channel_id}")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Prosta lista z guzikami (można dodać paginację później)
    keyboard = []
    
    for sub in subs[:20]: # Limit 20 na stronę
        btn_text = f"{sub.full_name} (@{sub.username})"
        # Callback to edit user
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"edit_sub_{sub.user_id}_{channel_id}")])
    

    keyboard.append([InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"manage_channel_{channel_id}")])
    
    await callback.message.edit_text(
        f"👥 <b>UŻYTKOWNICY</b> ({len(subs)})\n"
        "Wybierz użytkownika, aby zarządzać jego subskrypcją (telegram nie pozwala wczytać użyttkowników, którzy byli na kanale przed dołączeniem bota)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@dashboard_router.callback_query(F.data.startswith("list_banned_"))
async def list_banned_users(callback: CallbackQuery):
    """Lista zbanowanych użytkowników"""
    channel_id = int(callback.data.split("_")[-1])
    
    subs = await SubscriptionManager.get_banned_subscriptions(channel_id)
    
    if not subs:
        await callback.message.edit_text(
            "✅ **Brak zbanowanych użytkowników.**",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"manage_channel_{channel_id}")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    keyboard = []
    
    for sub in subs[:20]:
        btn_text = f"{sub.full_name} (@{sub.username})"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"manage_banned_{sub.user_id}_{channel_id}")])
    
    keyboard.append([InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"manage_channel_{channel_id}")])
    
    await callback.message.edit_text(
        f"🚫 **ZBANOWANI ({len(subs)})**\n"
        "Wybierz użytkownika, aby go odbanować.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@dashboard_router.callback_query(F.data.startswith("manage_banned_"))
async def manage_banned_user_menu(callback: CallbackQuery):
    """Menu zarządzania zbanowanym użytkownikiem"""
    parts = callback.data.split("_")
    user_id = int(parts[2])
    channel_id = int(parts[3])
    
    sub = await SubscriptionManager.get_subscription(user_id, channel_id)
    
    if not sub:
        await callback.answer("❌ Subskrypcja nie istnieje", show_alert=True)
        await list_banned_users(callback)
        return
        
    keyboard = [
        [InlineKeyboardButton(text="✅ ODBANUJ", callback_data=f"unban_user_{user_id}_{channel_id}")],
        [InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"list_banned_{channel_id}")]
    ]
    
    await callback.message.edit_text(
        f"🚫 **ZBANOWANY UŻYTKOWNIK**\n\n"
        f"👤 {sub.full_name}\n"
        f"🆔 `{user_id}`\n"
        f"Kiedyś Tier: {sub.tier}\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@dashboard_router.callback_query(F.data.startswith("unban_user_"))
async def unban_user(callback: CallbackQuery, bot: Bot):
    """Odbanowanie użytkownika"""
    parts = callback.data.split("_")
    user_id = int(parts[2])
    channel_id = int(parts[3])
    
    try:
        # 1. Unban in Telegram
        try:
            await bot.unban_chat_member(chat_id=channel_id, user_id=user_id, only_if_banned=True)
        except Exception as e:
            logger.warning(f"Unban telegram error (might not be banned): {e}")

        # 2. Update DB
        # Zmieniamy status na 'left' zamiast 'active'.
        # Jeśli użytkownik został odbanowany, to nie znaczy, że ma ważną subskrypcję.
        # Jeśli ma ważną datę, to i tak status 'left' jest bezpieczny (scheduler nie ruszy), 
        # a jak wejdzie na kanał, to event handler 'join' obsłuży go odpowiednio.
        await SubscriptionManager.update_subscription_status(user_id, channel_id, "left")
        
        await callback.answer("✅ Użytkownik odbanowany!", show_alert=True)
        
        # Wróć do listy zbanowanych (powinna być pusta lub mniejsza)
        callback.data = f"list_banned_{channel_id}"
        await list_banned_users(callback)
        
    except Exception as e:
        logger.error(f"Unban error: {e}")
        await callback.answer("Błąd podczas banowania")

@dashboard_router.callback_query(F.data.startswith("edit_sub_"))
async def edit_subscription_menu(callback: CallbackQuery):
    """Menu edycji subskrypcji użytkownika"""
    # format: edit_sub_USERID_CHANNELID
    parts = callback.data.split("_")
    user_id = int(parts[2])
    channel_id = int(parts[3])
    
    sub = await SubscriptionManager.get_subscription(user_id, channel_id)
    
    if not sub:
        await callback.answer("❌ Subskrypcja nie istnieje", show_alert=True)
        # Refresh list
        await list_channel_users(callback) # Recursion-ish but safe
        return
        
    end_date_str = sub.end_date.strftime('%Y-%m-%d %H:%M')
    
    keyboard = [
        [InlineKeyboardButton(text="📅 ZMIEŃ DATĘ", callback_data=f"dash_edit_date_{user_id}_{channel_id}")],
        [InlineKeyboardButton(text="❌ ZAKOŃCZ SUBSKRYPCJĘ (Kick)", callback_data=f"kick_sub_{user_id}_{channel_id}")],
        [InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"list_users_{channel_id}")]
    ]
    
    await callback.message.edit_text(
        f"👤 **EDYCJA UŻYTKOWNIKA**\n\n"
        f"**Imię:** {sub.full_name}\n"
        f"**User:** @{sub.username}\n"
        f"**ID:** `{user_id}`\n"
        f"**Tier:** {sub.tier}\n"
        f"**Wygasa:** `{end_date_str}`\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@dashboard_router.callback_query(F.data.startswith("kick_sub_"))
async def kick_subscriber(callback: CallbackQuery, bot: Bot):
    """Wyrzucenie użytkownika z kanału"""
    parts = callback.data.split("_")
    target_user_id = int(parts[2])
    channel_id = int(parts[3])
    
    try:
        # Kick from telegram
        await bot.ban_chat_member(chat_id=channel_id, user_id=target_user_id)
        await bot.unban_chat_member(chat_id=channel_id, user_id=target_user_id)
        
        # Update DB status
        await SubscriptionManager.update_subscription_status(target_user_id, channel_id, "banned")
        
        await callback.answer("✅ Użytkownik usunięty!", show_alert=True)
        
        # Back to list
        # Re-construct callback data to mimic "list_users_CHANNEL"
        callback.data = f"list_users_{channel_id}"
        await list_channel_users(callback)
        
    except Exception as e:

        logger.error(f"Kick error: {e}")
        await callback.answer("Błąd podczas usuwania użytkownika", show_alert=True)

# --- DATE EDITING HANDLERS ---

@dashboard_router.callback_query(F.data.startswith("dash_edit_date_"))
async def dash_edit_date_start(callback: CallbackQuery, state: FSMContext):
    """Rozpoczęcie edycji daty z poziomu dashboardu (osobny stan FSM, bez konfliktu z admin_subs)."""
    parts = callback.data.split("_")
    user_id = int(parts[3])
    channel_id = int(parts[4])
    await state.update_data(
        dash_edit_user_id=user_id,
        dash_edit_channel_id=channel_id,
    )
    await state.set_state(SubscriptionEditing.waiting_for_new_date)

    await callback.message.edit_text(
        "📅 **NOWA DATA SUBSKRYPCJI** ⏳\n\n"
        "Wprowadź datę wygaśnięcia dostępu. Format:\n"
        "`YYYY-MM-DD HH:MM`\n"
        "(np. 2026-06-01 12:00)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 ANULUJ", callback_data=f"edit_sub_{user_id}_{channel_id}")
        ]]),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()


@dashboard_router.message(SubscriptionEditing.waiting_for_new_date, F.text)
async def handle_dashboard_date_input(message: Message, state: FSMContext):
    """Obsługa wpisanej daty w edycji z dashboardu (stan SubscriptionEditing)."""
    data = await state.get_data()
    # Tylko dashboard ustawia dash_edit_*; /edit ustawia edit_user_id – wtedy obsługuje admin_edit
    if not data.get("dash_edit_user_id"):
        return
    user_id = data.get("dash_edit_user_id")
    channel_id = data.get("dash_edit_channel_id")
    if not user_id or not channel_id:
        await message.reply("❌ Błąd sesji. Wróć do listy użytkowników.")
        await state.clear()
        return

    from utils.helpers import parse_end_date_from_text
    new_date = parse_end_date_from_text(message.text)
    if not new_date:
        await message.reply("❌ Błędny format daty. Spróbuj `YYYY-MM-DD HH:MM`", parse_mode=ParseMode.MARKDOWN)
        return

    success = await SubscriptionManager.update_subscription_details(
        user_id=user_id,
        channel_id=channel_id,
        new_end_date=new_date
    )
    await state.clear()

    if success:
        await message.reply(
            f"✅ Data zaktualizowana do: `{new_date.strftime('%Y-%m-%d %H:%M')}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 DO UŻYTKOWNIKA", callback_data=f"edit_sub_{user_id}_{channel_id}")],
                [InlineKeyboardButton(text="🏠 Menu główne", callback_data="refresh_channels")],
            ])
        )
    else:
        await message.reply(
            "❌ Błąd bazy danych.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"edit_sub_{user_id}_{channel_id}")
            ]])
        )


