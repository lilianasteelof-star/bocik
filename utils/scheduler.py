"""
APScheduler - automatyczne zadania: auto-kick i publikowanie postów
"""
import html
import logging
import asyncio
from datetime import datetime
from typing import Optional
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.base import JobLookupError

from aiogram.enums import ParseMode
from config import settings
from database.models import SubscriptionManager, PostManager
from handlers.admin_posts import send_post_to_channel
from handlers.sfs import run_update_sfs_members_count
from utils.helpers import format_kick_notification

logger = logging.getLogger("scheduler")


class BotScheduler:
    """Menedżer zadań zaplanowanych dla bota"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self._is_running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Uruchomienie schedulera. loop – pętla zdarzeń (do uruchamiania async jobów)."""
        try:
            if self._is_running:
                logger.warning("Scheduler już działa")
                return

            self._loop = loop or asyncio.get_running_loop()

            # Oba joby są async – uruchamiamy je w pętli przez run_coroutine_threadsafe (APScheduler nie awaituje)
            self.scheduler.add_job(
                func=self._run_async_job,
                trigger=IntervalTrigger(minutes=1),
                id="auto_kick_job",
                name="Automatyczne usuwanie wygasłych subskrypcji",
                replace_existing=True,
                args=[self.check_expired_subscriptions],
            )
            self.scheduler.add_job(
                func=self._run_async_job,
                trigger=IntervalTrigger(minutes=1),
                id="publish_posts_job",
                name="Publikowanie zaplanowanych postów",
                replace_existing=True,
                args=[self.publish_scheduled_posts],
            )
            self.scheduler.add_job(
                func=self._run_async_job,
                trigger=IntervalTrigger(hours=24),
                id="sfs_daily_job",
                name="SFS – sprawdzanie co 24h (placeholder)",
                replace_existing=True,
                args=[self.sfs_daily_check],
            )
            self.scheduler.add_job(
                func=self._run_async_job,
                trigger=IntervalTrigger(hours=6),
                id="sfs_update_members_job",
                name="SFS – aktualizacja subów co 6h",
                replace_existing=True,
                args=[self._sfs_update_members_job],
            )

            self.scheduler.start()
            self._is_running = True

            logger.info("Scheduler uruchomiony (publish co 1 min)")
        except Exception as e:
            logger.error(f"Błąd uruchomienia schedulera: {e}")
            raise

    def _run_async_job(self, coro_func):
        """Sync wrapper: uruchamia async job (coro_func) w pętli zdarzeń."""
        if self._loop is None:
            logger.error("Scheduler: brak event loop")
            return
        try:
            coro = coro_func()
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as e:
            logger.error(f"Scheduler: błąd uruchomienia joba: {e}")

    async def stop(self):
        """Zatrzymanie schedulera"""
        try:
            if self._is_running:
                self.scheduler.shutdown()
                self._is_running = False
                logger.info("Scheduler zatrzymany")
        except Exception as e:
            logger.error(f"Błąd zatrzymania schedulera: {e}")

    async def check_expired_subscriptions(self):
        """Sprawdzenie i zbanowanie wygasłych subskrypcji"""
        try:
            expired_subs = await SubscriptionManager.get_expired_subscriptions()

            if not expired_subs:
                return

            logger.info(f"Znaleziono {len(expired_subs)} wygasłych subskrypcji")

            from database.models import SettingsManager

            kicked_count = 0
            for subscription in expired_subs:
                try:
                    # Get owner specific premium channel
                    premium_channel_id = await SettingsManager.get_premium_channel_id(subscription.owner_id)
                    
                    if not premium_channel_id:
                        logger.warning(f"Brak kanału premium dla ownera {subscription.owner_id} - skip ban for {subscription.user_id}")
                        continue

                    # 1. BANOWANIE NA TELEGRAMIE
                    # Nie robimy unban_chat_member -> użytkownik zostaje na czarnej liście
                    await self.bot.ban_chat_member(
                        chat_id=premium_channel_id,
                        user_id=subscription.user_id
                    )

                    # 2. AKTUALIZACJA STATUSU W BAZIE -> BANNED
                    await SubscriptionManager.update_subscription_status(
                        subscription.user_id, subscription.channel_id, "banned"
                    )

                    # 3. POWIADOMIENIE ADMINA (OWNERA)
                    safe_name = html.escape(subscription.full_name)
                    safe_user = html.escape(subscription.username or "brak")

                    notification = (
                        f"🚫 <b>Auto-Ban: Subskrypcja wygasła</b>\n\n"
                        f"👤 <a href='tg://user?id={subscription.user_id}'>{safe_name}</a>\n"
                        f"🏷️ Username: @{safe_user}\n"
                        f"💎 Tier: {subscription.tier}\n"
                        f"📅 Wygasła: {subscription.end_date.strftime('%Y-%m-%d %H:%M')}"
                    )

                    await self.bot.send_message(
                        chat_id=subscription.owner_id,
                        text=notification,
                        parse_mode=ParseMode.HTML
                    )

                    # 4. POWIADOMIENIE UŻYTKOWNIKA
                    try:
                        expiry_message = (
                            f"⏰ <b>Twoja subskrypcja wygasła</b>\n\n"
                            f"Zostałeś usunięty z kanału.\n"
                            f"Aby odnowić dostęp, skontaktuj się z administratorem."
                        )
                        await self.bot.send_message(
                            chat_id=subscription.user_id,
                            text=expiry_message,
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        pass  # Często niemożliwe jeśli bot zbanowany

                    kicked_count += 1
                    await asyncio.sleep(1)  # Unikanie rate limitów

                except Exception as kick_error:
                    logger.error(f"Błąd banowania {subscription.user_id}: {kick_error}")
                    continue

            if kicked_count > 0:
                logger.info(f"Zbanowano {kicked_count} użytkowników")

        except Exception as e:
            logger.error(f"Błąd procedury auto-ban: {e}")

    async def publish_scheduled_posts(self):
        """Publikowanie zaplanowanych postów."""
        try:
            posts_to_publish = await PostManager.get_posts_to_publish()

            if not posts_to_publish:
                return

            logger.info(
                "Planer: sprawdzono terminy, do publikacji teraz: %d postów",
                len(posts_to_publish),
            )
            
            from database.models import SettingsManager

            published_count = 0
            for post in posts_to_publish:
                try:
                    # Kanał: z posta (planowanie; ID w Telegramie jest ujemne) lub fallback na premium ownera
                    channel_id = getattr(post, "channel_id", None)
                    if channel_id is not None:
                        channel_id = int(channel_id)
                    if not channel_id:
                        channel_id = await SettingsManager.get_premium_channel_id(post.owner_id)
                    if not channel_id:
                        logger.error(f"Brak kanału dla posta {post.post_id} (owner {post.owner_id})")
                        await PostManager.update_post_status(post.post_id, "failed")
                        continue
                    channel_id = int(channel_id)

                    post_data = {
                        "content_type": post.content_type,
                        "content": post.content,
                        "caption": post.caption,
                        "buttons": None
                    }
                    if post.buttons_json:
                        try:
                            import json
                            post_data["buttons"] = json.loads(post.buttons_json)
                        except json.JSONDecodeError:
                            logger.warning(f"Błędny JSON przycisków w poście {post.post_id}")

                    success = await send_post_to_channel(
                        self.bot, post_data, user_id=post.owner_id, channel_id=channel_id
                    )

                    if success:
                        await PostManager.update_post_status(post.post_id, "sent")
                        published_count += 1

                        channel_name = ""
                        try:
                            from database.models import ChannelManager
                            ch = await ChannelManager.get_channel(channel_id)
                            channel_name = ch.get("title", "") if ch else ""
                        except Exception:
                            pass
                        if not channel_name:
                            try:
                                chat = await self.bot.get_chat(channel_id)
                                channel_name = getattr(chat, "title", "") or ""
                            except Exception:
                                channel_name = "Kanał"

                        def _esc(s):
                            if not s:
                                return "—"
                            return str(s).replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")[:120]

                        content_preview = ""
                        if post.content_type == "text" and post.content:
                            content_preview = _esc(post.content[:100]) + ("…" if len(post.content) > 100 else "")
                        elif post.caption:
                            content_preview = _esc(post.caption[:100]) + ("…" if len(post.caption) > 100 else "")
                        else:
                            type_names = {"photo": "Zdjęcie", "video": "Wideo", "document": "Dokument", "sticker": "Sticker", "text": "Tekst"}
                            content_preview = type_names.get(post.content_type, post.content_type)

                        notification = (
                            "✅ **Post opublikowany**\n\n"
                            f"📢 **Kanał:** {_esc(channel_name)}\n"
                            f"📝 **Treść:** {content_preview}\n\n"
                            f"🕐 Zaplanowany na: {post.publish_date.strftime('%d.%m.%Y %H:%M')}"
                        )

                        await self.bot.send_message(
                            chat_id=post.owner_id,
                            text=notification,
                            parse_mode="Markdown",
                            disable_notification=True
                        )
                        logger.info(f"Opublikowano post {post.post_id} dla {post.owner_id}")

                    else:
                        await PostManager.update_post_status(post.post_id, "failed")
                        logger.error(f"Nie udało się opublikować posta {post.post_id}")

                    await asyncio.sleep(2)

                except Exception as publish_error:
                    logger.error(f"Błąd publikowania posta {post.post_id}: {publish_error}")
                    await PostManager.update_post_status(post.post_id, "failed")
                    continue

            if published_count > 0:
                logger.info(f"Opublikowano {published_count} postów")

        except Exception as e:
            logger.error(f"Błąd publikowania zaplanowanych postów: {e}")

    async def _sfs_update_members_job(self):
        """SFS – aktualizacja subów (members_count) co 6h."""
        await run_update_sfs_members_count(self.bot)

    async def sfs_daily_check(self):
        """SFS – sprawdzanie co 24h. Bez Telethon nie pobieramy wyświetleń z kanałów (Bot API nie ma historii)."""
        try:
            from database.models import SFSManager
            count = await SFSManager.count_listings()
            if count > 0:
                logger.info("SFS: sprawdzanie co 24h – %d wpisów (wyświetlenia tylko z forwardów użytkownika)", count)
        except Exception as e:
            logger.error(f"SFS daily check: {e}")

    def schedule_single_post(self, post_id: int, publish_date: datetime):
        """Zaplanowanie pojedynczego posta na konkretny czas"""
        try:
            job_id = f"single_post_{post_id}"

            self.scheduler.add_job(
                func=self.publish_single_post,
                trigger=DateTrigger(run_date=publish_date),
                args=[post_id],
                id=job_id,
                name=f"Publikacja posta {post_id}",
                replace_existing=True
            )

            logger.info(f"Zaplanowano post {post_id} na {publish_date}")

        except Exception as e:
            logger.error(f"Błąd planowania pojedynczego posta: {e}")

    async def publish_single_post(self, post_id: int):
        """Publikowanie pojedynczego posta"""
        try:
            # Symulacja pobrania posta (w rzeczywistości pobieramy z bazy)
            posts = await PostManager.get_posts_to_publish()
            post = next((p for p in posts if p.post_id == post_id), None)

            if not post:
                logger.warning(f"Post {post_id} nie znaleziony")
                return

            # Użycie istniejącej logiki publikacji
            await self.publish_scheduled_posts()

        except Exception as e:
            logger.error(f"Błąd publikacji pojedynczego posta {post_id}: {e}")

    def cancel_post_job(self, post_id: int):
        """Anulowanie zaplanowanego posta"""
        try:
            job_id = f"single_post_{post_id}"
            self.scheduler.remove_job(job_id)
            logger.info(f"Anulowano zadanie dla posta {post_id}")

        except JobLookupError:
            logger.warning(f"Zadanie dla posta {post_id} nie znalezione")
        except Exception as e:
            logger.error(f"Błąd anulowania zadania posta {post_id}: {e}")

    def get_scheduler_status(self) -> dict:
        """Pobranie statusu schedulera"""
        try:
            jobs = self.scheduler.get_jobs()
            return {
                "running": self._is_running,
                "job_count": len(jobs),
                "jobs": [
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run": job.next_run_time.isoformat() if job.next_run_time else None
                    }
                    for job in jobs
                ]
            }
        except Exception as e:
            logger.error(f"Błąd pobierania statusu schedulera: {e}")
            return {"running": False, "error": str(e)}


# Globalna instancja schedulera (będzie zainicjalizowana w bot.py)
bot_scheduler: Optional[BotScheduler] = None