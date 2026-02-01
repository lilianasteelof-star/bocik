import asyncio
import logging
from config import settings

# Setup simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_repair")

# Używaj repair tylko dla SQLite (dla Supabase/PostgreSQL nie ma skryptu naprawy)
USE_POSTGRES = bool(getattr(settings, "DB_HOST", None) and getattr(settings, "DB_PASSWORD", None))

async def repair():
    if USE_POSTGRES:
        logger.info("Używasz Supabase (PostgreSQL). Ten skrypt naprawy dotyczy tylko SQLite. Pominięto.")
        return
    import aiosqlite
    db_path = settings.DATABASE_PATH
    admin_id = settings.ADMIN_ID
    logger.info(f"Repairing DB at: {db_path}")
    logger.info(f"Default Admin ID: {admin_id}")
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # 1. Rename bad table
            logger.info("Renaming current 'subscriptions' to 'subscriptions_backup_corrupted'...")
            try:
                await db.execute("ALTER TABLE subscriptions RENAME TO subscriptions_backup_corrupted")
            except Exception as e:
                logger.warning(f"Could not rename table (maybe doesn't exist?): {e}")

            # 2. Create correct table
            logger.info("Creating new valid 'subscriptions' table...")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER,
                    owner_id INTEGER, -- ID Admina
                    channel_id INTEGER, -- ID Kanału
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
            
            # 3. Try to migrate data
            logger.info("Attempting to recover data from backup...")
            try:
                async with db.execute("SELECT * FROM subscriptions_backup_corrupted") as cursor:
                    old_rows = await cursor.fetchall()
                
                # Try to find a default channel for the admin
                async with db.execute("SELECT channel_id FROM channels WHERE owner_id = ? AND type = 'premium' LIMIT 1", (admin_id,)) as cur:
                    chan_row = await cur.fetchone()
                
                default_channel_id = chan_row['channel_id'] if chan_row else None
                
                if not default_channel_id:
                    logger.error("No premium channel found for admin! Cannot assign subscriptions.")
                    # Try to create one? Or just create a dummy?
                    # Let's check if ANY channel exists
                    async with db.execute("SELECT channel_id FROM channels LIMIT 1") as cur:
                         any_chan = await cur.fetchone()
                    default_channel_id = any_chan['channel_id'] if any_chan else 0
                
                logger.info(f"Using default channel_id: {default_channel_id} for recovered subs")

                recovered_count = 0
                for row in old_rows:
                    try:
                        await db.execute("""
                            INSERT INTO subscriptions 
                            (user_id, owner_id, channel_id, username, full_name, start_date, end_date, tier, status, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row['user_id'], 
                            admin_id, 
                            default_channel_id,
                            row['username'], 
                            row['full_name'], 
                            row['start_date'], 
                            row['end_date'], 
                            row['tier'], 
                            row['status'],
                            row['created_at']
                        ))
                        recovered_count += 1
                    except Exception as ins_e:
                        logger.error(f"Failed to recover sub {row['user_id']}: {ins_e}")
                
                logger.info(f"Recovered {recovered_count} subscriptions.")
                
            except Exception as e:
                logger.error(f"Data recovery failed: {e}")

            await db.commit()
            logger.info("Repair complete.")

    except Exception as e:
        logger.error(f"Critical Repair Error: {e}")

if __name__ == "__main__":
    asyncio.run(repair())
