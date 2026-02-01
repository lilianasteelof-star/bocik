"""
Definicje stanów FSM (Finite State Machine) dla bota
"""
from aiogram.fsm.state import State, StatesGroup


class SubscriptionManagement(StatesGroup):
    """Stany dla zarządzania subskrypcjami"""
    
    # Stan oczekiwania na wybór kategorii (tier)
    waiting_tier = State()
    
    # Stan oczekiwania na ID użytkownika (manual add)
    waiting_user_id = State()
    
    # Stan oczekiwania na wybór czasu trwania subskrypcji
    waiting_duration = State()
    
    # Stan oczekiwania na wpisanie niestandardowej daty zakończenia
    waiting_custom_date = State()


class PostCreation(StatesGroup):
    """Stany dla tworzenia i planowania postów"""
    
    # Stan oczekiwania na treść posta
    waiting_content = State()
    
    # Stan oczekiwania na przyciski (opcjonalnie)
    waiting_buttons = State()
    
    # Stan oczekiwania na czas publikacji
    waiting_schedule = State()


class PostManagement(StatesGroup):
    """Stany dla zarządzania istniejącymi postami"""
    
    # Stan przeglądania zaplanowanych postów
    viewing_scheduled = State()
    
    # Stan edycji posta
    editing_post = State()
    
    # Stan potwierdzania usunięcia posta
    confirming_deletion = State()

class SubscriptionEditing(StatesGroup):
    """Stany dla edycji subskrypcji"""
    waiting_for_new_date = State()
    waiting_for_new_tier = State()

class ChannelSetup(StatesGroup):
    """Stany dla dodawania nowego kanału"""
    waiting_for_channel_forward = State()


class PostPlanning(StatesGroup):
    """Stany dla planowania postów z dashboardu"""
    choosing_channel = State()
    waiting_content = State()
    waiting_buttons = State()
    waiting_schedule = State()


class SFSStates(StatesGroup):
    """Stany dla SFS: forward przy dołączaniu lub odświeżaniu statystyk"""
    waiting_for_sfs_forward = State()
    waiting_for_sfs_stats_refresh = State()
    waiting_for_manual_views = State()


class SuperAdminBroadcast(StatesGroup):
    """Stany dla broadcastu (super-admin)."""
    choosing_audience = State()
    waiting_message = State()
    waiting_confirm = State()


class SuperAdminBlacklist(StatesGroup):
    """Stany dla czarnej listy (super-admin)."""
    waiting_user_id = State()
    waiting_user_id_full = State()  # ban + opuszczenie kanałów usera


class SuperAdminInbox(StatesGroup):
    """Stany dla inbox (odpowiedź użytkownikowi)."""
    waiting_reply_to_user = State()


class SuperAdminChatUser(StatesGroup):
    """Stany dla panelu aktywni użytkownicy (chat) – napisz jako bot."""
    waiting_message_to_user = State()