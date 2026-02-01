
import asyncio
import aiosqlite
from config import settings

async def inspect():
    db_path = settings.DATABASE_PATH
    print(f"Inspecting DB at: {db_path}")
    
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(subscriptions)") as cursor:
            columns = await cursor.fetchall()
            print("Columns in 'subscriptions' table:")
            for col in columns:
                print(col)

if __name__ == "__main__":
    asyncio.run(inspect())
