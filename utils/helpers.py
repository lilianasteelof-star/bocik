"""
Funkcje pomocnicze dla bota
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger("helpers")


async def get_premium_channel_id(user_id: int) -> Optional[int]:
    """
    Pobranie ID kanału premium dla konkretnego użytkownika
    Priorytet: 1. Baza danych, 2. .env (legacy fallback)
    """
    try:
        from database.models import SettingsManager
        from config import settings
        
        # Najpierw sprawdzamy bazę danych
        db_channel_id = await SettingsManager.get_premium_channel_id(user_id)
        if db_channel_id:
            return db_channel_id
        
        # Jeśli nie ma w bazie, używamy .env (tylko jeśli user_id pasuje do ADMIN_ID?)
        # W modelu multi-user .env jest mniej ważny, ale dla admina może być fallbackiem
        if user_id == settings.ADMIN_ID:
            return settings.PREMIUM_CHANNEL_ID
            
        return None
    except Exception as e:
        logger.error(f"Błąd pobierania ID kanału premium: {e}")
        return None


async def get_free_channel_id(user_id: int) -> Optional[int]:
    """
    Pobranie ID kanału free dla konkretnego użytkownika
    Priorytet: 1. Baza danych, 2. .env
    """
    try:
        from database.models import SettingsManager
        from config import settings
        
        # Najpierw sprawdzamy bazę danych
        db_channel_id = await SettingsManager.get_free_channel_id(user_id)
        if db_channel_id:
            return db_channel_id
        
        # Fallback do .env
        if user_id == settings.ADMIN_ID:
            return settings.FREE_CHANNEL_ID
            
        return None
    except Exception as e:
        logger.error(f"Błąd pobierania ID kanału free: {e}")
        return None


def create_tier_keyboard(user_id: int = None, channel_id: int = None) -> InlineKeyboardMarkup:
    """Utworzenie klawiatury do wyboru kategorii subskrypcji"""
    if user_id and channel_id:
        # Format: tier_Tier_UserId_ChannelId
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🥉 Bronze", callback_data=f"tier_Bronze_{user_id}_{channel_id}"),
                InlineKeyboardButton(text="🥈 Silver", callback_data=f"tier_Silver_{user_id}_{channel_id}"),
            ],
            [
                InlineKeyboardButton(text="🥇 Gold", callback_data=f"tier_Gold_{user_id}_{channel_id}"),
            ]
        ])
    elif user_id:
        # Legacy / Fallback (bez channel_id - spróbujemy pobrać z kontekstu lub domyślny)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🥉 Bronze", callback_data=f"tier_Bronze_{user_id}"),
                InlineKeyboardButton(text="🥈 Silver", callback_data=f"tier_Silver_{user_id}"),
            ],
            [
                InlineKeyboardButton(text="🥇 Gold", callback_data=f"tier_Gold_{user_id}"),
            ]
        ])
    else:
        # Fallback całkowity
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🥉 Bronze", callback_data="tier_Bronze"),
                InlineKeyboardButton(text="🥈 Silver", callback_data="tier_Silver"),
            ],
            [
                InlineKeyboardButton(text="🥇 Gold", callback_data="tier_Gold"),
            ]
        ])
    return keyboard


def create_duration_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Utworzenie klawiatury do wyboru czasu trwania subskrypcji"""
    if user_id:
        # Jeśli mamy user_id, dodaj go do callback_data
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="7 dni", callback_data=f"duration_7_{user_id}"),
                InlineKeyboardButton(text="30 dni", callback_data=f"duration_30_{user_id}"),
            ],
            [
                InlineKeyboardButton(text="90 dni", callback_data=f"duration_90_{user_id}"),
                InlineKeyboardButton(text="🔄 Dożywotnio", callback_data=f"duration_lifetime_{user_id}"),
            ],
            [
                InlineKeyboardButton(text="📅 Niestandardowa data", callback_data=f"duration_custom_{user_id}"),
            ]
        ])
    else:
        # Fallback dla kompatybilności
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="7 dni", callback_data="duration_7"),
                InlineKeyboardButton(text="30 dni", callback_data="duration_30"),
            ],
            [
                InlineKeyboardButton(text="90 dni", callback_data="duration_90"),
                InlineKeyboardButton(text="🔄 Dożywotnio", callback_data="duration_lifetime"),
            ],
            [
                InlineKeyboardButton(text="📅 Niestandardowa data", callback_data="duration_custom"),
            ]
        ])
    return keyboard


def create_schedule_keyboard() -> InlineKeyboardMarkup:
    """Utworzenie klawiatury do wyboru czasu publikacji posta"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Wyślij teraz", callback_data="schedule_now"),
        ],
        [
            InlineKeyboardButton(text="⏰ Zaplanuj na później", callback_data="schedule_later"),
        ],
        [
            InlineKeyboardButton(text="❌ Anuluj", callback_data="schedule_cancel"),
        ]
    ])
    return keyboard


def create_buttons_keyboard() -> InlineKeyboardMarkup:
    """Utworzenie klawiatury do zarządzania przyciskami posta"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Dodaj przyciski", callback_data="buttons_add"),
        ],
        [
            InlineKeyboardButton(text="➡️ Pomiń przyciski", callback_data="buttons_skip"),
        ],
        [
            InlineKeyboardButton(text="❌ Anuluj", callback_data="buttons_cancel"),
        ]
    ])
    return keyboard


def parse_buttons_text(text: str) -> List[Dict[str, str]]:
    """
    Parsowanie tekstu przycisków w formacie: "Tekst - Link"
    Zwraca listę słowników z kluczami 'text' i 'url'
    """
    buttons = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Sprawdzenie formatu: "Tekst - Link"
        if ' - ' in line:
            parts = line.split(' - ', 1)
            if len(parts) == 2:
                text_part, url_part = parts
                text_part = text_part.strip()
                url_part = url_part.strip()
                
                # Podstawowa walidacja URL
                if url_part.startswith(('http://', 'https://', 't.me/')):
                    buttons.append({
                        'text': text_part,
                        'url': url_part
                    })
                else:
                    logger.warning(f"Nieprawidłowy URL: {url_part}")
            else:
                logger.warning(f"Nieprawidłowy format linii: {line}")
        else:
            logger.warning(f"Brak separatora ' - ' w linii: {line}")
    
    return buttons


def create_inline_keyboard_from_buttons(buttons: List[Dict[str, str]]) -> Optional[InlineKeyboardMarkup]:
    """Utworzenie InlineKeyboard z listy przycisków"""
    if not buttons:
        return None
    
    try:
        inline_buttons = []
        for button in buttons:
            inline_buttons.append([
                InlineKeyboardButton(
                    text=button['text'],
                    url=button['url']
                )
            ])
        
        return InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    except Exception as e:
        logger.error(f"Błąd tworzenia klawiatury z przycisków: {e}")
        return None


def parse_datetime_from_text(text: str) -> Optional[datetime]:
    """
    Parsowanie daty i czasu z tekstu
    Obsługuje formaty: YYYY-MM-DD HH:MM, DD.MM.YYYY HH:MM, DD/MM/YYYY HH:MM
    """
    formats = [
        "%Y-%m-%d %H:%M",  # YYYY-MM-DD HH:MM (priorytet)
        "%Y:%m:%d %H:%M",  # YYYY:MM:DD HH:MM (jak użytkownik napisał)
        "%d.%m.%Y %H:%M",  # DD.MM.YYYY HH:MM
        "%d/%m/%Y %H:%M",  # DD/MM/YYYY HH:MM
        "%d-%m-%Y %H:%M",  # DD-MM-YYYY HH:MM
    ]
    
    for fmt in formats:
        try:
            parsed_date = datetime.strptime(text.strip(), fmt)
            
            # Sprawdzenie czy data nie jest w przeszłości (z tolerancją 1 minuty)
            if parsed_date < datetime.now() - timedelta(minutes=1):
                logger.warning(f"Data {text} jest w przeszłości")
                return None
                
            return parsed_date
        except ValueError:
            continue
    
    logger.warning(f"Nie można sparsować daty: {text}")
    return None


def parse_end_date_from_text(text: str) -> Optional[datetime]:
    """
    Parsowanie daty zakończenia subskrypcji z tekstu
    Inteligentne parsowanie z walidacją i podpowiedziami
    """
    text = text.strip()
    
    formats = [
        # Formaty z godziną (priorytet)
        ("%Y-%m-%d %H:%M", "YYYY-MM-DD HH:MM"),  # 2026-03-15 18:30
        ("%Y:%m:%d %H:%M", "YYYY:MM:DD HH:MM"),  # 2026:03:15 18:30
        ("%d.%m.%Y %H:%M", "DD.MM.YYYY HH:MM"),  # 15.03.2026 18:30
        ("%d/%m/%Y %H:%M", "DD/MM/YYYY HH:MM"),  # 15/03/2026 18:30
        ("%d-%m-%Y %H:%M", "DD-MM-YYYY HH:MM"),  # 15-03-2026 18:30
        # Formaty bez godziny (koniec dnia)
        ("%Y-%m-%d", "YYYY-MM-DD"),              # 2026-03-15
        ("%d.%m.%Y", "DD.MM.YYYY"),              # 15.03.2026
        ("%d/%m/%Y", "DD/MM/YYYY"),              # 15/03/2026
    ]
    
    for fmt, fmt_name in formats:
        try:
            parsed_date = datetime.strptime(text, fmt)
            
            # Jeśli format bez godziny, ustaw na koniec dnia
            if ":" not in text:
                parsed_date = parsed_date.replace(hour=23, minute=59, second=59)
                logger.info(f"Parsowano datę bez godziny, ustawiono 23:59:59")
            
            logger.info(f"Sparsowano datę: {text} → {parsed_date} (format: {fmt_name})")
            return parsed_date
            
        except ValueError as e:
            continue
    
    # Jeśli nie udało się sparsować, loguj dokładny błąd
    logger.warning(f"Nie można sparsować daty: '{text}'")
    logger.warning(f"Obsługiwane formaty: YYYY-MM-DD HH:MM, YYYY:MM:DD HH:MM, DD.MM.YYYY HH:MM")
    return None


def format_subscription_info(
    user_id: int, 
    username: str, 
    full_name: str, 
    tier: str, 
    end_date: datetime
) -> str:
    """Formatowanie informacji o subskrypcji dla admina"""
    
    days_remaining = (end_date - datetime.now()).days
    status_emoji = "✅" if days_remaining > 0 else "⚠️"
    
    return (
        f"{status_emoji} **Subskrypcja utworzona**\n\n"
        f"👤 Użytkownik: [{full_name}](tg://user?id={user_id})\n"
        f"🏷️ Username: @{username}\n"
        f"💎 Kategoria: {tier}\n"
        f"📅 Wygasa: {end_date.strftime('%d.%m.%Y %H:%M')}\n"
        f"⏳ Pozostało dni: {days_remaining}"
    )


def format_user_join_notification(
    user_id: int, 
    username: str, 
    full_name: str,
    channel_type: str = "Premium"
) -> str:
    """Formatowanie powiadomienia o dołączeniu użytkownika"""
    
    if channel_type == "Free":
        link = f"tg://user?id={user_id}"
        uname = f"@{username}" if username and username != "brak" else f"[Napisz do leada]({link})"
        return (
            f"🔔 **Nowy lead** (Free Channel)\n\n"
            f"👤 [{full_name}]({link})\n"
            f"🏷️ {uname}\n\n"
            f"_Pisz, póki ciepły._"
        )
    else:
        return (
            f"👋 **Nowy użytkownik na Premium!**\n\n"
            f"👤 [{full_name}](tg://user?id={user_id}) "
            f"dołączył do kanału Premium\n"
            f"🏷️ Username: @{username if username else 'brak'}\n\n"
            f"⚙️ Wybierz kategorię i czas subskrypcji:"
        )


def format_kick_notification(user_id: int, username: str, full_name: str, reason: str = "wygaśnięcie subskrypcji") -> str:
    """Formatowanie powiadomienia o usunięciu użytkownika"""
    return (
        f"🚫 **Użytkownik usunięty**\n\n"
        f"👤 [{full_name}](tg://user?id={user_id})\n"
        f"🏷️ Username: @{username if username else 'brak'}\n"
        f"📝 Powód: {reason}"
    )


def get_tier_duration_from_callback(callback_data: str) -> Tuple[Optional[str], Optional[int]]:
    """Ekstraktowanie tier/duration z callback_data"""
    if callback_data.startswith("tier_"):
        return callback_data[5:], None
    elif callback_data.startswith("duration_"):
        duration_str = callback_data[9:]
        if duration_str == "lifetime":
            return None, 36500  # 100 lat jako "dożywotnio"
        else:
            try:
                return None, int(duration_str)
            except ValueError:
                return None, None
    return None, None


def validate_admin_command(text: str) -> bool:
    """Walidacja czy wiadomość to komenda dla admina"""
    admin_commands = [
        "/start", "/help", "/newpost", "/scheduled", 
        "/stats", "/users", "/kick"
    ]
    
    return any(text.startswith(cmd) for cmd in admin_commands)