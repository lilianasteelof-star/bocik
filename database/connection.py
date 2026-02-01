"""
Zarządzanie połączeniem z bazą danych – Supabase (PostgreSQL) lub SQLite
"""
import logging
import re
from typing import Optional, List, Any

from config import settings

logger = logging.getLogger("database")

# Użycie PostgreSQL (Supabase) gdy podane DB_HOST i DB_PASSWORD
USE_POSTGRES = bool(getattr(settings, "DB_HOST", None) and getattr(settings, "DB_PASSWORD", None))


def _convert_placeholders(sql: str) -> str:
    """Zamienia placeholdery ? na $1, $2, ... (dla asyncpg)."""
    n = 0
    def repl(_):
        nonlocal n
        n += 1
        return f"${n}"
    return re.sub(r"\?", repl, sql)


class CursorLike:
    """Kursoropodobny obiekt zwracany przez execute() – fetchone/fetchall i async with."""
    def __init__(self, rows: List[Any]):
        self._rows = rows
        self._index = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def fetchone(self):
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None

    async def fetchall(self):
        r = self._rows[self._index:]
        self._index = len(self._rows)
        return r


if USE_POSTGRES:
    import asyncpg

    class _ExecuteContext:
        """Async context manager: async with connection.execute(...) as cursor – dla każdego zapytania osobne połączenie z puli."""
        def __init__(self, wrapper: "ConnectionWrapper", sql: str, parameters):
            self._wrapper = wrapper
            self._sql = sql
            self._params = parameters

        async def __aenter__(self):
            sql_pg, params = self._wrapper._sql_params(self._sql, self._params)
            async with self._wrapper._pool.acquire() as conn:
                try:
                    rows = await conn.fetch(sql_pg, *params)
                except Exception:
                    await conn.execute(sql_pg, *params)
                    rows = []
            self._cursor = CursorLike(rows)
            return self._cursor

        async def __aexit__(self, *args):
            return None

    class ConnectionWrapper:
        """Wrapper na pulę asyncpg – każde execute() bierze połączenie z puli (dostosowane do free Supabase)."""
        def __init__(self, pool: asyncpg.Pool):
            self._pool = pool

        def _sql_params(self, sql: str, parameters):
            params = parameters if parameters is not None else ()
            return _convert_placeholders(sql), params

        def execute(self, sql: str, parameters=None):
            """Zwraca obiekt async context manager – użycie: async with connection.execute(...) as cursor."""
            return _ExecuteContext(self, sql, parameters)

        async def commit(self):
            # asyncpg w trybie autocommit – commit no-op
            pass

    class DatabaseManager:
        """Menedżer połączeń z Supabase (PostgreSQL) – pula połączeń dla free tier (konkurencja zadań)."""
        def __init__(self):
            self._pool: Optional[asyncpg.Pool] = None
            self._wrapper: Optional[ConnectionWrapper] = None

        async def connect(self):
            if not settings.DB_PASSWORD:
                raise ValueError(
                    "DB_PASSWORD jest wymagane. Ustaw w Railway Variables (lub .env): "
                    "DB_PASSWORD=... albo dodaj plugin PostgreSQL – wtedy ustawiona jest DATABASE_URL."
                )
            try:
                self._pool = await asyncpg.create_pool(
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    database=settings.DB_NAME,
                    user=settings.DB_USER,
                    password=settings.DB_PASSWORD,
                    ssl="require",
                    statement_cache_size=0,  # wymagane przy PgBouncer (Supabase)
                    min_size=1,
                    max_size=5,  # free tier Supabase – mała pula, wiele zadań może równolegle
                )
                self._wrapper = ConnectionWrapper(self._pool)
                logger.info("Połączono z PostgreSQL – pula połączeń")
                return self._wrapper
            except Exception as e:
                err = str(e).lower()
                hint = ""
                if "password" in err and "authentication" in err:
                    hint = (
                        " Sprawdź: Railway → Twój serwis → PostgreSQL → Variables: "
                        "skopiuj DATABASE_URL (albo PGPASSWORD). "
                        "Jeśli używasz Supabase: ustaw DB_HOST, DB_USER, DB_PASSWORD w Variables."
                    )
                logger.error("Błąd połączenia z PostgreSQL: %s.%s", e, hint)
                raise

        async def disconnect(self):
            if self._pool:
                await self._pool.close()
                self._pool = None
                self._wrapper = None
                logger.info("Rozłączono z bazą danych")

        async def get_connection(self):
            """Zwraca wrapper z interfejsem execute/commit (jak aiosqlite)."""
            if not self._pool:
                await self.connect()
            return self._wrapper

        async def init_tables(self):
            await self._init_tables_postgres()

        async def _init_tables_postgres(self):
            if not self._pool:
                await self.connect()
            async with self._pool.acquire() as c:
                try:
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        setting_key TEXT NOT NULL,
                        setting_value TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, setting_key)
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS channels (
                        channel_id BIGINT PRIMARY KEY,
                        owner_id BIGINT NOT NULL,
                        title TEXT,
                        type TEXT DEFAULT 'premium',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        user_id BIGINT,
                        owner_id BIGINT,
                        channel_id BIGINT,
                        username TEXT,
                        full_name TEXT,
                        start_date TIMESTAMP NOT NULL,
                        end_date TIMESTAMP NOT NULL,
                        tier TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS scheduled_posts (
                        post_id SERIAL PRIMARY KEY,
                        owner_id BIGINT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        content_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        caption TEXT,
                        buttons_json TEXT,
                        publish_date TIMESTAMP NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_listings (
                        id SERIAL PRIMARY KEY,
                        owner_id BIGINT NOT NULL UNIQUE,
                        channel_id BIGINT NOT NULL,
                        username TEXT,
                        channel_title TEXT,
                        avg_views_per_post INTEGER DEFAULT 0,
                        members_count INTEGER DEFAULT 0,
                        refreshed_at TIMESTAMP NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_ratings (
                        owner_id BIGINT NOT NULL,
                        rater_user_id BIGINT NOT NULL,
                        vote INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (owner_id, rater_user_id)
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_stats_refreshes (
                        id SERIAL PRIMARY KEY,
                        owner_id BIGINT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_channel_posts (
                        id SERIAL PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL,
                        message_date_ts BIGINT NOT NULL,
                        views INTEGER DEFAULT 0,
                        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(channel_id, message_id)
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS global_blacklist (
                        user_id BIGINT PRIMARY KEY,
                        reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS bot_users (
                        user_id BIGINT PRIMARY KEY,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS inbox_muted (
                        user_id BIGINT PRIMARY KEY
                    )
                """)
                    await c.execute("""
                    CREATE TABLE IF NOT EXISTS user_interaction_logs (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        event_type TEXT NOT NULL,
                        content_preview TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                    await c.execute("CREATE INDEX IF NOT EXISTS idx_interaction_logs_user_created ON user_interaction_logs (user_id, created_at DESC)")
                    logger.info("Tabele PostgreSQL (Supabase) zainicjalizowane")
                    await self._migrate_bot_settings_user_id(c)
                    await self._migrate_scheduled_posts_owner_id(c)
                    await self._migrate_add_channel_id(c)
                    await self._migrate_scheduled_posts_channel_id(c)
                    await self._migrate_bot_users_display_info(c)
                except Exception as e:
                    logger.error(f"Błąd inicjalizacji tabel PostgreSQL: {e}")
                    raise

        async def _migrate_bot_users_display_info(self, conn):
            try:
                for col in ("last_username", "last_full_name"):
                    r = await conn.fetch("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'bot_users' AND column_name = $1
                    """, col)
                    if not r:
                        await conn.execute(f"ALTER TABLE bot_users ADD COLUMN {col} TEXT")
                        logger.info("Migracja bot_users: dodano kolumnę %s", col)
            except Exception as e:
                logger.error("Migracja bot_users display_info: %s", e)

        async def _migrate_add_channel_id(self, conn):
            try:
                r = await conn.fetch("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'subscriptions' AND column_name = 'channel_id'
                """)
                if r:
                    return
                logger.info("Migracja subscriptions (V2: channel_id) – tabela już ma channel_id w PostgreSQL")
            except Exception as e:
                logger.error(f"Błąd migracji subscriptions: {e}")

        async def _migrate_bot_settings_user_id(self, conn):
            try:
                r = await conn.fetch("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'bot_settings' AND column_name = 'user_id'
                """)
                if r:
                    return
                await conn.execute("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS user_id BIGINT")
                logger.info("Migracja bot_settings (user_id) zakończona.")
            except Exception as e:
                logger.error(f"Błąd migracji bot_settings user_id: {e}")

        async def _migrate_scheduled_posts_owner_id(self, conn):
            try:
                r = await conn.fetch("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'scheduled_posts' AND column_name = 'owner_id'
                """)
                if r:
                    return
                await conn.execute("ALTER TABLE scheduled_posts ADD COLUMN IF NOT EXISTS owner_id BIGINT")
                await conn.execute("""
                    UPDATE scheduled_posts SET owner_id = COALESCE((SELECT owner_id FROM channels LIMIT 1), 0)
                    WHERE owner_id IS NULL
                """)
                logger.info("Migracja scheduled_posts (owner_id) zakończona.")
            except Exception as e:
                logger.error(f"Błąd migracji scheduled_posts owner_id: {e}")

        async def _migrate_scheduled_posts_channel_id(self, conn):
            try:
                r = await conn.fetch("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'scheduled_posts' AND column_name = 'channel_id'
                """)
                if r:
                    return
                await conn.execute("ALTER TABLE scheduled_posts ADD COLUMN IF NOT EXISTS channel_id BIGINT")
                owners = await conn.fetch("SELECT DISTINCT owner_id FROM scheduled_posts WHERE channel_id IS NULL")
                for row in owners:
                    oid = row["owner_id"]
                    ch = await conn.fetchrow(
                        "SELECT setting_value FROM bot_settings WHERE user_id = $1 AND setting_key = 'premium_channel_id'", oid
                    )
                    ch_id = int(ch["setting_value"]) if ch and ch["setting_value"] else None
                    if not ch_id:
                        ch = await conn.fetchrow(
                            "SELECT channel_id FROM channels WHERE owner_id = $1 AND type = 'premium' LIMIT 1", oid
                        )
                        ch_id = ch["channel_id"] if ch else None
                    if ch_id is not None:
                        await conn.execute(
                            "UPDATE scheduled_posts SET channel_id = $1 WHERE owner_id = $2 AND channel_id IS NULL",
                            ch_id, oid
                        )
                logger.info("Migracja scheduled_posts (channel_id) zakończona.")
            except Exception as e:
                logger.error(f"Błąd migracji scheduled_posts channel_id: {e}")

else:
    import aiosqlite
    from pathlib import Path

    class DatabaseManager:
        """Menedżer połączeń z bazą danych SQLite (gdy brak Supabase)."""
        def __init__(self, db_path: str = None):
            self.db_path = db_path or settings.DATABASE_PATH
            self._connection: Optional[aiosqlite.Connection] = None

        async def connect(self):
            try:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
                self._connection = await aiosqlite.connect(self.db_path)
                self._connection.row_factory = aiosqlite.Row
                await self._connection.execute("PRAGMA foreign_keys = ON")
                await self._connection.commit()
                logger.info(f"Połączono z bazą danych SQLite: {self.db_path}")
                return self._connection
            except Exception as e:
                logger.error(f"Błąd połączenia z bazą danych: {e}")
                raise

        async def disconnect(self):
            if self._connection:
                await self._connection.close()
                logger.info("Rozłączono z bazą danych")

        async def get_connection(self):
            if not self._connection:
                await self.connect()
            return self._connection

        async def init_tables(self):
            try:
                connection = await self.get_connection()
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        setting_key TEXT NOT NULL,
                        setting_value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, setting_key)
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS channels (
                        channel_id INTEGER PRIMARY KEY,
                        owner_id INTEGER NOT NULL,
                        title TEXT,
                        type TEXT DEFAULT 'premium',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        user_id INTEGER,
                        owner_id INTEGER,
                        channel_id INTEGER,
                        username TEXT,
                        full_name TEXT,
                        start_date DATETIME NOT NULL,
                        end_date DATETIME NOT NULL,
                        tier TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS scheduled_posts (
                        post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        content_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        caption TEXT,
                        buttons_json TEXT,
                        publish_date DATETIME NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_listings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER NOT NULL UNIQUE,
                        channel_id INTEGER NOT NULL,
                        username TEXT,
                        channel_title TEXT,
                        avg_views_per_post INTEGER DEFAULT 0,
                        members_count INTEGER DEFAULT 0,
                        refreshed_at DATETIME NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_ratings (
                        owner_id INTEGER NOT NULL,
                        rater_user_id INTEGER NOT NULL,
                        vote INTEGER NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (owner_id, rater_user_id)
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_stats_refreshes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS sfs_channel_posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        message_date_ts INTEGER NOT NULL,
                        views INTEGER DEFAULT 0,
                        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(channel_id, message_id)
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS global_blacklist (
                        user_id INTEGER PRIMARY KEY,
                        reason TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS bot_users (
                        user_id INTEGER PRIMARY KEY,
                        first_seen DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS inbox_muted (
                        user_id INTEGER PRIMARY KEY
                    )
                """)
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS user_interaction_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        content_preview TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await connection.execute("CREATE INDEX IF NOT EXISTS idx_interaction_logs_user_created ON user_interaction_logs (user_id, created_at DESC)")
                await connection.commit()
                logger.info("Tabele Multi-Tenant zainicjalizowane")
                await self._migrate_bot_settings_user_id()
                await self._migrate_scheduled_posts_owner_id()
                await self._migrate_add_channel_id()
                await self._migrate_add_left_status()
                await self._migrate_scheduled_posts_channel_id()
                await self._migrate_sfs_ratings_to_owner()
                await self._migrate_bot_users_display_info()
            except Exception as e:
                logger.error(f"Błąd inicjalizacji tabel: {e}")
                raise

        async def _migrate_bot_users_display_info(self):
            try:
                async with self._connection.execute("PRAGMA table_info(bot_users)") as cursor:
                    cols = [row[1] for row in await cursor.fetchall()]
                for col in ("last_username", "last_full_name"):
                    if col not in cols:
                        await self._connection.execute(f"ALTER TABLE bot_users ADD COLUMN {col} TEXT")
                        logger.info("Migracja bot_users: dodano kolumnę %s", col)
            except Exception as e:
                logger.error("Migracja bot_users display_info: %s", e)

        async def _migrate_add_channel_id(self):
            try:
                async with self._connection.execute("PRAGMA table_info(subscriptions)") as cursor:
                    columns = await cursor.fetchall()
                    column_names = [col[1] for col in columns]
                if "channel_id" in column_names:
                    return
                logger.info("Rozpoczynam migrację subscriptions (V2)...")
                async with self._connection.execute("SELECT * FROM subscriptions") as cursor:
                    old_subs = await cursor.fetchall()
                if not old_subs:
                    await self._connection.execute("DROP TABLE subscriptions")
                    return
                await self._connection.execute("""
                    CREATE TABLE subscriptions_v2 (
                        user_id INTEGER,
                        owner_id INTEGER,
                        channel_id INTEGER,
                        username TEXT,
                        full_name TEXT,
                        start_date DATETIME NOT NULL,
                        end_date DATETIME NOT NULL,
                        tier TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                migrated_count = 0
                for sub in old_subs:
                    owner_id = sub["owner_id"]
                    async with self._connection.execute(
                        "SELECT channel_id FROM channels WHERE owner_id = ? AND type = 'premium' LIMIT 1",
                        (owner_id,),
                    ) as cur:
                        chan_row = await cur.fetchone()
                    if chan_row:
                        await self._connection.execute("""
                            INSERT INTO subscriptions_v2
                            (user_id, owner_id, channel_id, username, full_name, start_date, end_date, tier, status, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            sub["user_id"], sub["owner_id"], chan_row["channel_id"],
                            sub["username"], sub["full_name"], sub["start_date"],
                            sub["end_date"], sub["tier"], sub.get("status", "active"), sub.get("created_at"),
                        ))
                        migrated_count += 1
                await self._connection.execute("DROP TABLE subscriptions")
                await self._connection.execute("ALTER TABLE subscriptions_v2 RENAME TO subscriptions")
                await self._connection.commit()
                logger.info(f"Migracja V2 zakończona. Przeniesiono: {migrated_count}")
            except Exception as e:
                logger.error(f"Błąd migracji V2: {e}")

        async def _migrate_add_left_status(self):
            pass

        async def _migrate_bot_settings_user_id(self):
            try:
                async with self._connection.execute("PRAGMA table_info(bot_settings)") as cursor:
                    columns = await cursor.fetchall()
                    column_names = [col[1] for col in columns]
                if "user_id" in column_names:
                    return
                await self._connection.execute("ALTER TABLE bot_settings ADD COLUMN user_id INTEGER")
                await self._connection.commit()
            except Exception as e:
                logger.error(f"Błąd migracji bot_settings user_id: {e}")

        async def _migrate_scheduled_posts_owner_id(self):
            try:
                async with self._connection.execute("PRAGMA table_info(scheduled_posts)") as cursor:
                    columns = await cursor.fetchall()
                    column_names = [col[1] for col in columns]
                if "owner_id" in column_names:
                    return
                await self._connection.execute("ALTER TABLE scheduled_posts ADD COLUMN owner_id INTEGER")
                async with self._connection.execute("SELECT owner_id FROM channels LIMIT 1") as cursor:
                    row = await cursor.fetchone()
                default_owner = row["owner_id"] if row else 0
                await self._connection.execute("UPDATE scheduled_posts SET owner_id = ? WHERE owner_id IS NULL", (default_owner,))
                await self._connection.commit()
            except Exception as e:
                logger.error(f"Błąd migracji scheduled_posts owner_id: {e}")

        async def _migrate_scheduled_posts_channel_id(self):
            try:
                async with self._connection.execute("PRAGMA table_info(scheduled_posts)") as cursor:
                    columns = await cursor.fetchall()
                    column_names = [col[1] for col in columns]
                if "channel_id" in column_names:
                    return
                await self._connection.execute("ALTER TABLE scheduled_posts ADD COLUMN channel_id INTEGER")
                async with self._connection.execute("SELECT DISTINCT owner_id FROM scheduled_posts WHERE channel_id IS NULL") as cursor:
                    owners = await cursor.fetchall()
                for row in owners:
                    owner_id = row["owner_id"]
                    channel_id = None
                    async with self._connection.execute(
                        "SELECT setting_value FROM bot_settings WHERE user_id = ? AND setting_key = 'premium_channel_id'",
                        (owner_id,),
                    ) as cur:
                        setting = await cur.fetchone()
                    if setting and setting["setting_value"]:
                        channel_id = int(setting["setting_value"])
                    if not channel_id:
                        async with self._connection.execute(
                            "SELECT channel_id FROM channels WHERE owner_id = ? AND type = 'premium' LIMIT 1",
                            (owner_id,),
                        ) as cur:
                            ch = await cur.fetchone()
                        if ch:
                            channel_id = ch["channel_id"]
                    if channel_id is not None:
                        await self._connection.execute(
                            "UPDATE scheduled_posts SET channel_id = ? WHERE owner_id = ? AND channel_id IS NULL",
                            (channel_id, owner_id),
                        )
                await self._connection.commit()
            except Exception as e:
                logger.error(f"Błąd migracji scheduled_posts channel_id: {e}")

        async def _migrate_sfs_ratings_to_owner(self):
            try:
                async with self._connection.execute("PRAGMA table_info(sfs_ratings)") as cursor:
                    columns = await cursor.fetchall()
                    col_names = [c[1] for c in columns]
                if "owner_id" in col_names:
                    return
                if "listing_id" not in col_names:
                    return
                await self._connection.execute("""
                    CREATE TABLE sfs_ratings_new (
                        owner_id INTEGER NOT NULL,
                        rater_user_id INTEGER NOT NULL,
                        vote INTEGER NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (owner_id, rater_user_id)
                    )
                """)
                await self._connection.execute("""
                    INSERT INTO sfs_ratings_new (owner_id, rater_user_id, vote, created_at)
                    SELECT l.owner_id, r.rater_user_id, r.vote, r.created_at
                    FROM sfs_ratings r
                    JOIN sfs_listings l ON r.listing_id = l.id
                """)
                await self._connection.execute("DROP TABLE sfs_ratings")
                await self._connection.execute("ALTER TABLE sfs_ratings_new RENAME TO sfs_ratings")
                await self._connection.commit()
            except Exception as e:
                logger.error(f"Błąd migracji sfs_ratings owner_id: {e}")


db_manager = DatabaseManager() if USE_POSTGRES else DatabaseManager(settings.DATABASE_PATH)
