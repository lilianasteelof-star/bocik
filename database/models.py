"""
Modele danych i operacje CRUD dla bazy danych
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from .connection import db_manager, USE_POSTGRES

logger = logging.getLogger("database")


def _row_datetime(value):
    """Z wartości z wiersza (datetime lub string) zwraca datetime. Dla PostgreSQL asyncpg zwraca datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace(" ", "T", 1)[:19])
    except (ValueError, TypeError):
        return None


class ChannelManager:
    """Menedżer kanałów użytkownika"""

    @staticmethod
    async def get_user_channels(user_id: int) -> List[Dict[str, Any]]:
        """Pobranie kanałów, których właścicielem jest user_id"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute(
                "SELECT * FROM channels WHERE owner_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            logger.error(f"Błąd pobierania kanałów użytkownika {user_id}: {e}")
            return []

    @staticmethod
    async def get_channel(channel_id: int) -> Optional[Dict[str, Any]]:
        """Pobranie szczegółów kanału"""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Błąd pobierania kanału {channel_id}: {e}")
            return None

    @staticmethod
    async def create_channel(owner_id: int, channel_id: int, title: str, type: str = "premium") -> bool:
        """Dodanie nowego kanału"""
        try:
            connection = await db_manager.get_connection()
            
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO channels (channel_id, owner_id, title, type)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (channel_id) DO UPDATE SET owner_id = EXCLUDED.owner_id, title = EXCLUDED.title, type = EXCLUDED.type
                """, (channel_id, owner_id, title, type)): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO channels (channel_id, owner_id, title, type)
                    VALUES (?, ?, ?, ?)
                """, (channel_id, owner_id, title, type)): pass
            
            await connection.commit()
            logger.info(f"Dodano kanał {title} ({channel_id}) dla {owner_id}")
            return True
            
        except Exception as e:
            logger.error(f"Błąd tworzenia kanału: {e}")
            return False

    @staticmethod
    async def is_owner(user_id: int, channel_id: int) -> bool:
        """Sprawdzenie czy użytkownik jest właścicielem kanału"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute(
                "SELECT 1 FROM channels WHERE channel_id = ? AND owner_id = ?", 
                (channel_id, user_id)
            ) as cursor:
                return await cursor.fetchone() is not None
                
        except Exception as e:
            logger.error(f"Błąd sprawdzania właściciela: {e}")
            return False

    @staticmethod
    async def get_all_channels(page: int = 0, per_page: int = 10, type_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pobranie wszystkich kanałów z paginacją (super-admin). type_filter: None, 'premium', 'free'."""
        try:
            connection = await db_manager.get_connection()
            offset = page * per_page
            if type_filter:
                async with connection.execute(
                    "SELECT * FROM channels WHERE type = ? ORDER BY channel_id LIMIT ? OFFSET ?",
                    (type_filter, per_page, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with connection.execute(
                    "SELECT * FROM channels ORDER BY channel_id LIMIT ? OFFSET ?",
                    (per_page, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Błąd get_all_channels: {e}")
            return []

    @staticmethod
    async def count_all_channels(type_filter: Optional[str] = None) -> int:
        """Liczba wszystkich kanałów (super-admin). type_filter: None, 'premium', 'free'."""
        try:
            connection = await db_manager.get_connection()
            if type_filter:
                async with connection.execute(
                    "SELECT COUNT(*) FROM channels WHERE type = ?", (type_filter,)
                ) as cursor:
                    row = await cursor.fetchone()
            else:
                async with connection.execute("SELECT COUNT(*) FROM channels") as cursor:
                    row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Błąd count_all_channels: {e}")
            return 0


class GlobalBlacklist:
    """Globalna czarna lista użytkowników (super-admin)."""

    @staticmethod
    async def add(user_id: int, reason: Optional[str] = None) -> bool:
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now_param = now_dt if USE_POSTGRES else now_dt.isoformat()
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO global_blacklist (user_id, reason, created_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason, created_at = EXCLUDED.created_at
                """, (user_id, reason or "", now_param)): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO global_blacklist (user_id, reason, created_at)
                    VALUES (?, ?, ?)
                """, (user_id, reason or "", now_param)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd global_blacklist add: {e}")
            return False

    @staticmethod
    async def remove(user_id: int) -> bool:
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("DELETE FROM global_blacklist WHERE user_id = ?", (user_id,)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd global_blacklist remove: {e}")
            return False

    @staticmethod
    async def is_banned(user_id: int) -> bool:
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT 1 FROM global_blacklist WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Błąd global_blacklist is_banned: {e}")
            return False

    @staticmethod
    async def get_all(page: int = 0, per_page: int = 20) -> List[Dict[str, Any]]:
        try:
            connection = await db_manager.get_connection()
            offset = page * per_page
            async with connection.execute(
                "SELECT * FROM global_blacklist ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, offset)
            ) as cursor:
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Błąd global_blacklist get_all: {e}")
            return []

    @staticmethod
    async def count() -> int:
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT COUNT(*) FROM global_blacklist") as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Błąd global_blacklist count: {e}")
            return 0


class BotUsersManager:
    """Użytkownicy bota (np. /start) – do broadcastu do użytkowników bota."""

    @staticmethod
    async def ensure_user(user_id: int) -> bool:
        """Dodaj user_id do bot_users jeśli nie ma (np. przy /start)."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now_param = now_dt if USE_POSTGRES else now_dt.isoformat()
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO bot_users (user_id, first_seen) VALUES ($1, $2)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_id, now_param)): pass
            else:
                async with connection.execute(
                    "INSERT OR IGNORE INTO bot_users (user_id, first_seen) VALUES (?, ?)",
                    (user_id, now_param),
                ): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd bot_users ensure: {e}")
            return False

    @staticmethod
    async def get_all_user_ids() -> List[int]:
        """Wszyscy user_id z bot_users (do broadcastu)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT user_id FROM bot_users") as cursor:
                rows = await cursor.fetchall()
            return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error(f"Błąd bot_users get_all: {e}")
            return []

    @staticmethod
    async def update_user_display_info(
        user_id: int, username: Optional[str] = None, full_name: Optional[str] = None
    ) -> bool:
        """Aktualizacja last_username i last_full_name (wyświetlanie w panelu aktywni użytkownicy)."""
        try:
            connection = await db_manager.get_connection()
            if USE_POSTGRES:
                async with connection.execute("""
                    UPDATE bot_users SET last_username = COALESCE($1, last_username), last_full_name = COALESCE($2, last_full_name)
                    WHERE user_id = $3
                """, (username or None, full_name or None, user_id)): pass
            else:
                async with connection.execute("""
                    UPDATE bot_users SET last_username = COALESCE(?, last_username), last_full_name = COALESCE(?, last_full_name)
                    WHERE user_id = ?
                """, (username or None, full_name or None, user_id)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd update_user_display_info: {e}")
            return False

    @staticmethod
    async def get_users_with_activity(page: int = 0, per_page: int = 15) -> List[Dict[str, Any]]:
        """Użytkownicy, którzy mają interakcje z botem (otwarty chat), posortowani po ostatniej aktywności."""
        try:
            connection = await db_manager.get_connection()
            offset = page * per_page
            if USE_POSTGRES:
                sql = """
                    SELECT u.user_id, u.last_username, u.last_full_name, MAX(l.created_at) AS last_activity
                    FROM bot_users u
                    INNER JOIN user_interaction_logs l ON l.user_id = u.user_id
                    GROUP BY u.user_id, u.last_username, u.last_full_name
                    ORDER BY last_activity DESC
                    LIMIT $1 OFFSET $2
                """
                async with connection.execute(sql, (per_page, offset)) as cursor:
                    rows = await cursor.fetchall()
            else:
                sql = """
                    SELECT u.user_id, u.last_username, u.last_full_name, MAX(l.created_at) AS last_activity
                    FROM bot_users u
                    INNER JOIN user_interaction_logs l ON l.user_id = u.user_id
                    GROUP BY u.user_id, u.last_username, u.last_full_name
                    ORDER BY last_activity DESC
                    LIMIT ? OFFSET ?
                """
                async with connection.execute(sql, (per_page, offset)) as cursor:
                    rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Błąd get_users_with_activity: {e}")
            return []

    @staticmethod
    async def count_users_with_activity() -> int:
        """Liczba użytkowników mających co najmniej jedną interakcję."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_interaction_logs"
            ) as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Błąd count_users_with_activity: {e}")
            return 0


class UserInteractionLog:
    """Logi interakcji użytkowników z botem (dla panelu super-admina)."""

    @staticmethod
    async def add(user_id: int, event_type: str, content_preview: Optional[str] = None) -> bool:
        try:
            connection = await db_manager.get_connection()
            prev = (content_preview or "")[:500]
            now_dt = datetime.now()
            now_param = now_dt if USE_POSTGRES else now_dt.isoformat()
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO user_interaction_logs (user_id, event_type, content_preview, created_at)
                    VALUES ($1, $2, $3, $4)
                """, (user_id, event_type, prev, now_param)): pass
            else:
                async with connection.execute("""
                    INSERT INTO user_interaction_logs (user_id, event_type, content_preview, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, event_type, prev, now_param)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd user_interaction_log add: {e}")
            return False

    @staticmethod
    async def get_last_for_user(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            connection = await db_manager.get_connection()
            if USE_POSTGRES:
                async with connection.execute("""
                    SELECT id, user_id, event_type, content_preview, created_at
                    FROM user_interaction_logs
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                """, (user_id, limit)) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with connection.execute("""
                    SELECT id, user_id, event_type, content_preview, created_at
                    FROM user_interaction_logs
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, limit)) as cursor:
                    rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Błąd get_last_for_user: {e}")
            return []


class InboxMuted:
    """Wyciszeni użytkownicy – admin nie dostaje powiadomień z inbox od tych userów."""

    @staticmethod
    async def is_muted(user_id: int) -> bool:
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT 1 FROM inbox_muted WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Błąd inbox_muted is_muted: {e}")
            return False

    @staticmethod
    async def add(user_id: int) -> bool:
        try:
            connection = await db_manager.get_connection()
            if USE_POSTGRES:
                async with connection.execute(
                    "INSERT INTO inbox_muted (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                ): pass
            else:
                async with connection.execute("INSERT OR IGNORE INTO inbox_muted (user_id) VALUES (?)", (user_id,)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd inbox_muted add: {e}")
            return False

    @staticmethod
    async def remove(user_id: int) -> bool:
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("DELETE FROM inbox_muted WHERE user_id = ?", (user_id,)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"Błąd inbox_muted remove: {e}")
            return False


class SettingsManager:
    """Menedżer ustawień bota w bazie danych"""
    
    @staticmethod
    async def get_setting(key: str, user_id: int) -> Optional[str]:
        """Pobranie wartości ustawienia dla konkretnego użytkownika"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute(
                "SELECT setting_value FROM bot_settings WHERE user_id = ? AND setting_key = ?", 
                (user_id, key)
            ) as cursor:
                row = await cursor.fetchone()
                
            return row["setting_value"] if row else None
            
        except Exception as e:
            logger.error(f"Błąd pobierania ustawienia {key} dla {user_id}: {e}")
            return None

    @staticmethod
    async def get_all_settings_for_key(key: str) -> List[Dict[str, Any]]:
        """Pobranie wszystkich ustawień danego typu (dla wszystkich userów)"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute(
                "SELECT user_id, setting_value FROM bot_settings WHERE setting_key = ?", 
                (key,)
            ) as cursor:
                rows = await cursor.fetchall()
                
            return [{"user_id": row["user_id"], "value": row["setting_value"]} for row in rows]
            
        except Exception as e:
            logger.error(f"Błąd pobierania wszystkich ustawień {key}: {e}")
            return []

    # ... update_subscription_details remains mostly same but might need context ...

    @staticmethod
    async def set_setting(key: str, value: str, user_id: int) -> bool:
        """Ustawienie wartości dla użytkownika"""
        try:
            connection = await db_manager.get_connection()
            
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO bot_settings (user_id, setting_key, setting_value, updated_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = EXCLUDED.updated_at
                """, (user_id, key, value, datetime.now())): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO bot_settings (user_id, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, key, value, datetime.now())): pass
            
            await connection.commit()
            
            logger.info(f"Zaktualizowano ustawienie {key} dla {user_id}: {value}")
            return True
            
        except Exception as e:
            logger.error(f"Błąd ustawiania {key}: {e}")
            return False
    
    @staticmethod
    async def get_premium_channel_id(user_id: int) -> Optional[int]:
        """Pobranie ID kanału premium (Settings -> Channels table fallback)"""
        # 1. Sprawdź czy jest ustawiony konkretny ID w ustawieniach
        value = await SettingsManager.get_setting("premium_channel_id", user_id)
        if value:
            return int(value)
            
        # 2. Fallback: Pobierz pierwszy kanał typu 'premium' z tabeli channels
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT channel_id FROM channels WHERE owner_id = ? AND type = 'premium' LIMIT 1",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["channel_id"] if row else None
        except Exception as e:
            logger.error(f"Błąd fallback premium channel: {e}")
            return None
    
    @staticmethod
    async def get_free_channel_id(user_id: int) -> Optional[int]:
        """Pobranie ID kanału free (Settings -> Channels table fallback)"""
        # 1. Sprawdź czy jest ustawiony konkretny ID w ustawieniach
        value = await SettingsManager.get_setting("free_channel_id", user_id)
        if value:
            return int(value)
            
        # 2. Fallback: Pobierz pierwszy kanał typu 'free' z tabeli channels
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT channel_id FROM channels WHERE owner_id = ? AND type = 'free' LIMIT 1",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["channel_id"] if row else None
        except Exception as e:
            logger.error(f"Błąd fallback free channel: {e}")
            return None
    
    @staticmethod
    async def set_premium_channel_id(user_id: int, channel_id: int) -> bool:
        """Ustawienie ID kanału premium"""
        return await SettingsManager.set_setting("premium_channel_id", str(channel_id), user_id)
    
    @staticmethod
    async def set_free_channel_id(user_id: int, channel_id: int) -> bool:
        """Ustawienie ID kanału free"""
        return await SettingsManager.set_setting("free_channel_id", str(channel_id), user_id)

    # Limit zaplanowanych postów na użytkownika (domyślnie 10)
    DEFAULT_MAX_SCHEDULED_POSTS = 10

    @staticmethod
    async def get_max_scheduled_posts(user_id: int) -> int:
        """Pobranie limitu zaplanowanych postów dla użytkownika (domyślnie 10)."""
        value = await SettingsManager.get_setting("max_scheduled_posts", user_id)
        if value is None:
            return SettingsManager.DEFAULT_MAX_SCHEDULED_POSTS
        try:
            n = int(value)
            return max(1, min(n, 500))
        except ValueError:
            return SettingsManager.DEFAULT_MAX_SCHEDULED_POSTS

    @staticmethod
    async def set_max_scheduled_posts(user_id: int, limit: int) -> bool:
        """Ustawienie limitu zaplanowanych postów (1–500)."""
        limit = max(1, min(500, int(limit)))
        return await SettingsManager.set_setting("max_scheduled_posts", str(limit), user_id)

    @staticmethod
    async def get_maintenance_mode() -> bool:
        """Tryb konserwacji (user_id=0, klucz maintenance_mode)."""
        val = await SettingsManager.get_setting("maintenance_mode", 0)
        return (val or "").lower() in ("true", "1", "yes")

    @staticmethod
    async def set_maintenance_mode(enabled: bool) -> bool:
        """Włączenie/wyłączenie trybu konserwacji."""
        return await SettingsManager.set_setting("maintenance_mode", "true" if enabled else "false", 0)


@dataclass
class Subscription:
    """Model subskrypcji"""
    user_id: int
    owner_id: int
    channel_id: int  # Added channel_id
    username: str
    full_name: str
    start_date: datetime
    end_date: datetime
    tier: str
    status: str = "active"
    created_at: Optional[datetime] = None


@dataclass
class ScheduledPost:
    """Model zaplanowanego posta"""
    owner_id: int
    channel_id: int
    content_type: str
    content: str
    publish_date: datetime
    post_id: Optional[int] = None
    caption: Optional[str] = None
    buttons_json: Optional[str] = None
    status: str = "pending"
    created_at: Optional[datetime] = None


class SubscriptionManager:
    """Menedżer operacji na subskrypcjach"""
    
    @staticmethod
    async def create_subscription(
        user_id: int,
        owner_id: int,
        channel_id: int,
        username: str,
        full_name: str,
        tier: str,
        duration_days: int = None,
        end_date: datetime = None
    ) -> bool:
        """
        Utworzenie nowej subskrypcji (przypisanej do kanału)
        """
        try:
            connection = await db_manager.get_connection()
            
            start_date = datetime.now()
            
            # Obliczenie end_date
            if end_date is not None:
                final_end_date = end_date
            elif duration_days is not None:
                final_end_date = start_date + timedelta(days=duration_days)
            else:
                logger.error("Ani duration_days ani end_date nie zostały podane!")
                return False
            
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO subscriptions 
                    (user_id, owner_id, channel_id, username, full_name, start_date, end_date, tier, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active')
                    ON CONFLICT (user_id, channel_id) DO UPDATE SET owner_id = EXCLUDED.owner_id, username = EXCLUDED.username, full_name = EXCLUDED.full_name, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, tier = EXCLUDED.tier, status = 'active'
                """, (user_id, owner_id, channel_id, username, full_name, start_date, final_end_date, tier)): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO subscriptions 
                    (user_id, owner_id, channel_id, username, full_name, start_date, end_date, tier, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """, (user_id, owner_id, channel_id, username, full_name, start_date, final_end_date, tier)): pass
            
            await connection.commit()
            
            logger.info(
                f"Utworzono subskrypcję dla {user_id} w kanale {channel_id} "
                f"({username}) - {tier}, wygasa: {final_end_date.strftime('%Y-%m-%d %H:%M')}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Błąd tworzenia subskrypcji: {e}")
            return False
    
    @staticmethod
    async def get_subscription(user_id: int, channel_id: int) -> Optional[Subscription]:
        """Pobranie subskrypcji użytkownika dla danego kanału"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND channel_id = ?", (user_id, channel_id)
            ) as cursor:
                row = await cursor.fetchone()
                
            if row:
                return Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                )
            return None
            
        except Exception as e:
            logger.error(f"Błąd pobierania subskrypcji: {e}")
            return None

    @staticmethod
    async def get_subscription_by_username(username: str, channel_id: int) -> Optional[Subscription]:
        """Pobranie subskrypcji po nazwie użytkownika (dla danego kanału)"""
        try:
            connection = await db_manager.get_connection()

            clean_username = username.replace("@", "").strip()

            async with connection.execute(
                    "SELECT * FROM subscriptions WHERE LOWER(username) = LOWER(?) AND channel_id = ?",
                    (clean_username, channel_id)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                return Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                )
            return None

        except Exception as e:
            logger.error(f"Błąd wyszukiwania po username {username}: {e}")
            return None

    @staticmethod
    async def get_subscription_for_owner(user_id: int, owner_id: int) -> Optional[Subscription]:
        """Pobranie dowolnej subskrypcji użytkownika należącej do danego ownera (dla /edit po ID)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND owner_id = ? LIMIT 1",
                (user_id, owner_id)
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                return Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                )
            return None
        except Exception as e:
            logger.error(f"Błąd get_subscription_for_owner: {e}")
            return None

    @staticmethod
    async def get_subscription_by_username_for_owner(username: str, owner_id: int) -> Optional[Subscription]:
        """Pobranie subskrypcji po @username w dowolnym kanale ownera (dla /edit @user)."""
        try:
            connection = await db_manager.get_connection()
            clean_username = username.replace("@", "").strip()
            async with connection.execute(
                "SELECT * FROM subscriptions WHERE LOWER(username) = LOWER(?) AND owner_id = ? LIMIT 1",
                (clean_username, owner_id)
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                return Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                )
            return None
        except Exception as e:
            logger.error(f"Błąd get_subscription_by_username_for_owner: {e}")
            return None

    @staticmethod
    async def get_expired_subscriptions() -> List[Subscription]:
        """Pobranie wygasłych subskrypcji"""
        try:
            connection = await db_manager.get_connection()
            
            now = datetime.now()
            now_str = now.isoformat()
            logger.info(f"Sprawdzam wygasłe subskrypcje, teraz: {now_str}")
            # PostgreSQL/asyncpg wymaga datetime, SQLite przyjmuje string
            now_param = now if USE_POSTGRES else now_str
            async with connection.execute("""
                SELECT * FROM subscriptions 
                WHERE status = 'active' AND end_date <= ?
            """, (now_param,)) as cursor:
                rows = await cursor.fetchall()
            
            logger.info(f"Zapytanie SQL zwróciło {len(rows)} wygasłych subskrypcji")
            
            subscriptions = []
            for row in rows:
                subscriptions.append(Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                ))
            
            return subscriptions
            
        except Exception as e:
            logger.error(f"Błąd pobierania wygasłych subskrypcji: {e}")
            return []
    
    @staticmethod
    async def get_channel_leads_stats(channel_id: int) -> Dict[str, Any]:
        """Pobiera statystyki leadów dla kanału (Free). Średnia leadów/dzień = od pierwszego leada (dodania bota) do teraz."""
        try:
            connection = await db_manager.get_connection()
            
            # Nowe leady dziś
            if USE_POSTGRES:
                async with connection.execute("""
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE channel_id = $1 AND tier = 'free' 
                    AND date(created_at) = CURRENT_DATE
                """, (channel_id,)) as cursor:
                    today_leads = (await cursor.fetchone())[0]
                async with connection.execute("""
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE channel_id = $1 AND tier = 'free' 
                    AND date(created_at) >= CURRENT_DATE - INTERVAL '7 days'
                """, (channel_id,)) as cursor:
                    week_leads = (await cursor.fetchone())[0]
            else:
                async with connection.execute("""
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE channel_id = ? AND tier = 'free' 
                    AND date(created_at) = date('now')
                """, (channel_id,)) as cursor:
                    today_leads = (await cursor.fetchone())[0]
                async with connection.execute("""
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE channel_id = ? AND tier = 'free' 
                    AND date(created_at) >= date('now', '-7 days')
                """, (channel_id,)) as cursor:
                    week_leads = (await cursor.fetchone())[0]

            # Łącznie leadów od początku + data pierwszego leada (odkąd bot w kanale)
            async with connection.execute("""
                SELECT COUNT(*), MIN(created_at) FROM subscriptions 
                WHERE channel_id = ? AND tier = 'free'
            """, (channel_id,)) as cursor:
                row = await cursor.fetchone()
            total_all_time = row[0] or 0
            first_lead_str = row[1]  # ISO string or None

            # Średnia leadów/dzień = od pierwszego leada do teraz (nie ostatnie 7 dni)
            first_lead_iso = None
            if total_all_time and first_lead_str:
                try:
                    first_dt = datetime.fromisoformat(first_lead_str.replace("Z", "+00:00"))
                    if first_dt.tzinfo is None:
                        first_dt = first_dt.replace(tzinfo=timezone.utc)
                    first_lead_iso = first_dt.isoformat()
                    now = datetime.now(timezone.utc)
                    days_since = max(1, (now - first_dt).days)
                    daily_avg = round(total_all_time / days_since, 1)
                except (ValueError, TypeError):
                    daily_avg = round(total_all_time / 1, 1)
            else:
                daily_avg = 0.0
                
            return {
                "today": today_leads,
                "week": week_leads,
                "daily_avg": daily_avg,
                "total_all_time": total_all_time,
                "first_lead_iso": first_lead_iso,
            }
        except Exception as e:
            logger.error(f"Błąd statystyk leadów: {e}")
            return {"today": 0, "week": 0, "daily_avg": 0.0, "total_all_time": 0, "first_lead_iso": None}

    @staticmethod
    async def update_subscription_status(user_id: int, channel_id: int, status: str) -> bool:
        """Aktualizacja statusu subskrypcji"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute("""
                UPDATE subscriptions 
                SET status = ? 
                WHERE user_id = ? AND channel_id = ?
            """, (status, user_id, channel_id)): pass
            await connection.commit()
            
            logger.info(f"Zaktualizowano status subskrypcji {user_id} w kanale {channel_id}: {status}")
            return True
            
        except Exception as e:
            logger.error(f"Błąd aktualizacji statusu subskrypcji: {e}")
            return False
    

    @staticmethod
    async def get_all_active_subscriptions(channel_id: int) -> List[Subscription]:
        """Pobranie wszystkich aktywnych subskrypcji dla danego KANAŁU"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute("""
                SELECT * FROM subscriptions 
                WHERE status = 'active' AND channel_id = ?
                ORDER BY end_date ASC
            """, (channel_id,)) as cursor:
                rows = await cursor.fetchall()
            
            subscriptions = []
            for row in rows:
                subscriptions.append(Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                ))
            
            return subscriptions
            
        except Exception as e:
            logger.error(f"Błąd pobierania aktywnych subskrypcji dla kanału {channel_id}: {e}")
            return []

    @staticmethod
    async def get_banned_subscriptions(channel_id: int) -> List[Subscription]:
        """Pobranie wszystkich ZBANOWANYCH subskrypcji dla danego KANAŁU"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute("""
                SELECT * FROM subscriptions 
                WHERE status = 'banned' AND channel_id = ?
                ORDER BY end_date ASC
            """, (channel_id,)) as cursor:
                rows = await cursor.fetchall()
            
            subscriptions = []
            for row in rows:
                subscriptions.append(Subscription(
                    user_id=row["user_id"],
                    owner_id=row["owner_id"],
                    channel_id=row["channel_id"],
                    username=row["username"],
                    full_name=row["full_name"],
                    start_date=_row_datetime(row["start_date"]),
                    end_date=_row_datetime(row["end_date"]),
                    tier=row["tier"],
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                ))
            
            return subscriptions
            
        except Exception as e:
            logger.error(f"Błąd pobierania zbanowanych subskrypcji dla kanału {channel_id}: {e}")
            return []

    @staticmethod
    async def get_all_subscriptions_paginated(
        channel_id: Optional[int] = None, page: int = 0, per_page: int = 20
    ) -> List[Dict[str, Any]]:
        """Pobranie subskrypcji z paginacją (super-admin). channel_id=None = wszystkie."""
        try:
            connection = await db_manager.get_connection()
            offset = page * per_page
            if channel_id is not None:
                async with connection.execute(
                    """SELECT * FROM subscriptions WHERE channel_id = ?
                       ORDER BY end_date DESC LIMIT ? OFFSET ?""",
                    (channel_id, per_page, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with connection.execute(
                    """SELECT * FROM subscriptions ORDER BY end_date DESC LIMIT ? OFFSET ?""",
                    (per_page, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Błąd get_all_subscriptions_paginated: {e}")
            return []

    @staticmethod
    async def count_subscriptions(channel_id: Optional[int] = None) -> int:
        """Liczba subskrypcji (super-admin). channel_id=None = wszystkie."""
        try:
            connection = await db_manager.get_connection()
            if channel_id is not None:
                async with connection.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE channel_id = ?", (channel_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            else:
                async with connection.execute("SELECT COUNT(*) FROM subscriptions") as cursor:
                    row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Błąd count_subscriptions: {e}")
            return 0

    @staticmethod
    async def get_broadcast_user_ids(owners_only: bool = False) -> List[int]:
        """Unikalne user_id do broadcastu: subskrypcje + ownerzy kanałów. owners_only=True = tylko owner_id z channels."""
        try:
            connection = await db_manager.get_connection()
            user_ids = set()
            if owners_only:
                async with connection.execute("SELECT owner_id FROM channels") as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    user_ids.add(int(row["owner_id"]))
            else:
                async with connection.execute("SELECT user_id FROM subscriptions") as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    user_ids.add(int(row["user_id"]))
                async with connection.execute("SELECT owner_id FROM channels") as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    user_ids.add(int(row["owner_id"]))
            return list(user_ids)
        except Exception as e:
            logger.error(f"Błąd get_broadcast_user_ids: {e}")
            return []

    @staticmethod
    async def update_subscription_details(
        user_id: int, 
        channel_id: Optional[int] = None, 
        new_end_date: Optional[datetime] = None,
        new_tier: Optional[str] = None
    ) -> bool:
        """
        Aktualizacja szczegółów subskrypcji (data końca, tier).
        Jeśli channel_id jest podany, aktualizuje konkretny wpis.
        Jeśli nie, próbuje zaktualizować (mniej bezpieczne jeśli user ma suby w wielu kanałach, ale zachowano dla kompatybilności wstecznej).
        ZALECANE: Podawanie channel_id.
        """
        try:
            connection = await db_manager.get_connection()
            updates = []
            params = []

            if new_end_date:
                updates.append("end_date = ?")
                params.append(new_end_date)
            
            if new_tier:
                updates.append("tier = ?")
                params.append(new_tier)

            if not updates:
                return False

            query = "UPDATE subscriptions SET " + ", ".join(updates) + " WHERE user_id = ?"
            params.append(user_id)

            if channel_id:
                query += " AND channel_id = ?"
                params.append(channel_id)

            async with connection.execute(query, tuple(params)): pass
            await connection.commit()
            
            logger.info(f"Zaktualizowano subskrypcję user_id={user_id}, channel={channel_id}: {updates}")
            return True

        except Exception as e:
            logger.error(f"Błąd aktualizacji szczegółów subskrypcji: {e}")
            return False



def _parse_publish_date(value) -> datetime:
    """Parsuje publish_date z bazy (string ISO lub YYYY-MM-DD HH:MM:SS)."""
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace(" ", "T", 1)[:19])
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
        except ValueError:
            continue
    return datetime.now()


class PostManager:
    """Menedżer operacji na zaplanowanych postach"""
    
    @staticmethod
    async def create_scheduled_post(
        owner_id: int,
        channel_id: int,
        content_type: str,
        content: str,
        publish_date: datetime,
        caption: Optional[str] = None,
        buttons: Optional[List[Dict[str, str]]] = None
    ) -> Optional[int]:
        """Utworzenie zaplanowanego posta (przypisanego do kanału)."""
        try:
            connection = await db_manager.get_connection()
            
            buttons_json = json.dumps(buttons) if buttons else None
            
            # channel_id w Telegramie jest ujemny – zapisujemy jako int
            ch_id = int(channel_id)
            # PostgreSQL/asyncpg wymaga datetime, SQLite przyjmuje string
            publish_param = publish_date if USE_POSTGRES else publish_date.strftime("%Y-%m-%d %H:%M:%S")
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO scheduled_posts 
                    (owner_id, channel_id, content_type, content, caption, buttons_json, publish_date)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING post_id
                """, (owner_id, ch_id, content_type, content, caption, buttons_json, publish_param)) as cursor:
                    row = await cursor.fetchone()
                    post_id = row["post_id"] if row else None
            else:
                async with connection.execute("""
                    INSERT INTO scheduled_posts 
                    (owner_id, channel_id, content_type, content, caption, buttons_json, publish_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (owner_id, ch_id, content_type, content, caption, buttons_json, publish_param)): pass
                async with connection.execute("SELECT last_insert_rowid()") as cur:
                    row = await cur.fetchone()
                    post_id = row[0] if row else None
            await connection.commit()
            
            if post_id:
                logger.info(
                    f"Utworzono zaplanowany post {post_id} dla {owner_id} na kanał {channel_id} "
                    f"na {publish_date.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            return post_id
            
        except Exception as e:
            logger.error(f"Błąd tworzenia zaplanowanego posta: {e}")
            return None
    
    @staticmethod
    async def get_scheduled_posts(owner_id: int, channel_id: Optional[int] = None) -> List[ScheduledPost]:
        """Pobranie zaplanowanych postów dla właściciela (opcjonalnie dla danego kanału)."""
        try:
            connection = await db_manager.get_connection()
            if channel_id is not None:
                query = """
                    SELECT * FROM scheduled_posts 
                    WHERE owner_id = ? AND channel_id = ? AND status = 'pending'
                    ORDER BY publish_date ASC
                """
                params = (owner_id, channel_id)
            else:
                query = """
                    SELECT * FROM scheduled_posts 
                    WHERE owner_id = ? AND status = 'pending'
                    ORDER BY publish_date ASC
                """
                params = (owner_id,)
            async with connection.execute(query, params) as cursor:
                rows = await cursor.fetchall()
            
            posts = []
            for row in rows:
                try:
                    ch_id = row["channel_id"]
                except (KeyError, IndexError):
                    ch_id = None
                ch_id = int(ch_id) if ch_id is not None else 0
                posts.append(ScheduledPost(
                    post_id=row["post_id"],
                    owner_id=row["owner_id"],
                    channel_id=ch_id,
                    content_type=row["content_type"],
                    content=row["content"],
                    caption=row["caption"],
                    buttons_json=row["buttons_json"],
                    publish_date=_parse_publish_date(row["publish_date"]),
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                ))
            return posts
        except Exception as e:
            logger.error(f"Błąd pobierania postów właściciela {owner_id}: {e}")
            return []

    @staticmethod
    async def count_pending_posts(owner_id: int) -> int:
        """Liczba zaplanowanych (pending) postów użytkownika."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT COUNT(*) FROM scheduled_posts WHERE owner_id = ? AND status = 'pending'",
                (owner_id,),
            ) as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Błąd zliczania postów {owner_id}: {e}")
            return 0

    @staticmethod
    async def get_post_by_id(post_id: int, owner_id: Optional[int] = None) -> Optional[ScheduledPost]:
        """Pobranie pojedynczego posta po ID (opcjonalnie z walidacją owner_id)."""
        try:
            connection = await db_manager.get_connection()
            if owner_id is not None:
                async with connection.execute(
                    "SELECT * FROM scheduled_posts WHERE post_id = ? AND owner_id = ?",
                    (post_id, owner_id),
                ) as cursor:
                    row = await cursor.fetchone()
            else:
                async with connection.execute(
                    "SELECT * FROM scheduled_posts WHERE post_id = ?", (post_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            if not row:
                return None
            try:
                ch_id = row["channel_id"]
            except (KeyError, IndexError):
                ch_id = None
            ch_id = int(ch_id) if ch_id is not None else 0
            return ScheduledPost(
                post_id=row["post_id"],
                owner_id=row["owner_id"],
                channel_id=ch_id,
                content_type=row["content_type"],
                content=row["content"],
                caption=row["caption"],
                buttons_json=row["buttons_json"],
                publish_date=_parse_publish_date(row["publish_date"]),
                status=row["status"],
                created_at=_row_datetime(row.get("created_at"))
            )
        except Exception as e:
            logger.error(f"Błąd pobierania posta {post_id}: {e}")
            return None

    @staticmethod
    async def get_posts_to_publish() -> List[ScheduledPost]:
        """Pobranie postów gotowych do publikacji (z channel_id). Porównanie dat po stringu ISO."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            # PostgreSQL/asyncpg wymaga datetime, SQLite przyjmuje string
            now_param = now_dt if USE_POSTGRES else now_dt.strftime("%Y-%m-%d %H:%M:%S")
            async with connection.execute("""
                SELECT * FROM scheduled_posts 
                WHERE status = 'pending' AND publish_date <= ?
                ORDER BY publish_date ASC
            """, (now_param,)) as cursor:
                rows = await cursor.fetchall()
            
            posts = []
            for row in rows:
                try:
                    ch_id = row["channel_id"]
                except (KeyError, IndexError):
                    ch_id = None
                ch_id = int(ch_id) if ch_id is not None else 0
                posts.append(ScheduledPost(
                    post_id=row["post_id"],
                    owner_id=row["owner_id"],
                    channel_id=ch_id,
                    content_type=row["content_type"],
                    content=row["content"],
                    caption=row["caption"],
                    buttons_json=row["buttons_json"],
                    publish_date=_parse_publish_date(row["publish_date"]),
                    status=row["status"],
                    created_at=_row_datetime(row.get("created_at"))
                ))
            return posts
        except Exception as e:
            logger.error(f"Błąd pobierania postów do publikacji: {e}")
            return []
    
    @staticmethod
    async def update_post_status(post_id: int, status: str) -> bool:
        """Aktualizacja statusu posta"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute("""
                UPDATE scheduled_posts 
                SET status = ? 
                WHERE post_id = ?
            """, (status, post_id)): pass
            await connection.commit()
            
            logger.info(f"Zaktualizowano status posta {post_id}: {status}")
            return True
            
        except Exception as e:
            logger.error(f"Błąd aktualizacji statusu posta: {e}")
            return False
    
    @staticmethod
    async def delete_post(post_id: int) -> bool:
        """Usunięcie zaplanowanego posta"""
        try:
            connection = await db_manager.get_connection()
            
            async with connection.execute("""
                DELETE FROM scheduled_posts 
                WHERE post_id = ?
            """, (post_id,)): pass
            await connection.commit()
            
            logger.info(f"Usunięto zaplanowany post {post_id}")
            return True
            
        except Exception as e:
            logger.error(f"Błąd usuwania posta: {e}")
            return False


class SFSManager:
    """Menedżer ogłoszeń i ocen SFS (Shoutout for Shoutout)"""

    @staticmethod
    async def count_listings() -> int:
        """Liczba wpisów na liście SFS."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT COUNT(*) FROM sfs_listings") as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"SFS count_listings: {e}")
            return 0

    @staticmethod
    async def get_listing_by_owner(owner_id: int) -> Optional[Dict[str, Any]]:
        """Pobranie wpisu SFS po owner_id."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT * FROM sfs_listings WHERE owner_id = ?", (owner_id,)
            ) as cursor:
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"SFS get_listing_by_owner: {e}")
            return None

    @staticmethod
    async def get_listings_page(page: int, per_page: int = 10) -> List[Dict[str, Any]]:
        """Strona listy SFS z reputacją (łapki) po owner_id. Sortowanie: refreshed_at DESC, created_at DESC."""
        try:
            connection = await db_manager.get_connection()
            offset = page * per_page
            # Podzapytanie dla agregatów – zgodne z PostgreSQL (GROUP BY) i SQLite
            async with connection.execute("""
                SELECT l.*,
                    COALESCE(stats.thumbs_up, 0) AS thumbs_up,
                    COALESCE(stats.thumbs_down, 0) AS thumbs_down
                FROM sfs_listings l
                LEFT JOIN (
                    SELECT owner_id,
                        SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END) AS thumbs_up,
                        SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) AS thumbs_down
                    FROM sfs_ratings
                    GROUP BY owner_id
                ) stats ON stats.owner_id = l.owner_id
                ORDER BY l.refreshed_at DESC, l.created_at DESC
                LIMIT ? OFFSET ?
            """, (per_page, offset)) as cursor:
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"SFS get_listings_page: {e}")
            return []

    @staticmethod
    async def get_listings_total() -> int:
        """Całkowita liczba wpisów SFS (do paginacji)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT COUNT(*) FROM sfs_listings") as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"SFS get_listings_total: {e}")
            return 0

    @staticmethod
    async def get_all_listings() -> List[Dict[str, Any]]:
        """Wszystkie wpisy SFS (owner_id, channel_id) – do okresowego auto-fill views."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT owner_id, channel_id FROM sfs_listings"
            ) as cursor:
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"SFS get_all_listings: {e}")
            return []

    @staticmethod
    async def create_listing(
        owner_id: int,
        channel_id: int,
        username: str,
        channel_title: str,
        avg_views_per_post: int,
        members_count: int,
    ) -> bool:
        """Dodanie lub aktualizacja wpisu SFS (jeden wpis na owner_id)."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now = now_dt if USE_POSTGRES else now_dt.isoformat()
            existing = await SFSManager.get_listing_by_owner(owner_id)
            if existing:
                async with connection.execute("""
                    UPDATE sfs_listings
                    SET channel_id = ?, username = ?, channel_title = ?, avg_views_per_post = ?, members_count = ?, refreshed_at = ?
                    WHERE owner_id = ?
                """, (channel_id, username or "", channel_title or "", avg_views_per_post, members_count, now, owner_id)): pass
            else:
                async with connection.execute("""
                    INSERT INTO sfs_listings
                    (owner_id, channel_id, username, channel_title, avg_views_per_post, members_count, refreshed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (owner_id, channel_id, username or "", channel_title or "", avg_views_per_post, members_count, now, now)): pass
            await connection.commit()
            logger.info(f"SFS: listing owner_id={owner_id}, channel_id={channel_id}")
            return True
        except Exception as e:
            logger.error(f"SFS create_listing: {e}")
            return False

    @staticmethod
    async def update_listing_refresh(
        owner_id: int,
        refreshed_at: Optional[datetime] = None,
        avg_views_per_post: Optional[int] = None,
        members_count: Optional[int] = None,
    ) -> bool:
        """Aktualizacja refreshed_at i opcjonalnie avg_views_per_post, members_count."""
        try:
            connection = await db_manager.get_connection()
            now_dt = refreshed_at or datetime.now()
            now = now_dt if USE_POSTGRES else now_dt.isoformat()
            if avg_views_per_post is not None and members_count is not None:
                async with connection.execute("""
                    UPDATE sfs_listings
                    SET refreshed_at = ?, avg_views_per_post = ?, members_count = ?
                    WHERE owner_id = ?
                """, (now, avg_views_per_post, members_count, owner_id)): pass
            else:
                async with connection.execute("""
                    UPDATE sfs_listings SET refreshed_at = ? WHERE owner_id = ?
                """, (now, owner_id)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS update_listing_refresh: {e}")
            return False

    @staticmethod
    async def was_refreshed_today(owner_id: int) -> bool:
        """Czy użytkownik odświeżył już dziś (tego samego dnia kalendarzowego)."""
        try:
            listing = await SFSManager.get_listing_by_owner(owner_id)
            if not listing or not listing.get("refreshed_at"):
                return False
            ref = listing["refreshed_at"]
            if isinstance(ref, str):
                ref_dt = datetime.fromisoformat(ref.replace("Z", "+00:00")[:19])
            else:
                ref_dt = ref
            return ref_dt.date() == datetime.now().date()
        except Exception as e:
            logger.error(f"SFS was_refreshed_today: {e}")
            return True

    @staticmethod
    async def set_rating(owner_id: int, rater_user_id: int, vote: int) -> bool:
        """Ustawienie oceny użytkownika (owner_id): vote 1 = thumbs up, -1 = thumbs down. Reputacja nie resetuje się przy usunięciu ogłoszenia."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now = now_dt if USE_POSTGRES else now_dt.isoformat()
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO sfs_ratings (owner_id, rater_user_id, vote, created_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (owner_id, rater_user_id) DO UPDATE SET vote = EXCLUDED.vote, created_at = EXCLUDED.created_at
                """, (owner_id, rater_user_id, vote, now)): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO sfs_ratings (owner_id, rater_user_id, vote, created_at)
                    VALUES (?, ?, ?, ?)
                """, (owner_id, rater_user_id, vote, now)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS set_rating: {e}")
            return False

    @staticmethod
    async def get_rating_counts(owner_id: int) -> tuple:
        """Zwraca (thumbs_up, thumbs_down) dla owner_id (reputacja użytkownika)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("""
                SELECT vote, COUNT(*) AS cnt FROM sfs_ratings WHERE owner_id = ? GROUP BY vote
            """, (owner_id,)) as cursor:
                rows = await cursor.fetchall()
            up = down = 0
            for row in rows:
                if row["vote"] == 1:
                    up = row["cnt"]
                elif row["vote"] == -1:
                    down = row["cnt"]
            return (up, down)
        except Exception as e:
            logger.error(f"SFS get_rating_counts: {e}")
            return (0, 0)

    @staticmethod
    async def can_user_rate(rater_user_id: int) -> bool:
        """Czy użytkownik ma prawo oceniać (ma wpis SFS z min. 100 subów na kanale free)."""
        try:
            listing = await SFSManager.get_listing_by_owner(rater_user_id)
            if not listing:
                return False
            return (listing.get("members_count") or 0) >= 100
        except Exception as e:
            logger.error(f"SFS can_user_rate: {e}")
            return False

    @staticmethod
    async def delete_listing(owner_id: int) -> bool:
        """Usunięcie wpisu SFS. Oceny (reputacja) użytkownika nie są usuwane."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute("SELECT 1 FROM sfs_listings WHERE owner_id = ? LIMIT 1", (owner_id,)) as cur:
                if await cur.fetchone() is None:
                    return False
            async with connection.execute("DELETE FROM sfs_listings WHERE owner_id = ?", (owner_id,)): pass
            async with connection.execute("DELETE FROM sfs_stats_refreshes WHERE owner_id = ?", (owner_id,)): pass
            await connection.commit()
            logger.info(f"SFS: usunięto listing owner_id={owner_id}")
            return True
        except Exception as e:
            logger.error(f"SFS delete_listing: {e}")
            return False

    @staticmethod
    async def count_stats_refreshes_today(owner_id: int) -> int:
        """Liczba odświeżeń statystyk (views) dziś – max 5 dziennie."""
        try:
            connection = await db_manager.get_connection()
            if USE_POSTGRES:
                q = "SELECT COUNT(*) FROM sfs_stats_refreshes WHERE owner_id = $1 AND date(created_at) = CURRENT_DATE"
            else:
                q = "SELECT COUNT(*) FROM sfs_stats_refreshes WHERE owner_id = ? AND date(created_at) = date('now', 'localtime')"
            async with connection.execute(q, (owner_id,)) as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"SFS count_stats_refreshes_today: {e}")
            return 999

    @staticmethod
    async def record_stats_refresh(owner_id: int) -> bool:
        """Zapis odświeżenia statystyk (views) – do limitu 5/dzień."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now_param = now_dt if USE_POSTGRES else now_dt.isoformat()
            async with connection.execute(
                "INSERT INTO sfs_stats_refreshes (owner_id, created_at) VALUES (?, ?)",
                (owner_id, now_param),
            ): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS record_stats_refresh: {e}")
            return False

    @staticmethod
    async def update_listing_views(owner_id: int, avg_views_per_post: int) -> bool:
        """Aktualizacja tylko avg_views_per_post dla wpisu SFS (bez zmiany refreshed_at)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "UPDATE sfs_listings SET avg_views_per_post = ? WHERE owner_id = ?",
                (avg_views_per_post, owner_id),
            ): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS update_listing_views: {e}")
            return False

    @staticmethod
    async def update_listing_members_count(owner_id: int, members_count: int) -> bool:
        """Aktualizacja tylko members_count dla wpisu SFS (bez zmiany refreshed_at)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "UPDATE sfs_listings SET members_count = ? WHERE owner_id = ?",
                (members_count, owner_id),
            ): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS update_listing_members_count: {e}")
            return False

    @staticmethod
    async def get_listing_by_channel_id(channel_id: int) -> Optional[Dict[str, Any]]:
        """Pobranie wpisu SFS po channel_id (kanał free)."""
        try:
            connection = await db_manager.get_connection()
            async with connection.execute(
                "SELECT * FROM sfs_listings WHERE channel_id = ?", (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"SFS get_listing_by_channel_id: {e}")
            return None

    @staticmethod
    async def store_channel_post(channel_id: int, message_id: int, message_date_ts: int, views: int) -> bool:
        """Zapis posta z kanału (channel_post) – do późniejszego uzupełnienia views (24h–3 dni)."""
        try:
            connection = await db_manager.get_connection()
            now_dt = datetime.now()
            now_param = now_dt if USE_POSTGRES else now_dt.isoformat()
            if USE_POSTGRES:
                async with connection.execute("""
                    INSERT INTO sfs_channel_posts (channel_id, message_id, message_date_ts, views, received_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (channel_id, message_id) DO UPDATE SET message_date_ts = EXCLUDED.message_date_ts, views = EXCLUDED.views, received_at = EXCLUDED.received_at
                """, (channel_id, message_id, message_date_ts, views, now_param)): pass
            else:
                async with connection.execute("""
                    INSERT OR REPLACE INTO sfs_channel_posts (channel_id, message_id, message_date_ts, views, received_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (channel_id, message_id, message_date_ts, views, now_param)): pass
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"SFS store_channel_post: {e}")
            return False

    @staticmethod
    async def get_channel_post_in_range(
        channel_id: int, min_age_sec: int, max_age_sec: int
    ) -> Optional[Dict[str, Any]]:
        """Post z kanału w przedziale wieku (min_age_sec <= wiek <= max_age_sec). Zwraca ostatni (najświeższy) pasujący."""
        try:
            connection = await db_manager.get_connection()
            now_ts = int(datetime.now(timezone.utc).timestamp())
            min_ts = now_ts - max_age_sec
            max_ts = now_ts - min_age_sec
            async with connection.execute("""
                SELECT * FROM sfs_channel_posts
                WHERE channel_id = ? AND message_date_ts >= ? AND message_date_ts <= ?
                ORDER BY message_date_ts DESC
                LIMIT 1
            """, (channel_id, min_ts, max_ts)) as cursor:
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"SFS get_channel_post_in_range: {e}")
            return None