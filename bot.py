"""
Główny plik bota - entry point aplikacji
Premium Telegram Bot do zarządzania kanałami subskrypcyjnymi
"""
import asyncio
import logging
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, Message, MenuButtonCommands, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

# Import konfiguracji
from config import settings

# Import middleware
from middlewares.auth import (
    AuthMiddleware, 
    LoggingMiddleware, 
    RateLimitMiddleware,
    DatabaseMiddleware
)

# Import routerów
from handlers.events import events_router
from handlers.admin_subs import admin_subs_router
from handlers.admin_posts import admin_posts_router
from handlers.admin_settings import admin_settings_router
from handlers.start import start_router

# Import bazy danych i schedulera
from database.connection import db_manager
from utils.scheduler import BotScheduler
from handlers.admin_bans import admin_bans_router
from handlers.admin_edit import admin_edit_router
from handlers.sfs import run_update_sfs_members_count
from handlers.superadmin import superadmin_router
from handlers.inbox import inbox_router
logger = logging.getLogger(__name__)


class PremiumBot:
    """Główna klasa bota Premium"""
    
    def __init__(self):
        # Inicjalizacja bota z domyślnymi właściwościami
        self.bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(
                parse_mode=ParseMode.MARKDOWN
            )
        )
        
        # Storage dla FSM (w pamięci)
        storage = MemoryStorage()
        
        # Inicjalizacja dispatchera
        self.dp = Dispatcher(storage=storage)
        
        # Scheduler
        self.scheduler = BotScheduler(self.bot)
        
        # Dependency Injection dla handlerów
        self.dp["scheduler"] = self.scheduler
        
        # Setup middleware i routerów
        self._setup_middleware()
        self._setup_routers()
        self._setup_commands()
        self._setup_error_handlers()
    
    def _setup_middleware(self):
        """Konfiguracja middleware"""
        # Kolejność middleware ma znaczenie!
        
        # 1. Database middleware (pierwszy, żeby zapewnić połączenie)
        self.dp.message.middleware(DatabaseMiddleware())
        self.dp.callback_query.middleware(DatabaseMiddleware())
        self.dp.chat_member.middleware(DatabaseMiddleware())
        
        # 2. Rate limiting middleware
        self.dp.message.middleware(RateLimitMiddleware(max_requests_per_minute=30))
        self.dp.callback_query.middleware(RateLimitMiddleware(max_requests_per_minute=30))
        
        # 3. Logging middleware
        self.dp.message.middleware(LoggingMiddleware())
        self.dp.callback_query.middleware(LoggingMiddleware())
        self.dp.chat_member.middleware(LoggingMiddleware())
        
        # 4. Auth middleware (ostatni, żeby miał wszystkie dane)
        self.dp.message.middleware(AuthMiddleware())
        self.dp.callback_query.middleware(AuthMiddleware())
        
        logger.info("Middleware skonfigurowane")
    
    def _setup_routers(self):
        """Konfiguracja routerów"""
        # Dodanie routerów do dispatchera
        from handlers.shortcuts import shortcuts_router
        from handlers.admin_stats import admin_stats_router
        from handlers.dashboard import dashboard_router
        from handlers.post_planning import post_planning_router
        from handlers.sfs import sfs_router
        
        self.dp.include_router(start_router)  # Nowy router startowy
        self.dp.include_router(post_planning_router)  # Planowanie postów (przed dashboard)
        self.dp.include_router(sfs_router)  # SFS System
        self.dp.include_router(dashboard_router)  # Dashboard router
        self.dp.include_router(shortcuts_router) # Skróty (przed settings!)
        self.dp.include_router(events_router)  # ChatMemberUpdated events
        self.dp.include_router(admin_subs_router)  # Zarządzanie subskrypcjami
        self.dp.include_router(admin_posts_router)  # Zarządzanie postami
        self.dp.include_router(admin_settings_router)  # Ustawienia kanałów
        self.dp.include_router(admin_bans_router) # lista banó i unban
        self.dp.include_router(admin_edit_router)
        self.dp.include_router(admin_stats_router)
        self.dp.include_router(superadmin_router)
        self.dp.include_router(inbox_router)  # Na końcu – łapie tylko nieobsłużone wiadomości (inbox)
        
        logger.info("Routery skonfigurowane")

    def _setup_error_handlers(self):
        """Globalna obsługa błędów (np. business connection not found)."""
        @self.dp.errors(TelegramBadRequest)
        async def on_telegram_bad_request(event, exception: TelegramBadRequest):
            if "business connection" not in str(exception).lower():
                raise exception
            callback = event.callback_query if hasattr(event, "callback_query") and event.callback_query else (event if isinstance(event, CallbackQuery) else None)
            if callback:
                try:
                    await callback.answer(
                        "Bot nie obsługuje czatu przez konto biznesowe. Użyj bota w zwykłym czacie (napisz /start do bota).",
                        show_alert=True,
                    )
                except Exception:
                    pass
            logger.debug("Business connection update obsłużony: %s", exception)

    def _setup_commands(self):
        """Konfiguracja podstawowych komend"""
        
        # Logika /start przeniesiona do handlers/start.py

        @self.dp.message(Command("checknow"))
        async def cmd_check_now(message: Message):
            """Ręczne sprawdzenie wygasłych subskrypcji"""
            # Dostęp dla każdego admina (właściciela)
            try:
                await message.reply("🔍 Sprawdzam wygasłe subskrypcje...")
                
                # Ręczne uruchomienie sprawdzania (scheduler sprawdza globalnie)
                await self.scheduler.check_expired_subscriptions()
                
                await message.reply("✅ Sprawdzenie zakończone! Listę użytkowników zobacz w panelu kanału (/start → wybierz kanał).")
                
            except Exception as e:
                logger.error(f"Błąd ręcznego sprawdzania: {e}")
                await message.reply(f"❌ Błąd: {e}")
        
        @self.dp.message(Command("checksetup"))
        async def cmd_check_setup(message: Message):
            """Sprawdzenie konfiguracji bota dla użytkownika"""
            try:
                from database.models import ChannelManager
                
                user_id = message.from_user.id
                channels = await ChannelManager.get_user_channels(user_id)
                
                status_text = "🔍 **Diagnostyka Konfiguracji**\n\n"
                
                if channels:
                    status_text += "**Twoje kanały:**\n"
                    for ch in channels:
                        status_text += f"✅ {ch['title']} ({ch['type']})\n"
                else:
                    status_text += "❌ Brak skonfigurowanych kanałów\n"
                
                status_text += "\n**Baza danych:**\n"
                try:
                    connection = await db_manager.get_connection()
                    status_text += "✅ Połączenie OK\n"
                except Exception as db_e:
                    status_text += f"❌ Błąd: {db_e}\n"
                
                status_text += "\n**Scheduler:**\n"
                scheduler_status = self.scheduler.get_scheduler_status()
                if scheduler_status['running']:
                    status_text += f"✅ Aktywny ({scheduler_status['job_count']} zadań)\n"
                else:
                    status_text += "❌ Nieaktywny\n"
                
                await message.reply(status_text, parse_mode="Markdown")
                
            except Exception as e:
                logger.error(f"Błąd sprawdzania konfiguracji: {e}")
                await message.reply(f"❌ Błąd: {e}")

        @self.dp.message(Command("sfs_autofill"))
        async def cmd_sfs_autofill(message: Message):
            """Tymczasowa komenda: wymuszenie SFS auto-fill views / subów (jak job co 6h, bez odświeżono)."""
            try:
                await message.reply("🔄 Uruchamiam aktualizację subów SFS...")
                await run_update_sfs_members_count(message.bot)
                await message.reply("✅ Aktualizacja subów zakończona.")
            except Exception as e:
                logger.error(f"SFS autofill: {e}")
                await message.reply(f"❌ Błąd: {e}")
        
        @self.dp.message(Command("help"))
        async def cmd_help(message: Message):
            """Komenda /help — funkcje i korzyści bota"""
            help_text = (
                "📖 <b>Pomoc — EWH-WatchDog</b>\n\n"
                "Bot do zarządzania płatnymi kanałami i subskrypcjami. "
                "Wszystko w jednym miejscu: użytkownicy, statystyki, planer postów.\n\n"
                "✨ <b>Główne funkcje</b>\n"
                "• <b>/start</b> — menu główne, wybór kanału, planer, statystyki, dodawanie kanału\n"
                "• <b>/premium</b> — szybki dostęp do kanału Premium (opcjonalnie: <code>/premium stats</code>)\n"
                "• <b>/stats</b> — podsumowanie subskrypcji i statystyk dla Twoich kanałów\n"
                "• <b>/newpost</b> — tworzenie nowego posta na wybrany kanał\n"
                "• <b>/getchannels</b> — lista Twoich kanałów z linkami\n\n"
                "📢 <b>Z panelu kanału</b> (po wyborze kanału w /start)\n"
                "Użytkownicy, lista zbanowanych, statystyki kanału, edycja subskrypcji, usuwanie kanału.\n\n"
                "📅 <b>Planer postów</b> (z menu /start)\n"
                "Zaplanowane posty, nowy post, wybór kanału i terminu publikacji.\n\n"
                "⚙️ <b>Konfiguracja</b>\n"
                "• <b>/addchannel</b> — dodanie nowego kanału (lub przycisk „Dodaj kanał” w menu)\n"
                "• <b>/checksetup</b> — diagnostyka: kanały, baza, scheduler\n\n"
                "🏠 <b>Nawigacja</b>\n"
                "Zawsze możesz wrócić do menu głównego: <b>/start</b> lub przycisk „Menu główne” / „Wróć”.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👤 <b>Autor bota:</b> @thunder_dev\n"
                "🙌 Stworzono dzięki społeczności <b>@thunder_threads</b>"
            )
            await message.reply(help_text, parse_mode=ParseMode.HTML)

        
        logger.info("Podstawowe komendy skonfigurowane")
    
    async def _set_bot_commands(self):
        """Ustawienie listy komend bota (menu komend w Telegramie)"""
        commands = [
            BotCommand(command="start", description="🏠 Menu główne"),
            BotCommand(command="premium", description="💎 Kanał Premium"),
            BotCommand(command="stats", description="📊 Statystyki"),
            BotCommand(command="newpost", description="📝 Nowy post"),
            BotCommand(command="addchannel", description="➕ Dodaj kanał"),
            BotCommand(command="getchannels", description="📋 Moje kanały"),
            BotCommand(command="help", description="❓ Pomoc"),
        ]
        await self.bot.set_my_commands(
            commands=commands,
            scope=BotCommandScopeDefault()
        )
        logger.info("Komendy bota ustawione")
    
    async def start_bot(self):
        """Uruchomienie bota"""
        try:
            logger.info("Uruchamianie Premium Bota...")
            
            # Inicjalizacja bazy danych
            await db_manager.init_tables()
            logger.info("Baza danych zainicjalizowana")
            
            # Bufor logów dla konsolki super-admina
            from utils.log_buffer import setup_buffer_handler
            setup_buffer_handler()
            
            # Ustawienie komend bota
            await self._set_bot_commands()

            # Przycisk menu obok pola wiadomości: Commands zamiast Mini App (Web App)
            try:
                await self.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            except Exception as menu_err:
                logger.warning("Nie udało się ustawić przycisku menu (Commands): %s", menu_err)

            # Uruchomienie schedulera (przekazanie pętli, żeby async joby się wykonywały)
            await self.scheduler.start(loop=asyncio.get_running_loop())
            
            # Powiadomienie admina o starcie
            try:
                await self.bot.send_message(
                    chat_id=settings.ADMIN_ID,
                    text=(
                        f"🚀 **Premium Bot uruchomiony!**\n\n"
                        f"✅ Baza danych: OK\n"
                        f"✅ Scheduler: OK\n"
                        f"Bot gotowy do pracy! 🎯"
                    )
                )
            except Exception as notify_error:
                logger.warning(f"Nie można wysłać powiadomienia o starcie: {notify_error}")
            
            # Rozpoczęcie pobierania aktualizacji
            logger.info("Bot rozpoczyna pobieranie aktualizacji...")
            await self.dp.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query", "chat_member", "chat_join_request", "channel_post", "edited_channel_post"]
            )
            
        except Exception as e:
            logger.error(f"Błąd uruchomienia bota: {e}")
            raise
    
    async def stop_bot(self):
        """Zatrzymanie bota"""
        try:
            logger.info("Zatrzymywanie bota...")
            
            # Zatrzymanie schedulera
            await self.scheduler.stop()
            
            # Zamknięcie połączenia z bazą danych
            await db_manager.disconnect()
            
            # Powiadomienie admina o zatrzymaniu
            try:
                await self.bot.send_message(
                    chat_id=settings.ADMIN_ID,
                    text="🛑 **Premium Bot zatrzymany**\n\nDo zobaczenia! 👋"
                )
            except Exception:
                pass  # Ignorujemy błędy przy zatrzymywaniu
            
            # Zamknięcie bota
            await self.bot.session.close()
            
            logger.info("Bot zatrzymany")
            
        except Exception as e:
            logger.error(f"Błąd zatrzymania bota: {e}")


async def main():
    """Główna funkcja aplikacji"""
    bot = PremiumBot()
    run_task = asyncio.create_task(bot.start_bot())

    def signal_handler(signum, frame):
        logger.info(f"Otrzymano sygnał {signum} (graceful shutdown)")
        run_task.cancel()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await run_task
    except asyncio.CancelledError:
        logger.info("Zatrzymywanie bota (sygnał)...")
    except KeyboardInterrupt:
        logger.info("Przerwano przez użytkownika")
    except Exception as e:
        logger.critical(f"Krytyczny błąd: {e}")
        raise
    finally:
        try:
            await bot.stop_bot()
        except Exception as e:
            logger.error(f"Błąd przy zatrzymywaniu bota: {e}")


if __name__ == "__main__":
    """Entry point"""
    try:
        # Uruchomienie głównej funkcji async
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Aplikacja przerwana")
    except Exception as e:
        logger.critical(f"Błąd uruchomienia aplikacji: {e}")
        sys.exit(1)