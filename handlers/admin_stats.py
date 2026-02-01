"""
Handler do wyświetlania statystyk
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import settings
from database.models import SubscriptionManager, SettingsManager, ChannelManager
from utils.scheduler import BotScheduler

logger = logging.getLogger("handlers")
admin_stats_router = Router(name="admin_stats")


def _parse_first_lead_iso(iso_str):
    """Zwraca datetime z first_lead_iso lub None."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None

async def generate_stats_text(channel_id: int, title: str, channel_type: str, scheduler_status: dict) -> str:
    """Helper do generowania tekstu statystyk dla jednego kanału"""
    # Pobranie subskrypcji dla kanału
    subs = await SubscriptionManager.get_all_active_subscriptions(channel_id)
    
    tier_stats = {"Bronze": 0, "Silver": 0, "Gold": 0}
    for sub in subs:
        if sub.tier in tier_stats:
            tier_stats[sub.tier] += 1
            
    base_text = (
        f"📊 **RAPORT FINANSOWY: {title}** 📈\n\n"
        f"💰 **Subskrypcje:** {len(subs)}\n"
        f"🥉 Bronze: {tier_stats['Bronze']}\n"
        f"🥈 Silver: {tier_stats['Silver']}\n"
        f"🥇 Gold: {tier_stats['Gold']}"
    )

    if channel_type == 'free':
        stats = await SubscriptionManager.get_channel_leads_stats(channel_id)
        base_text += (
            f"\n\n📊 **STATYSTYKI DARMOWEGO KANAŁU**\n"
            f"Łącznie leadów od początku: {stats.get('total_all_time', 0)}\n"
            f"Nowe leady dziś: {stats['today']}\n"
            f"Nowe leady w ciągu 7 dni: {stats['week']}\n"
            f"Średnia leadów/dzień (od dodania bota): {stats['daily_avg']}"
        )
    
    return base_text

@admin_stats_router.callback_query(F.data == "general_stats")
async def handle_general_stats(callback: CallbackQuery, bot: Bot, scheduler: BotScheduler):
    """Callback dla ogólnych statystyk (to samo co /stats)"""
    try:
        # Wywołujemy tę samą logikę co w komendzie /stats, ale edytujemy wiadomość
        user_id = callback.from_user.id
        
        channels = await ChannelManager.get_user_channels(user_id)
        
        if not channels:
            await callback.answer("❌ Nie masz żadnych kanałów.", show_alert=True)
            return

        total_subs = 0
        global_tier_stats = {"Bronze": 0, "Silver": 0, "Gold": 0}
        count_premium = sum(1 for ch in channels if ch.get("type") == "premium")
        count_free = sum(1 for ch in channels if ch.get("type") == "free")
        
        # Free stats: leady + łączna liczba subów na free kanałach; średnia od pierwszego leada do teraz
        free_stats_total = {"today": 0, "week": 0, "daily_avg": 0.0, "total_all_time": 0}
        first_lead_dates = []
        free_channels_members_total = 0
        has_free_channels = False
        
        for ch in channels:
            subs = await SubscriptionManager.get_all_active_subscriptions(ch['channel_id'])
            total_subs += len(subs)
            for sub in subs:
                if sub.tier in global_tier_stats:
                    global_tier_stats[sub.tier] += 1
            
            if ch['type'] == 'free':
                has_free_channels = True
                f_stats = await SubscriptionManager.get_channel_leads_stats(ch['channel_id'])
                free_stats_total["today"] += f_stats["today"]
                free_stats_total["week"] += f_stats["week"]
                free_stats_total["total_all_time"] += f_stats.get("total_all_time", 0)
                fi = _parse_first_lead_iso(f_stats.get("first_lead_iso"))
                if fi:
                    first_lead_dates.append(fi)
                try:
                    free_channels_members_total += await bot.get_chat_member_count(chat_id=ch['channel_id'])
                except Exception as e:
                    logger.warning("get_chat_member_count free channel %s: %s", ch['channel_id'], e)
        
        # Średnia leadów/dzień = od pierwszego leada (dodania bota) do teraz
        if has_free_channels and free_stats_total["total_all_time"] and first_lead_dates:
            oldest = min(first_lead_dates)
            days_since = max(1, (datetime.now(timezone.utc) - oldest).days)
            free_stats_total["daily_avg"] = round(free_stats_total["total_all_time"] / days_since, 1)
        elif has_free_channels and free_stats_total["total_all_time"]:
            free_stats_total["daily_avg"] = round(free_stats_total["total_all_time"] / 1, 1)
        
        scheduler_status = scheduler.get_scheduler_status() if scheduler else {'running': False}
        
        stats_text = (
            f"🌍 **STATYSTYKI GLOBALNE** 🌍\n\n"
            f"🏰 **Twoje Kanały:** {len(channels)} (premium: {count_premium}, darmowe: {count_free})\n"
            f"💎 **Wszyscy Subskrybenci:** {total_subs}\n"
            f"🥉 Bronze: {global_tier_stats['Bronze']}\n"
            f"🥈 Silver: {global_tier_stats['Silver']}\n"
            f"🥇 Gold: {global_tier_stats['Gold']}"
        )
        if has_free_channels and free_channels_members_total >= 0:
            stats_text += f"\n👥 **Subskrybenci na darmowych kanałach:** {free_channels_members_total}"
        
        if has_free_channels:
            stats_text += (
                f"\n\n📊 **STATYSTYKI DARMOWYCH KANAŁÓW**\n"
                f"Łącznie leadów od początku: {free_stats_total['total_all_time']}\n"
                f"Nowe leady dziś: {free_stats_total['today']}\n"
                f"Nowe leady w ciągu 7 dni: {free_stats_total['week']}\n"
                f"Średnia leadów/dzień (od dodania bota): {free_stats_total['daily_avg']}"
            )
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                 InlineKeyboardButton(text="🔙 POWRÓT DO BAZY", callback_data="refresh_channels")
            ]])
        )
    except Exception as e:
        logger.error(f"Błąd general_stats: {e}")
        await callback.answer("Błąd statystyk")

@admin_stats_router.callback_query(F.data.startswith("channel_stats_"))
async def handle_channel_stats(callback: CallbackQuery, scheduler: BotScheduler):
    """Callback dla statystyk konkretnego kanału"""
    try:
        channel_id = int(callback.data.split("_")[-1])
        channel = await ChannelManager.get_channel(channel_id)
        
        if not channel:
            await callback.answer("❌ Kanał nie istnieje")
            return
            
        scheduler_status = scheduler.get_scheduler_status() if scheduler else {'running': False}
        stats_text = await generate_stats_text(channel_id, channel['title'], channel['type'], scheduler_status)
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 POWRÓT", callback_data=f"manage_channel_{channel_id}")
            ]])
        )
    except Exception as e:
        logger.error(f"Błąd channel_stats: {e}")
        await callback.answer("Błąd statystyk")

async def send_channel_stats(message: Message, channel_id: int, scheduler: BotScheduler):
    """Pomocnicza funkcja do wysyłania statystyk kanału (dla skrótów)"""
    try:
        channel = await ChannelManager.get_channel(channel_id)
        if not channel:
            await message.reply("❌ Kanał nie istnieje.")
            return

        scheduler_status = scheduler.get_scheduler_status() if scheduler else {'running': False}
        stats_text = await generate_stats_text(channel_id, channel['title'], channel['type'], scheduler_status)
        
        await message.reply(stats_text)
    except Exception as e:
        logger.error(f"Błąd send_channel_stats: {e}")
        await message.reply("❌ Błąd pobierania statystyk.")

@admin_stats_router.message(Command("stats"))
async def cmd_stats(message: Message, bot: Bot, scheduler: BotScheduler):
    """Statystyki bota (Globalne - sumaryczne)"""
    user_id = message.from_user.id
    
    try:
        channels = await ChannelManager.get_user_channels(user_id)
        
        if not channels:
            await message.reply("❌ Nie masz żadnych kanałów.")
            return

        total_subs = 0
        global_tier_stats = {"Bronze": 0, "Silver": 0, "Gold": 0}
        count_premium = sum(1 for ch in channels if ch.get("type") == "premium")
        count_free = sum(1 for ch in channels if ch.get("type") == "free")
        
        # Free stats: leady + łączna liczba subów na free kanałach; średnia od pierwszego leada do teraz
        free_stats_total = {"today": 0, "week": 0, "daily_avg": 0.0, "total_all_time": 0}
        first_lead_dates = []
        free_channels_members_total = 0
        has_free_channels = False
        
        for ch in channels:
            subs = await SubscriptionManager.get_all_active_subscriptions(ch['channel_id'])
            total_subs += len(subs)
            for sub in subs:
                if sub.tier in global_tier_stats:
                    global_tier_stats[sub.tier] += 1
            
            if ch['type'] == 'free':
                has_free_channels = True
                f_stats = await SubscriptionManager.get_channel_leads_stats(ch['channel_id'])
                free_stats_total["today"] += f_stats["today"]
                free_stats_total["week"] += f_stats["week"]
                free_stats_total["total_all_time"] += f_stats.get("total_all_time", 0)
                fi = _parse_first_lead_iso(f_stats.get("first_lead_iso"))
                if fi:
                    first_lead_dates.append(fi)
                try:
                    free_channels_members_total += await bot.get_chat_member_count(chat_id=ch['channel_id'])
                except Exception as e:
                    logger.warning("get_chat_member_count free channel %s: %s", ch['channel_id'], e)

        # Średnia leadów/dzień = od pierwszego leada (dodania bota) do teraz
        if has_free_channels and free_stats_total["total_all_time"] and first_lead_dates:
            oldest = min(first_lead_dates)
            days_since = max(1, (datetime.now(timezone.utc) - oldest).days)
            free_stats_total["daily_avg"] = round(free_stats_total["total_all_time"] / days_since, 1)
        elif has_free_channels and free_stats_total["total_all_time"]:
            free_stats_total["daily_avg"] = round(free_stats_total["total_all_time"] / 1, 1)
        
        scheduler_status = scheduler.get_scheduler_status() if scheduler else {'running': False}
        
        stats_text = (
            f"🌍 **STATYSTYKI GLOBALNE** 🌍\n\n"
            f"🏰 **Twoje Kanały:** {len(channels)} (premium: {count_premium}, darmowe: {count_free})\n"
            f"💎 **Wszyscy Subskrybenci:** {total_subs}\n"
            f"🥉 Bronze: {global_tier_stats['Bronze']}\n"
            f"🥈 Silver: {global_tier_stats['Silver']}\n"
            f"🥇 Gold: {global_tier_stats['Gold']}"
        )
        if has_free_channels and free_channels_members_total >= 0:
            stats_text += f"\n👥 **Subskrybenci na darmowych kanałach:** {free_channels_members_total}"
        
        if has_free_channels:
            stats_text += (
                f"\n\n📊 **STATYSTYKI DARMOWYCH KANAŁÓW**\n"
                f"Łącznie leadów od początku: {free_stats_total['total_all_time']}\n"
                f"Nowe leady dziś: {free_stats_total['today']}\n"
                f"Nowe leady w ciągu 7 dni: {free_stats_total['week']}\n"
                f"Średnia leadów/dzień (od dodania bota): {free_stats_total['daily_avg']}"
            )
        
        await message.reply(
            stats_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Menu główne", callback_data="refresh_channels")
            ]])
        )

    except Exception as e:
        logger.error(f"Błąd global stats: {e}")
        await message.reply("❌ Błąd pobierania statystyk")
