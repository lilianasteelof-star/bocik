"""
Handler do zarządzania postami - FSM dla tworzenia i planowania
"""
import json
import logging
from datetime import datetime
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery, ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import settings
from database.models import PostManager
from utils.states import PostCreation
from utils.helpers import (
    create_schedule_keyboard,
    create_buttons_keyboard,
    parse_buttons_text,
    parse_datetime_from_text,
    create_inline_keyboard_from_buttons
)

logger = logging.getLogger("handlers")
admin_posts_router = Router()


@admin_posts_router.message(Command("newpost"))
async def start_post_creation(message: Message, state: FSMContext):
    """Rozpoczęcie procesu tworzenia nowego posta"""
    try:
        await state.clear()  # Wyczyszczenie poprzedniego stanu
        
        await message.reply(
            "📝 **Tworzenie nowego posta**\n\n"
            "Wyślij treść posta (tekst, zdjęcie lub wideo):",
            parse_mode="Markdown"
        )
        
        await state.set_state(PostCreation.waiting_content)
        logger.info("Rozpoczęto tworzenie posta")
        
    except Exception as e:
        logger.error(f"Błąd rozpoczynania tworzenia posta: {e}")
        await message.reply("❌ Błąd rozpoczynania tworzenia posta")


@admin_posts_router.message(PostCreation.waiting_content)
async def handle_post_content(message: Message, state: FSMContext):
    """Obsługa treści posta"""
    try:
        content_data = {}
        
        # Obsługa różnych typów treści
        if message.text:
            content_data = {
                "content_type": "text",
                "content": message.text,
                "caption": None
            }
            
        elif message.photo:
            # Największe zdjęcie (najlepsza jakość)
            photo = message.photo[-1]
            content_data = {
                "content_type": "photo",
                "content": photo.file_id,
                "caption": message.caption
            }
            
        elif message.video:
            content_data = {
                "content_type": "video",
                "content": message.video.file_id,
                "caption": message.caption
            }
            
        elif message.document:
            content_data = {
                "content_type": "document",
                "content": message.document.file_id,
                "caption": message.caption
            }
            
        else:
            await message.reply(
                "❌ Nieobsługiwany typ treści. "
                "Wyślij tekst, zdjęcie, wideo lub dokument."
            )
            return
        
        # Zapisanie treści w stanie
        await state.update_data(**content_data)
        
        # Przejście do pytania o przyciski
        await message.reply(
            "✅ Treść zapisana!\n\n"
            "🔘 Chcesz dodać przyciski do posta?",
            reply_markup=create_buttons_keyboard()
        )
        
        await state.set_state(PostCreation.waiting_buttons)
        logger.info(f"Zapisano treść posta: {content_data['content_type']}")
        
    except Exception as e:
        logger.error(f"Błąd obsługi treści posta: {e}")
        await message.reply("❌ Błąd przetwarzania treści")


@admin_posts_router.callback_query(F.data == "buttons_add", PostCreation.waiting_buttons)
async def request_buttons_input(callback: CallbackQuery, state: FSMContext):
    """Prośba o wprowadzenie przycisków"""
    try:
        await callback.message.edit_text(
            "🔘 **Dodawanie przycisków**\n\n"
            "Wyślij przyciski w formacie:\n"
            "`Tekst - Link`\n"
            "`Inny tekst - https://example.com`\n\n"
            "Każdy przycisk w nowej linii.",
            parse_mode="Markdown"
        )
        
        await callback.answer()
        # Pozostajemy w tym samym stanie, czekając na input
        
    except Exception as e:
        logger.error(f"Błąd prośby o przyciski: {e}")
        await callback.answer("❌ Błąd", show_alert=True)


@admin_posts_router.callback_query(F.data == "buttons_skip", PostCreation.waiting_buttons)
async def skip_buttons(callback: CallbackQuery, state: FSMContext):
    """Pominięcie dodawania przycisków"""
    try:
        await callback.message.edit_text(
            "⏰ **Planowanie publikacji**\n\n"
            "Kiedy chcesz opublikować post?",
            reply_markup=create_schedule_keyboard()
        )
        
        await state.set_state(PostCreation.waiting_schedule)
        await callback.answer()
        
        logger.info("Pominięto dodawanie przycisków")
        
    except Exception as e:
        logger.error(f"Błąd pomijania przycisków: {e}")
        await callback.answer("❌ Błąd", show_alert=True)


@admin_posts_router.callback_query(F.data == "buttons_cancel", PostCreation.waiting_buttons)
async def cancel_post_creation(callback: CallbackQuery, state: FSMContext):
    """Anulowanie tworzenia posta"""
    try:
        await callback.message.edit_text("❌ Tworzenie posta anulowane")
        await state.clear()
        await callback.answer()
        
        logger.info("Anulowano tworzenie posta")
        
    except Exception as e:
        logger.error(f"Błąd anulowania: {e}")
        await callback.answer("❌ Błąd", show_alert=True)


@admin_posts_router.message(PostCreation.waiting_buttons)
async def handle_buttons_input(message: Message, state: FSMContext):
    """Obsługa wprowadzonych przycisków"""
    try:
        if not message.text:
            await message.reply("❌ Wyślij tekst z przyciskami")
            return
        
        # Parsowanie przycisków
        buttons = parse_buttons_text(message.text)
        
        if not buttons:
            await message.reply(
                "❌ Nie znaleziono prawidłowych przycisków.\n\n"
                "Format: `Tekst - Link`"
            )
            return
        
        # Zapisanie przycisków w stanie
        await state.update_data(buttons=buttons)
        
        # Potwierdzenie i przejście do planowania
        buttons_text = "\n".join([f"• {btn['text']} → {btn['url']}" for btn in buttons])
        
        await message.reply(
            f"✅ **Przyciski dodane:**\n\n{buttons_text}\n\n"
            "⏰ Kiedy chcesz opublikować post?",
            reply_markup=create_schedule_keyboard()
        )
        
        await state.set_state(PostCreation.waiting_schedule)
        logger.info(f"Dodano {len(buttons)} przycisków")
        
    except Exception as e:
        logger.error(f"Błąd obsługi przycisków: {e}")
        await message.reply("❌ Błąd przetwarzania przycisków")


@admin_posts_router.message(PostCreation.waiting_schedule)
async def handle_schedule_time(message: Message, state: FSMContext):
    """Obsługa czasu zaplanowania"""
    try:
        if not message.text:
            await message.reply("❌ Wyślij datę i czas jako tekst")
            return
        
        # Parsowanie daty
        publish_date = parse_datetime_from_text(message.text)
        
        if not publish_date:
            await message.reply(
                "❌ Nieprawidłowy format daty.\n\n"
                "Użyj: `DD.MM.YYYY HH:MM`\n"
                "Przykład: `31.12.2024 15:30`"
            )
            return
        
        # Pobranie danych posta i kanału (domyślnie premium)
        data = await state.get_data()
        owner_id = message.from_user.id
        from database.models import SettingsManager
        channel_id = await SettingsManager.get_premium_channel_id(owner_id)
        if not channel_id:
            await message.reply("❌ Nie skonfigurowano kanału premium. Użyj /addchannel lub ustawienia.")
            return

        # Zapisanie zaplanowanego posta w bazie
        post_id = await PostManager.create_scheduled_post(
            owner_id=owner_id,
            channel_id=channel_id,
            content_type=data["content_type"],
            content=data["content"],
            publish_date=publish_date,
            caption=data.get("caption"),
            buttons=data.get("buttons")
        )
        
        if post_id:
            await message.reply(
                f"✅ **Post zaplanowany!**\n\n"
                f"🆔 ID: {post_id}\n"
                f"📅 Data publikacji: {publish_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"📝 Typ: {data['content_type']}"
            )
        else:
            await message.reply("❌ Błąd planowania posta")
        
        await state.clear()
        logger.info(f"Zaplanowano post na {publish_date} dla {owner_id}")
        
    except Exception as e:
        logger.error(f"Błąd planowania: {e}")
        await message.reply("❌ Błąd planowania posta")


async def send_post_to_channel(bot: Bot, post_data: dict, user_id: int, channel_id: int = None) -> bool:
    """Wysłanie posta na kanał: channel_id jeśli podany, inaczej kanał premium użytkownika."""
    try:
        from database.models import SettingsManager

        target_channel_id = int(channel_id) if channel_id is not None else None
        if not target_channel_id:
            target_channel_id = await SettingsManager.get_premium_channel_id(user_id)
        if not target_channel_id:
            logger.error(f"Kanał nie jest skonfigurowany dla {user_id}")
            return False
        target_channel_id = int(target_channel_id)

        content_type = post_data["content_type"]
        content = post_data["content"]
        caption = post_data.get("caption")
        buttons = post_data.get("buttons")
        reply_markup = create_inline_keyboard_from_buttons(buttons) if buttons else None

        if content_type == "text":
            await bot.send_message(
                chat_id=target_channel_id,
                text=content,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif content_type == "photo":
            await bot.send_photo(
                chat_id=target_channel_id,
                photo=content,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif content_type == "video":
            await bot.send_video(
                chat_id=target_channel_id,
                video=content,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif content_type == "document":
            await bot.send_document(
                chat_id=target_channel_id,
                document=content,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif content_type == "sticker":
            await bot.send_sticker(chat_id=target_channel_id, sticker=content)
        else:
            logger.error(f"Nieobsługiwany typ treści: {content_type}")
            return False

        logger.info(f"Wysłano post na kanał {target_channel_id}: {content_type}")
        return True
    except Exception as e:
        logger.error(f"Błąd wysyłania posta na kanał: {e}")
        return False


@admin_posts_router.callback_query(F.data == "schedule_now", PostCreation.waiting_schedule)
async def publish_now(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Natychmiastowa publikacja posta"""
    try:
        # Pobranie danych posta
        data = await state.get_data()
        user_id = callback.from_user.id
        
        success = await send_post_to_channel(bot, data, user_id)
        
        if success:
            await callback.message.edit_text("✅ Post został opublikowany!")
        else:
            await callback.message.edit_text("❌ Błąd publikacji posta. Sprawdź /checksetup.")
        
        await state.clear()
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Błąd natychmiastowej publikacji: {e}")
        await callback.answer("❌ Błąd", show_alert=True)


@admin_posts_router.message(Command("scheduled"))
async def list_scheduled_posts(message: Message):
    """Lista zaplanowanych postów"""
    try:
        user_id = message.from_user.id
        posts = await PostManager.get_scheduled_posts(user_id)
        
        if not posts:
            await message.reply("📋 Brak zaplanowanych postów")
            return
        
        response = "📅 **Zaplanowane posty:**\n\n"
        
        for post in posts[:10]:  # Limit 10
            content_preview = post.content[:50] + "..." if len(post.content) > 50 else post.content
            response += (
                f"🆔 `{post.post_id}` | 📝 {post.content_type}\n"
                f"📅 {post.publish_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"📄 {content_preview}\n\n"
            )
        
        await message.reply(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Błąd listowania postów: {e}")
        await message.reply("❌ Błąd pobierania zaplanowanych postów")
