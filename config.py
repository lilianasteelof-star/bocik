"""
Konfiguracja bota - ładowanie zmiennych środowiskowych z walidacją
"""
import os
import logging
from pathlib import Path
from typing import Optional
from pydantic import validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Ustawienia bota z walidacją Pydantic"""
    
    # Bot configuration
    BOT_TOKEN: str
    ADMIN_ID: int
    # Dodatkowe ID superadminów (po przecinku). Domyślnie drugi superadmin: 7829319839
    SUPERADMIN_IDS: str = "7829319839"
    
    # Channel IDs (opcjonalne - mogą być ustawione przez komendy bota)
    PREMIUM_CHANNEL_ID: Optional[int] = None
    FREE_CHANNEL_ID: Optional[int] = None
    
    # Telethon (opcjonalne – do średniej wyświetleń/post w SFS)
    TELEGRAM_API_ID: Optional[int] = None
    TELEGRAM_API_HASH: Optional[str] = None
    
    # Database – Supabase (PostgreSQL); jeśli ustawione, używana jest Supabase zamiast SQLite
    DATABASE_PATH: str = "database/bot.db"  # używane tylko gdy brak DB_HOST
    DB_HOST: Optional[str] = "aws-1-eu-central-1.pooler.supabase.com"
    DB_PORT: int = 6543
    DB_NAME: str = "postgres"
    DB_USER: str = "postgres.cflzgaosomhshxffjevf"
    DB_PASSWORD: str = ""  # ustaw w .env (np. DB_PASSWORD=Wektor420##)
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Scheduler
    SCHEDULER_INTERVAL_HOURS: int = 1
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
    
    @validator("BOT_TOKEN")
    def validate_bot_token(cls, v):
        if not v or len(v) < 10:
            raise ValueError("BOT_TOKEN musi być prawidłowy")
        return v
    
    @validator("ADMIN_ID")
    def validate_admin_id(cls, v):
        if v <= 0:
            raise ValueError("ADMIN_ID musi być dodatnim numerem")
        return v

    @property
    def superadmin_ids(self) -> list:
        """Lista dodatkowych ID superadminów (z env SUPERADMIN_IDS)."""
        if not getattr(self, "SUPERADMIN_IDS", None):
            return []
        try:
            return [int(x.strip()) for x in self.SUPERADMIN_IDS.split(",") if x.strip()]
        except (ValueError, AttributeError):
            return []

    def is_superadmin(self, user_id: int) -> bool:
        """Czy user_id to główny admin lub jeden z SUPERADMIN_IDS."""
        return user_id == self.ADMIN_ID or user_id in self.superadmin_ids
    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"LOG_LEVEL musi być jednym z: {valid_levels}")
        return v.upper()
    
    @validator("SCHEDULER_INTERVAL_HOURS")
    def validate_scheduler_interval(cls, v):
        if v < 1 or v > 24:
            raise ValueError("SCHEDULER_INTERVAL_HOURS musi być między 1 a 24")
        return v

    def setup_logging(self) -> None:
        """Konfiguracja systemu logowania"""
        # Utworzenie katalogu logs jeśli nie istnieje
        Path("logs").mkdir(exist_ok=True)
        
        # Format logów
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"
        
        # Konfiguracja głównego loggera
        logging.basicConfig(
            level=getattr(logging, self.LOG_LEVEL),
            format=log_format,
            datefmt=date_format,
            handlers=[
                # Handler do pliku
                logging.FileHandler(
                    "logs/bot.log", 
                    encoding="utf-8"
                ),
                # Handler do konsoli
                logging.StreamHandler()
            ]
        )
        
        # Oddzielne logi dla różnych modułów
        modules_to_log = [
            "aiogram",
            "database",
            "scheduler",
            "handlers"
        ]
        
        for module in modules_to_log:
            logger = logging.getLogger(module)
            file_handler = logging.FileHandler(
                f"logs/{module}.log", 
                encoding="utf-8"
            )
            file_handler.setFormatter(
                logging.Formatter(log_format, date_format)
            )
            logger.addHandler(file_handler)


# Globalna instancja konfiguracji
try:
    settings = Settings()
    settings.setup_logging()
except Exception as e:
    import sys
    sys.stderr.write(f"Blad ladowania konfiguracji: {e}\n")
    sys.stderr.write("Sprawdz plik .env lub utworz go na podstawie .env.example\n")
    sys.stderr.write("Wymagane zmienne: BOT_TOKEN, ADMIN_ID\n")
    exit(1)