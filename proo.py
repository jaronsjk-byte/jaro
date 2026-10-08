import streamlit as st
import os
import time
import random
import json
import pandas as pd
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from PIL import Image
import base64
import numpy as np
import requests
from streamlit_lottie import st_lottie
try:
    import nltk
    from nltk.stem.lancaster import LancasterStemmer
except ImportError:
    pass

# Try to import Google's Generative AI
try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("Warning: google.generativeai not installed. Run: pip install google-generativeai")

# Compatibility helpers: provide `st.status` and `st.toggle` if Streamlit
# in the current environment does not expose them. These are small
# fallbacks so the app doesn't crash when older/newer APIs differ.
if not hasattr(st, "status"):
    def _status(msg, expanded=False):
        class _Ctx:
            def __enter__(self):
                # Use spinner for simple status UI
                self._spinner = st.spinner(msg)
                self._spinner.__enter__()
                return self
            def update(self, label=None, state=None):
                # Spinner doesn't support updates; keep method for compatibility
                return None
            def __exit__(self, exc_type, exc, tb):
                return self._spinner.__exit__(exc_type, exc, tb)
        return _Ctx()
    st.status = _status

if not hasattr(st, "toggle"):
    def _toggle(label, value=False, key=None):
        # Fallback to checkbox which is widely available
        return st.checkbox(label, value=value, key=key)
    st.toggle = _toggle

# --- 1. CONFIGURATION ---
def get_secret(name, default=""):
    try:
        value=st.secrets.get(name,default)
        if value: return str(value)
    except Exception: pass
    return os.getenv(name,default)

GOOGLE_API_KEY=get_secret("GOOGLE_API_KEY","")
AI_ENABLED=bool(GOOGLE_API_KEY) and GENAI_AVAILABLE
ACTIVE_AI_MODEL=get_secret("GEMINI_MODEL","gemini-1.5-flash")
DB_PATH=get_secret("CHILLMIND_DB_PATH","chillmind.db")

def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt.encode(),180000)
    return salt,digest.hex()

def verify_password(password,salt,password_hash):
    _,candidate=hash_password(password,salt)
    return hmac.compare_digest(candidate,password_hash)

def init_database():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL,
            state_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        conn.commit()

def _json_default(value):
    if isinstance(value,datetime): return value.isoformat()
    if isinstance(value,set): return list(value)
    return str(value)

def collect_persistent_state():
    breathing=[]
    for item in st.session_state.get("breathing_history",[]):
        item=dict(item)
        if isinstance(item.get("timestamp"),datetime): item["timestamp"]=item["timestamp"].isoformat()
        breathing.append(item)
    return {
        "user_data":st.session_state.get("user_data",{}),
        "chat_history":st.session_state.get("chat_history",[])[-100:],
        "schedule_items":st.session_state.get("schedule_items",[]),
        "notifications":st.session_state.get("notifications",[])[-200:],
        "stats_data":st.session_state.get("stats_data",{}),
        "breathing_history":breathing[-200:],"breathing_streak":st.session_state.get("breathing_streak",0),
        "daily_goals":st.session_state.get("daily_goals",{}),"notif_preferences":st.session_state.get("notif_preferences",{}),
        "task_1":st.session_state.get("task_1",False),"task_2":st.session_state.get("task_2",False),"task_3":st.session_state.get("task_3",False),
        "tic_scores":st.session_state.get("tic_scores",{"X":0,"O":0}),"tic_games_played":st.session_state.get("tic_games_played",0),
        "jumble_score":st.session_state.get("jumble_score",0),"jumble_streak":st.session_state.get("jumble_streak",0),
        "memory_score":st.session_state.get("memory_score",0),"memory_level":st.session_state.get("memory_level",1),
        "word_score":st.session_state.get("word_score",0),"word_streak":st.session_state.get("word_streak",0),
        "focus_score":st.session_state.get("focus_score",0),"focus_high_score":st.session_state.get("focus_high_score",0),
        "focus_total_played":st.session_state.get("focus_total_played",0),"focus_total_correct":st.session_state.get("focus_total_correct",0),
        "rest_alerts":st.session_state.get("rest_alerts",True),"deep_work_mode":st.session_state.get("deep_work_mode",False),
        "ambience_enabled":st.session_state.get("ambience_enabled",True)
    }

def restore_persistent_state(state):
    for key,value in (state or {}).items():
        if key=="breathing_history":
            restored=[]
            for item in value or []:
                item=dict(item)
                try: item["timestamp"]=datetime.fromisoformat(item["timestamp"])
                except Exception: item["timestamp"]=datetime.now()
                restored.append(item)
            st.session_state.breathing_history=restored
        else: st.session_state[key]=value

def persist_user_state():
    username=st.session_state.get("authenticated_user")
    if not username: return
    try:
        state_json=json.dumps(collect_persistent_state(),default=_json_default)
        now=datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE users SET state_json=?,updated_at=? WHERE username=?",(state_json,now,username)); conn.commit()
    except Exception as exc: print(f"State persistence error: {exc}")

def create_user(username,password):
    username=username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}",username): return False,"Use 3–32 letters, numbers, dots, underscores, or hyphens for the username."
    if len(password)<8: return False,"Password must be at least 8 characters."
    salt,password_hash=hash_password(password); now=datetime.now().isoformat(timespec="seconds")
    state=collect_persistent_state(); state["user_data"]["username"]=username; state["user_data"]["name"]=username
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO users(username,salt,password_hash,state_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",(username,salt,password_hash,json.dumps(state,default=_json_default),now,now)); conn.commit()
        return True,"Account created."
    except sqlite3.IntegrityError: return False,"That username already exists. Please log in instead."
    except Exception as exc: print(f"Account creation error: {exc}"); return False,"Could not create the account right now."

def authenticate_user(username,password):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row=conn.execute("SELECT salt,password_hash,state_json FROM users WHERE username=?",(username.strip(),)).fetchone()
        if not row or not verify_password(password,row[0],row[1]): return False
        st.session_state.authenticated_user=username.strip(); restore_persistent_state(json.loads(row[2]))
        st.session_state.user_data["username"]=username.strip()
        return True
    except Exception as exc: print(f"Authentication error: {exc}"); return False

init_database()
if AI_ENABLED:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        available_models=[m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        for candidate in [ACTIVE_AI_MODEL,"gemini-1.5-flash","gemini-1.5-pro","gemini-pro"]:
            normalized=candidate if candidate.startswith("models/") else f"models/{candidate}"
            if normalized in available_models:
                ACTIVE_AI_MODEL=normalized.replace("models/","",1); break
    except Exception as exc: AI_ENABLED=False; print(f"AI configuration error: {exc}")

def get_active_model_name():
    return ACTIVE_AI_MODEL if AI_ENABLED else None

# --- 2.1 LOCAL INTELLIGENCE DATA ---
try:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True)
except:
    pass

def load_intents():
    if os.path.exists("intents.json"):
        with open("intents.json") as file:
            return json.load(file)
    return None

intents_data = load_intents()
stemmer = LancasterStemmer() if 'LancasterStemmer' in globals() else None

def get_intent_response(text):
    if 'nltk' not in globals() or not intents_data or not stemmer:
        return None
    
    try:
        text_words = nltk.word_tokenize(text.lower())
    except:
        return None
        
    text_stems = [stemmer.stem(w) for w in text_words]
    
    best_match = None
    max_matches = 0
    
    for intent in intents_data['intents']:
        for pattern in intent['patterns']:
            try:
                pattern_words = nltk.word_tokenize(pattern.lower())
                pattern_stems = [stemmer.stem(w) for w in pattern_words]
                matches = len(set(text_stems) & set(pattern_stems))
                if matches > max_matches:
                    max_matches = matches
                    best_match = intent
            except:
                continue
    
    if best_match and max_matches > 0:
        return random.choice(best_match['responses'])
    return None

st.set_page_config(page_title="ChillMind - Pro Student", page_icon="🎓", layout="wide", initial_sidebar_state="expanded")

if not getattr(st.rerun,"_chillmind_wrapped",False):
    _original_rerun=st.rerun
    def _persisting_rerun(*args,**kwargs):
        persist_user_state(); return _original_rerun(*args,**kwargs)
    _persisting_rerun._chillmind_wrapped=True
    st.rerun=_persisting_rerun

# --- 3. SESSION STATE INITIALIZATION ---
if 'page' not in st.session_state: st.session_state.page = 'login'
if 'authenticated_user' not in st.session_state: st.session_state.authenticated_user = None
if 'current_view' not in st.session_state: st.session_state.current_view = 'Dashboard'
if 'user_data' not in st.session_state: st.session_state.user_data = {'username': 'User', 'name': 'User', 'age': None, 'mood': 'Happy', 'join_date': datetime.now().strftime("%B %Y"), 'profile_pic': None, 'bio': '', 'streak': 0, 'goals': ''}
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'active_game' not in st.session_state: st.session_state.active_game = None
if 'active_calendar_view' not in st.session_state: st.session_state.active_calendar_view = None
if 'show_notifications' not in st.session_state: st.session_state.show_notifications = False
if 'swiped_notification' not in st.session_state: st.session_state.swiped_notification = None
if 'current_section_index' not in st.session_state: st.session_state.current_section_index = 0
if 'notifications_page' not in st.session_state: st.session_state.notifications_page = 1
if 'notifications_per_page' not in st.session_state: st.session_state.notifications_per_page = 5
if 'tic_board' not in st.session_state: st.session_state.tic_board = [""]*9
if 'tic_scores' not in st.session_state: st.session_state.tic_scores = {'X': 0, 'O': 0}
if 'tic_games_played' not in st.session_state: st.session_state.tic_games_played = 0
if 'jumble_score' not in st.session_state: st.session_state.jumble_score = 0
if 'jumble_streak' not in st.session_state: st.session_state.jumble_streak = 0
if 'memory_cards' not in st.session_state: st.session_state.memory_cards = []
if 'memory_solved' not in st.session_state: st.session_state.memory_solved = []
if 'memory_selected' not in st.session_state: st.session_state.memory_selected = []
if 'memory_moves' not in st.session_state: st.session_state.memory_moves = 0
if 'memory_score' not in st.session_state: st.session_state.memory_score = 0
if 'memory_level' not in st.session_state: st.session_state.memory_level = 1
if 'word_chain' not in st.session_state: st.session_state.word_chain = []
if 'word_score' not in st.session_state: st.session_state.word_score = 0
if 'word_streak' not in st.session_state: st.session_state.word_streak = 0
if 'last_ai_word' not in st.session_state: st.session_state.last_ai_word = ""
if 'stats_data' not in st.session_state: st.session_state.stats_data = {'Energy': 7, 'Focus': 7, 'Sleep': 7, 'Stress': 5}
if 'task_1' not in st.session_state: st.session_state.task_1 = False
if 'task_2' not in st.session_state: st.session_state.task_2 = False
if 'task_3' not in st.session_state: st.session_state.task_3 = False
if 'breathing_history' not in st.session_state: st.session_state.breathing_history = []
if 'breathing_streak' not in st.session_state: st.session_state.breathing_streak = 0
if 'focus_number' not in st.session_state: st.session_state.focus_number = ""
if 'focus_level' not in st.session_state: st.session_state.focus_level = 1
if 'focus_score' not in st.session_state: st.session_state.focus_score = 0
if 'focus_showing' not in st.session_state: st.session_state.focus_showing = False
if 'focus_streak' not in st.session_state: st.session_state.focus_streak = 0
if 'focus_total_played' not in st.session_state: st.session_state.focus_total_played = 0
if 'focus_total_correct' not in st.session_state: st.session_state.focus_total_correct = 0
if 'focus_high_score' not in st.session_state: st.session_state.focus_high_score = 0
if 'focus_digits' not in st.session_state: st.session_state.focus_digits = 3
if 'jumble_level' not in st.session_state: st.session_state.jumble_level = 1
if 'flow_level' not in st.session_state: st.session_state.flow_level = 1
if 'tic_level' not in st.session_state: st.session_state.tic_level = 1
if 'sent_notifications' not in st.session_state: st.session_state.sent_notifications = set()
if 'first_login' not in st.session_state: st.session_state.first_login = datetime.now()
if 'chat_stats' not in st.session_state: st.session_state.chat_stats = {"messages": 0, "session_start": datetime.now()}
if 'blacklisted_models' not in st.session_state: st.session_state.blacklisted_models = set()
if 'snow_theme' not in st.session_state: st.session_state.snow_theme = False

# AI/chat backend enhanced with auto-adopted Gemini model
if AI_ENABLED and 'gemini_model' not in st.session_state:
    try:
        st.session_state.gemini_model = genai.GenerativeModel(ACTIVE_AI_MODEL)
        st.session_state.chat_session = st.session_state.gemini_model.start_chat(history=[])
    except Exception as e:
        st.session_state.gemini_model = None
        st.session_state.chat_session = None
        print(f"Error initializing Gemini {ACTIVE_AI_MODEL} chat: {e}")

if 'chat_engine' not in st.session_state:
    st.session_state.chat_engine = None
if 'schedule_items' not in st.session_state: 
    st.session_state.schedule_items = [
        {"id": 1, "time": "08:00", "activity": "Morning Meditation", "icon": "🧘", "completed": False, "date": datetime.now().strftime("%Y-%m-%d")},
        {"id": 2, "time": "12:00", "activity": "Healthy Lunch", "icon": "🥗", "completed": False, "date": datetime.now().strftime("%Y-%m-%d")},
        {"id": 3, "time": "23:00", "activity": "Bed Time", "icon": "😴", "completed": False, "date": datetime.now().strftime("%Y-%m-%d")}
    ]
if 'show_add_schedule' not in st.session_state: st.session_state.show_add_schedule = False
if 'selected_date' not in st.session_state: st.session_state.selected_date = datetime.now().strftime("%Y-%m-%d")
if 'notifications' not in st.session_state: st.session_state.notifications = []
if 'notification_read' not in st.session_state: st.session_state.notification_read = []
if 'selected_month' not in st.session_state: st.session_state.selected_month = datetime.now().month
if 'selected_year' not in st.session_state: st.session_state.selected_year = datetime.now().year
if 'notif_preferences' not in st.session_state: 
    st.session_state.notif_preferences = {
        'task_reminders': True,
        'daily_tips': True,
        'achievement_alerts': True,
        'sound_effects': False
    }
if 'daily_goals' not in st.session_state:
    st.session_state.daily_goals = {
        'meditation_minutes': 5,
        'water_glasses': 8,
        'steps': 5000,
        'games_played': 2
    }
if 'theme' not in st.session_state: st.session_state.theme = "Dark"

# --- PAGINATION CLASS ---
class Pagination:
    def __init__(self, items, page, per_page):
        self.items = items
        self.total_items = len(items)
        self.per_page = per_page
        self.total_pages = math.ceil(self.total_items / per_page) if self.total_items > 0 else 1
        
        # Ensure page is within valid range
        self.current_page = max(1, min(page, self.total_pages)) if self.total_pages > 0 else 1
        
        # Calculate slices
        self.start_index = (self.current_page - 1) * self.per_page
        self.end_index = self.start_index + self.per_page
        self.paginated_items = self.items[self.start_index:self.end_index] if self.items else []

    @property
    def has_next(self):
        return self.current_page < self.total_pages

    @property
    def has_previous(self):
        return self.current_page > 1

    def get_metadata(self):
        return {
            "total_items": self.total_items,
            "total_pages": self.total_pages,
            "current_page": self.current_page,
            "per_page": self.per_page,
            "has_next": self.has_next,
            "has_previous": self.has_previous,
            "start_index": self.start_index,
            "end_index": self.end_index
        }

# --- 4. CSS STYLING (Premium Architecture) ---
st.markdown(f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
    :root {{
        --primary: #f59e0b;
        --primary-glow: rgba(245, 158, 11, 0.4);
        --secondary: #fb7185;
        --secondary-glow: rgba(251, 113, 133, 0.3);
        --accent: #f97316;
        --error: #ea580c;
        --bg-dark: #17120f;
        --card-bg: rgba(30, 41, 59, 0.4);
        --glass-border: rgba(255, 255, 255, 0.08);
        --glass-border-bright: rgba(255, 255, 255, 0.15);
        --text-main: #f8fafc;
        --text-muted: #c4b5a5;
        --sidebar-bg: #17120f;
        --transition-soft: all 0.6s cubic-bezier(0.23, 1, 0.32, 1);
        --3d-shadow: 0 50px 100px -20px rgba(0, 0, 0, 0.5);
    }}

    /* Base Reset & Typography */
    * {{ 
        font-family: 'Inter', sans-serif !important; 
        transition: var(--transition-soft);
    }}
    
    h1, h2, h3, h4, .brand-font {{
        font-family: 'Outfit', sans-serif !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
    }}

    .stApp {{
        background: linear-gradient(135deg, #17120f 0%, #2a1b14 50%, #3b2417 100%) !important;
        background-attachment: fixed !important;
        color: var(--text-main);
        overflow-x: hidden;
    }}

    /* Immersive Animated Background Elements */
    .stApp::before {{
        content: '';
        position: fixed;
        top: -20%; left: -20%;
        width: 80%; height: 80%;
        background: radial-gradient(circle, rgba(245, 158, 11, 0.08) 0%, transparent 70%);
        z-index: -1;
        animation: float-bg 30s infinite alternate ease-in-out;
    }}

    .stApp::after {{
        content: '';
        position: fixed;
        bottom: -20%; right: -20%;
        width: 80%; height: 80%;
        background: radial-gradient(circle, rgba(251, 113, 133, 0.05) 0%, transparent 70%);
        z-index: -1;
        animation: float-bg 40s infinite alternate-reverse ease-in-out;
    }}

    @keyframes float-bg {{
        0% {{ transform: translate(0, 0) scale(1); opacity: 0.5; }}
        100% {{ transform: translate(10%, 15%) scale(1.2); opacity: 1; }}
    }}

    /* Scrollbar Artistry */
    ::-webkit-scrollbar {{ width: 8px; }}
    ::-webkit-scrollbar-track {{ background: rgba(0,0,0,0.2); }}
    ::-webkit-scrollbar-thumb {{ background: rgba(255, 255, 255, 0.1); border-radius: 20px; border: 2px solid transparent; background-clip: content-box; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--primary); background-clip: content-box; }}

    /* Ultimate CSS Reset for Error Tones */
    div[data-testid="stAlert"] {{
        background-color: rgba(245, 158, 11, 0.1) !important;
        color: #f1f5f9 !important;
        border: 1px solid rgba(245, 158, 11, 0.2) !important;
        border-radius: 12px !important;
    }}
    div[data-testid="stAlert"] svg {{
        fill: var(--primary) !important;
    }}
    
    header[data-testid="stHeader"] {{ background: transparent !important; }}
    .stDecoration {{ display: none; }}
    footer {{ visibility: hidden; }}
    .block-container {{ 
        padding-top: 2rem !important; 
        padding-left: 2rem !important; 
        padding-right: 2rem !important; 
        max-width: 1200px !important; 
        margin: 0 auto !important;
    }}
    
    /* Ensure the root element of block container fills width correctly */
    .block-container > div {{
        width: 100% !important;
        max-width: 1200px !important;
    }}

    /* Fix Sidebar Size and Disable Resizer */
    [data-testid="stSidebarResizer"] {{
        display: none !important;
    }}
    
    /* Add sidebar toggle pattern back but make it completely invisible to avoid double arrow text */
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stHeader"] button,
    [data-testid="baseButton-header"],
    [data-testid="stIconMaterial"] {{
        opacity: 0 !important;
        color: transparent !important;
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        align-items: center;
        justify-content: center;
    }}
    
    [data-testid="stSidebar"] {{
        min-width: 320px !important;
        max-width: 320px !important;
    }}

    /* Ultimate Glassmorphism 4.0 */
    .glass-card, .preference-card, .achievement-card {{
        background: var(--card-bg);
        backdrop-filter: blur(24px) saturate(180%);
        -webkit-backdrop-filter: blur(24px) saturate(180%);
        border: 1px solid var(--glass-border);
        border-radius: 40px;
        padding: 2.5rem;
        box-shadow: var(--3d-shadow);
        margin-bottom: 2.5rem;
        position: relative;
        overflow: hidden;
        transition: var(--transition-soft);
    }}

    .glass-card:hover, .preference-card:hover, .achievement-card:hover {{
        border-color: var(--glass-border-bright);
        transform: translateY(-5px);
        box-shadow: 0 40px 80px -20px rgba(0, 0, 0, 0.6), 0 0 20px rgba(245, 158, 11, 0.1);
    }}

    /* Refined Specular Reflection */
    .glass-card::after, .preference-card::after, .achievement-card::after {{
        content: '';
        position: absolute;
        top: 0; left: -100%;
        width: 100%; height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.03), transparent);
        transition: 0.6s;
    }}

    .glass-card:hover::after, .preference-card:hover::after, .achievement-card:hover::after {{
        left: 100%;
    }}

    /* Dashboard & Stat Cards */
    .stat-card-premium {{
        text-align: center;
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.5), rgba(23, 18, 15, 0.7));
        padding: 1.8rem;
        border-radius: 36px;
        border: 1px solid var(--glass-border);
        transition: var(--transition-soft);
    }}

    .stat-card-premium:hover {{
        border-color: var(--primary);
        transform: translateY(-5px) scale(1.02);
    }}

    .stat-value-large {{
        font-size: 3rem;
        font-weight: 800;
        margin: 0.5rem 0;
        background: linear-gradient(135deg, #fff 30%, #c4b5a5 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }}

    .stat-label-muted {{
        color: var(--text-muted);
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        font-weight: 600;
    }}

    /* Achievement specific */
    .achievement-icon-lg {{
        font-size: 3rem;
        margin-bottom: 1rem;
        filter: drop-shadow(0 10px 15px rgba(0,0,0,0.3));
    }}

    .locked-badge {{
        filter: grayscale(1) opacity(0.5);
    }}

    /* Preference specific */
    .preference-title {{
        font-size: 1.25rem;
        font-weight: 700;
        margin-bottom: 1.5rem;
        color: var(--primary);
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }}

    /* Breathing & Tip specific */
    .breathing-tip {{
        background: rgba(245, 158, 11, 0.1);
        border: 1px solid rgba(245, 158, 11, 0.2);
        padding: 1rem 1.5rem;
        border-radius: 16px;
        margin: 1.5rem 0;
        color: var(--text-main);
        font-style: italic;
        text-align: center;
    }}

    .pulse-effect {{
        animation: pulse-slow 3s infinite ease-in-out;
    }}

    @keyframes pulse-slow {{
        0%, 100% {{ transform: scale(1); filter: brightness(1); }}
        50% {{ transform: scale(1.05); filter: brightness(1.2); }}
    }}

    /* Wellness Stats Grid Elements */
    .wellness-card-stat {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--glass-border);
        border-radius: 18px;
        padding: 1.2rem;
        text-align: center;
        transition: var(--transition-soft);
    }}

    .wellness-card-stat:hover {{
        background: rgba(255, 255, 255, 0.08);
        border-color: var(--primary);
        transform: scale(1.03);
    }}

    /* Header & Badge Styling */
    .welcome-badge {{
        background: rgba(245, 158, 11, 0.15);
        padding: 0.5rem 1.2rem;
        border-radius: 100px;
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--primary);
        border: 1px solid rgba(245, 158, 11, 0.2);
        display: inline-block;
        margin-bottom: 1rem;
    }}

    .card-icon-wrapper {{
        width: 44px; height: 44px;
        background: linear-gradient(135deg, var(--primary), #c2410c);
        border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
        font-size: 1.4rem; color: white;
        box-shadow: 0 8px 16px rgba(79, 70, 229, 0.2);
    }}

    .card-header-premium {{
        display: flex; align-items: center; gap: 1rem;
        margin-bottom: 1.5rem; padding-bottom: 1rem;
        border-bottom: 1px solid var(--glass-border);
    }}

    /* Buttons Reimagined */
    .stButton > button {{
        background: rgba(255, 255, 255, 0.08) !important;
        border: 1px solid var(--glass-border) !important;
        border-radius: 30px !important;
        padding: 0.8rem 2rem !important;
        font-weight: 600 !important;
        color: var(--text-main) !important;
        transition: var(--transition-soft) !important;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}

    .stButton > button:hover {{
        background: var(--primary) !important;
        border-color: var(--primary) !important;
        color: white !important;
        box-shadow: 0 10px 20px -5px var(--primary-glow) !important;
        transform: translateY(-2px);
    }}

    /* Navigation Pills */
    .nav-pill-container {{
        display: flex; justify-content: center; gap: 0.5rem;
        background: rgba(0,0,0,0.2); padding: 0.4rem;
        border-radius: 100px; width: fit-content; margin: 0 auto 2rem;
        border: 1px solid var(--glass-border);
    }}

    /* Responsive Mastery */
    @media (max-width: 768px) {{
        .block-container {{ padding: 1rem !important; }}
        .glass-card, .preference-card, .achievement-card {{ padding: 1.5rem; }}
        h1 {{ font-size: 2.2rem !important; }}
        .stat-value-large {{ font-size: 2.2rem; }}
        .wellness-card-stat {{ padding: 0.8rem; }}
    }}

    /* Arcade Cards */
    .action-card-mini {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 1.5rem;
        text-align: center;
        transition: var(--transition-soft);
        height: 100%;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }}
    .action-card-mini:hover {{
        background: rgba(245, 158, 11, 0.1);
        border-color: var(--primary);
        transform: translateY(-8px) scale(1.02);
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4), 0 0 15px var(--primary-glow);
    }}
    .action-icon-premium {{
        font-size: 2.5rem;
        margin-bottom: 1rem;
        filter: drop-shadow(0 5px 10px rgba(0,0,0,0.2));
    }}
    .game-lvl-badge {{
        background: var(--primary);
        color: white;
        padding: 2px 10px;
        border-radius: 50px;
        font-size: 0.75rem;
        font-weight: 700;
        margin-top: 5px;
        display: inline-block;
    }}

    /* Cybernetic Profile Rework System */
    .cyber-card {{
        background: linear-gradient(135deg, #0d0d12 0%, #1a1a2e 100%);
        border: 1px solid rgba(0, 255, 255, 0.15);
        border-radius: 32px;
        padding: 2.5rem;
        position: relative;
        overflow: hidden;
        box-shadow: inset 0 0 30px rgba(0, 255, 255, 0.03);
    }}

    .cyber-card::before {{
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%),
                    linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
        background-size: 100% 2px, 3px 100%;
        pointer-events: none;
        z-index: 2;
    }}

    .scan-line {{
        position: absolute;
        top: 0; left: 0; width: 100%; height: 2px;
        background: rgba(0, 255, 255, 0.4);
        box-shadow: 0 0 15px rgba(0, 255, 255, 0.8);
        animation: scanMove 3s infinite linear;
        z-index: 10;
        pointer-events: none;
    }}

    @keyframes scanMove {{
        0% {{ top: -2%; }}
        100% {{ top: 102%; }}
    }}

    .cyber-header {{
        font-family: 'Outfit', sans-serif;
        text-transform: uppercase;
        letter-spacing: 4px;
        color: #00ffff;
        text-shadow: 0 0 10px rgba(0, 255, 255, 0.5);
        font-weight: 800;
        margin-bottom: 2rem;
    }}

    .cyber-stat {{
        background: rgba(0, 0, 0, 0.4);
        border: 1px solid rgba(0, 255, 255, 0.1);
        padding: 1.5rem;
        text-align: center;
        transition: all 0.3s ease;
    }}

    .cyber-stat:hover {{
        background: rgba(0, 255, 255, 0.05);
        border-color: #00ffff;
        transform: translateY(-2px);
    }}

    .glitch-text {{
        position: relative;
        display: inline-block;
    }}

    .cyber-btn {{
        background: transparent !important;
        border: 1px solid #00ffff !important;
        color: #00ffff !important;
        border-radius: 0px !important;
        text-transform: uppercase !important;
        letter-spacing: 2px !important;
        font-weight: 700 !important;
        padding: 0.8rem 2rem !important;
        transition: all 0.3s ease !important;
    }}

    .cyber-btn:hover {{
        background: rgba(0, 255, 255, 0.1) !important;
        box-shadow: 0 0 20px rgba(0, 255, 255, 0.2) !important;
    }}

    .cyber-avatar-frame {{
        width: 180px; height: 180px;
        border: 1px solid #00ffff;
        padding: 10px;
        position: relative;
    }}

    .cyber-avatar-frame::before {{
        content: ""; position: absolute;
        top: -5px; left: -5px; width: 20px; height: 20px;
        border-top: 2px solid #00ffff; border-left: 2px solid #00ffff;
    }}

    .cyber-avatar-frame::after {{
        content: ""; position: absolute;
        bottom: -5px; right: -5px; width: 20px; height: 20px;
        border-bottom: 2px solid #00ffff; border-right: 2px solid #00ffff;
    }}

    /* Inputs and Forms (Dark Theme Text Visibility Fix) */
    input, textarea, select {{
        background: rgba(23, 18, 15, 0.4) !important;
        border: 1px solid var(--glass-border) !important;
        border-radius: 10px !important;
        color: #f8fafc !important;
    }}
    
    /* Ensure dropdown items are visible */
    .stSelectbox div[data-baseweb="select"] > div {{
        background-color: transparent !important;
    }}
    
    ul[data-testid="stSelectboxVirtualDropdown"] {{
        background-color: #17120f !important;
    }}
    
    ul[data-testid="stSelectboxVirtualDropdown"] li {{
        color: #f8fafc !important;
    }}

    /* Exit Button Container */
    .exit-button-container {{
        position: sticky;
        top: 0;
        z-index: 100;
        margin-bottom: 1rem;
    }}

    /* Login Background Artwork */
    .login-bg-immersive {{
        position: fixed;
        top: 0; left: 0;
        width: 100vw; height: 100vh;
        background-image: url('login_logo.png');
        background-position: center 30%;
        background-size: 60% auto;
        background-repeat: no-repeat;
        opacity: 0.12;
        z-index: -1;
        pointer-events: none;
        filter: blur(1px) saturate(1.2);
    }}

    /* Task Item Premium */
    .task-premium {{
        display: flex;
        align-items: center;
        gap: 1.2rem;
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--glass-border);
        padding: 1rem 1.5rem;
        border-radius: 18px;
        margin-bottom: 1rem;
        transition: var(--transition-soft);
    }}
    .task-premium:hover {{
        background: rgba(255, 255, 255, 0.07);
        border-color: var(--primary);
        transform: translateX(10px);
    }}
    .task-icon-mini {{
        width: 40px; height: 40px;
        display: flex; align-items: center; justify-content: center;
        background: rgba(245, 158, 11, 0.1);
        border-radius: 12px;
        font-size: 1.2rem;
    }}

    /* Dashboard Navigation Buttons */
    .nav-btn-premium {{
        background: var(--card-bg) !important;
        border: 1px solid var(--glass-border) !important;
        color: var(--text-muted) !important;
        font-weight: 600 !important;
        padding: 0.5rem 1rem !important;
        border-radius: 12px !important;
        transition: var(--transition-soft) !important;
    }}
    .nav-btn-premium-active {{
        background: var(--primary) !important;
        border-color: var(--primary) !important;
        color: white !important;
        box-shadow: 0 0 20px var(--primary-glow) !important;
    }}

    .header-btn {{
        cursor: pointer;
        transition: var(--transition-soft);
    }}
    .header-btn:hover {{
        background: rgba(245, 158, 11, 0.2) !important;
        transform: scale(1.05);
    }}

    /* Progress & Sliders */
    .stProgress > div > div > div > div {{
        background-color: var(--primary) !important;
    }}
    
    .stSlider > div > div > div > div {{
        color: var(--primary) !important;
    }}

    /* Bloom UI Extensions */
    .bloom-card {{
        background: rgba(23, 18, 15, 0.8) !important;
        backdrop-filter: blur(40px) saturate(200%) !important;
        -webkit-backdrop-filter: blur(40px) saturate(200%) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 40px !important;
        padding: 2.5rem !important;
        box-shadow: 0 50px 100px -20px rgba(0, 0, 0, 0.8), 
                    0 0 30px var(--primary-glow) !important;
        margin-bottom: 2rem !important;
        animation: bloomEntrance 0.8s cubic-bezier(0.2, 0.8, 0.2, 1);
    }}

    .bloom-title-gradient {{
        font-size: 3.5rem;
        font-weight: 900;
        background: linear-gradient(135deg, var(--primary), var(--secondary));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
        line-height: 1.1;
    }}

    .phase-badge-premium {{
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 0.5rem 1.5rem;
        border-radius: 100px;
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: 2px;
        color: var(--text-muted);
        text-transform: uppercase;
        margin-bottom: 1.5rem;
        display: inline-block;
    }}

    @keyframes bloomEntrance {{
        from {{ opacity: 0; transform: scale(0.95) translateY(20px); }}
        to {{ opacity: 1; transform: scale(1) translateY(0); }}
    }}

    @keyframes float-slow {{
        0%, 100% {{ transform: translate(0, 0); }}
        50% {{ transform: translate(2%, 3%); }}
    }}

    /* Global Tab Overrides */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 1rem;
        background-color: transparent;
        padding: 0.5rem;
    }}
    .stTabs [data-baseweb="tab"] {{
        height: 48px;
        background-color: rgba(255,255,255,0.05);
        border-radius: 24px;
        border: 1px solid rgba(255,255,255,0.05);
        padding: 0 1.5rem;
        color: rgba(255,255,255,0.6);
        font-weight: 600;
    }}
    .stTabs [aria-selected="true"] {{
        background-color: rgba(99, 102, 241, 0.2) !important;
        border-color: #ea580c !important;
        color: #f59e0b !important;
    }}

    /* Fixed Premium Sidebar */
    [data-testid="stSidebar"], [data-testid="stSidebarUserContent"], [data-testid="stSidebarNav"] {{
        background: #17120f !important;
        border-right: none !important;
        overflow-x: hidden !important;
    }}
    
    [data-testid="stSidebar"] *::-webkit-scrollbar:horizontal {{
        display: none !important;
        height: 0 !important;
    }}
    
    /* Style for native collapse button */
    [data-testid="stSidebarCollapseButton"] button {{
        background-color: rgba(245, 158, 11, 0.1) !important;
        color: var(--primary) !important;
        border: 1px solid var(--glass-border) !important;
        border-radius: 14px !important;
        margin-top: 5px !important;
    }}
    [data-testid="stSidebarCollapseButton"] button:hover {{
        background-color: var(--primary) !important;
        color: white !important;
    }}
    
    /* Ensure main content respects the fixed sidebar */
    .stMain {{
        margin-left: 0 !important;
    }}
    
    [data-testid="stSidebar"] .stButton > button {{
        background: transparent !important;
        border: 1px solid transparent !important;
        text-align: left !important;
        justify-content: flex-start !important;
        font-weight: 500 !important;
        font-size: 0.95rem !important;
        padding: 0.8rem 1rem !important;
        margin-bottom: 0.2rem !important;
        color: #c4b5a5 !important;
    }}
    
    [data-testid="stSidebar"] .stButton > button:hover {{
        background: rgba(255, 255, 255, 0.05) !important;
        border-color: rgba(255, 255, 255, 0.1) !important;
        color: #ffffff !important;
        transform: translateX(5px);
    }}

    .sidebar-brand {{
        padding: 2rem 1rem;
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 2rem;
    }}
    
    .sidebar-logo-d {{
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        font-size: 1.2rem;
        color: #ffffff;
        background: #ea580c;
        width: 32px; height: 32px;
        display: flex; align-items: center; justify-content: center;
        border-radius: 10px;
    }}
    
    .sidebar-brand-text {{
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        font-size: 1.1rem;
        letter-spacing: -0.5px;
        color: #ffffff;
    }}

    .nav-cell {{
        margin-bottom: 0.5rem;
    }}
    
    .profile-card-mini {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        padding: 1rem;
        margin-top: 2rem;
        display: flex;
        align-items: center;
        gap: 12px;
    }}
    
    .profile-avatar-mini {{
        width: 36px; height: 36px;
        background: linear-gradient(135deg, #ea580c, #f97316);
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: 0.8rem; color: white;
    }}
    
    .profile-info-mini {{
        overflow: hidden;
    }}
    .profile-name-mini {{ 
        font-size: 0.85rem; font-weight: 600; color: white;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .profile-status-mini {{ font-size: 0.7rem; color: #f59e0b; font-weight: 700; letter-spacing: 0.5px; }}

    .sidebar-divider {{
        height: 1px;
        background: linear-gradient(90deg, transparent, var(--glass-border), transparent);
        margin: 1.5rem 0;
    }}

    /* Survey & Form Elements */
    .survey-question {{
        background: rgba(255, 255, 255, 0.02);
        padding: 1.5rem;
        border-radius: 16px;
        border: 1px solid var(--glass-border);
        margin-bottom: 1.5rem;
        transition: var(--transition-soft);
    }}
    .survey-question:hover {{
        background: rgba(255, 255, 255, 0.05);
        border-color: var(--primary);
    }}

    /* Breathing Circle Animation (Global) */
    .breathing-circle {{
        width: 250px; height: 250px;
        margin: 2rem auto;
        background: conic-gradient(from 0deg, #f59e0b, #fb7185, #f59e0b);
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        animation: rotate 20s linear infinite;
        box-shadow: 0 0 50px rgba(245, 158, 11, 0.3);
        position: relative;
    }}
    .breathing-circle::before {{
        content: ""; position: absolute;
        width: 235px; height: 235px;
        background: var(--bg-dark);
        border-radius: 50%;
        backdrop-filter: blur(10px);
    }}
    .breathing-circle-inner {{
        position: relative; z-index: 2;
        color: white; font-size: 2.2rem;
        font-weight: 800; text-align: center;
    }}
    @keyframes rotate {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}

    .breathing-phase {{
        font-size: 2.5rem; font-weight: 800;
        margin: 1.5rem 0; padding: 1.2rem;
        border-radius: 50px; text-align: center;
        background: rgba(255,255,255,0.03);
        border: 1px solid var(--glass-border);
        transition: all 0.5s ease;
    }}
    .phase-inhale {{ color: #fb7185; border-color: #fb718566; box-shadow: 0 0 30px #fb718522; }}
    .phase-hold {{ color: #fbbf24; border-color: #fbbf2466; box-shadow: 0 0 30px #fbbf2422; }}
    .phase-exhale {{ color: #f59e0b; border-color: #f59e0b66; box-shadow: 0 0 30px #f59e0b22; }}
    
    .breathing-timer {{
        font-size: 4rem; font-weight: 900;
        text-align: center; font-family: 'Outfit', sans-serif;
        background: linear-gradient(135deg, #fff, rgba(255,255,255,0.4));
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}

    /* Emotion & Stat Rings */
    .stat-ring-container {{
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        width: 130px; height: 130px; border-radius: 50%;
        background: rgba(255, 255, 255, 0.02);
        border: 2px solid var(--glass-border);
        position: relative; transition: var(--transition-soft);
        margin: 0 auto;
    }}
    .stat-ring-container:hover {{
        transform: scale(1.1);
        border-color: var(--primary);
        box-shadow: 0 0 30px var(--primary-glow);
    }}
    .stat-ring-fill {{
        position: absolute; top: 0; left: 0; width: 100%; height: 100%;
        border-radius: 50%; clip-path: inset(0 0 0 0);
        border: 4px solid var(--primary);
        opacity: 0.3;
    }}
    .stat-ring-label {{
        font-size: 0.75rem; color: var(--text-muted); font-weight: 700;
        text-transform: uppercase; letter-spacing: 1px;
    }}
    .stat-ring-value {{
        font-size: 2rem; font-weight: 900; color: white; margin-top: -5px;
    }}

    .mood-orb-v3 {{
        width: 80px; height: 80px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        background: rgba(255,255,255,0.05);
        border: 1px solid var(--glass-border);
        font-size: 2rem; cursor: pointer;
        transition: var(--transition-soft);
        margin: 10px;
    }}
    .mood-orb-v3:hover {{
        background: var(--primary-glow);
        transform: scale(1.1);
        border-color: var(--primary);
    }}
    .mood-orb-active {{
        background: linear-gradient(135deg, var(--primary), #c2410c) !important;
        box-shadow: 0 0 40px var(--primary-glow);
        border-color: white !important;
        transform: scale(1.15);
    }}

    /* Circular Orbital Controls */
    .st-circle-upload [data-testid="stFileUploader"] {{
        width: 120px !important; height: 120px !important; margin: 0 auto;
    }}
    .st-circle-upload [data-testid="stFileUploader"] section {{
        border-radius: 50% !important;
        width: 120px !important; height: 120px !important;
        padding: 0 !important;
        background: rgba(255,255,255,0.03) !important;
        border: 2px dashed var(--primary) !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
        min-height: 120px !important; cursor: pointer;
        transition: var(--transition-soft);
    }}
    .st-circle-upload [data-testid="stFileUploader"] section:hover {{
        background: var(--primary-glow) !important;
        border-style: solid !important;
        transform: scale(1.05);
    }}
    .st-circle-upload [data-testid="stFileUploader"] section > div {{
        display: none !important;
    }}
    .st-circle-upload [data-testid="stFileUploader"] section::before {{
        content: "➕";
        font-size: 2.2rem;
        filter: drop-shadow(0 0 8px var(--primary-glow));
    }}

    /* Global Circular Add Button Styling */
    .orbital-add-btn button {{
        width: 60px !important; height: 60px !important;
        border-radius: 50% !important;
        padding: 0 !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
        font-size: 1.8rem !important;
        background: linear-gradient(135deg, var(--primary), #c2410c) !important;
        border: none !important;
        box-shadow: 0 8px 20px var(--primary-glow) !important;
        margin: 0 auto !important;
        transition: var(--transition-soft) !important;
    }}
    .orbital-add-btn button:hover {{
        transform: scale(1.1) rotate(90deg) !important;
        box-shadow: 0 12px 30px var(--primary-glow) !important;
    }}
</style>
""", unsafe_allow_html=True)


# --- 5. LOGIC & INTELLIGENCE ---
def get_ai_response(prompt_text, history=None, image=None, stream=False):
    """Enhanced AI response using Gemini when available, falls back to local intents."""
    
    # First try local intent response for common queries
    if not image:
        local_response = get_intent_response(prompt_text)
        if local_response:
            if stream:
                class MockChunk:
                    def __init__(self, text): self.text = text
                return [MockChunk(local_response)]
            return local_response
    
    # If Gemini is available, use it for more sophisticated responses
    if AI_ENABLED and hasattr(st.session_state, 'chat_session') and st.session_state.chat_session:
        try:
            # Build context from history if available
            context = ""
            if history and len(history) > 0:
                # Get last few messages for context
                recent = history[-3:] if len(history) > 3 else history
                context = "Previous conversation:\n"
                for msg in recent:
                    role = "Student" if msg["role"] == "user" else "Mentor"
                    context += f"{role}: {msg['content']}\n"
            
            # Create a prompt that focuses on student wellness
            full_prompt = f"""You are Chillmind, a supportive AI mentor for students. 
Your role is to help with:
- Stress management and mental wellness
- Study techniques and focus
- Motivation and encouragement
- Breathing exercises and mindfulness
- Academic challenges

Keep responses warm, encouraging, and concise (under 150 words).

{context}
Student: {prompt_text}
Mentor:"""
            
            if stream:
                # For streaming response
                response = st.session_state.chat_session.send_message(full_prompt, stream=True)
                return response
            else:
                # For single response
                response = st.session_state.chat_session.send_message(full_prompt)
                return response.text
        except Exception as e:
            print(f"Gemini API error: {e}")
            # Fall back to echo on error
            fallback = f"I'm here to help with your wellness journey. (Note: {e})"
            if stream:
                class MockChunk:
                    def __init__(self, text): self.text = text
                return [MockChunk(fallback)]
            return fallback
    
    # Ultimate fallback
    fallback_text = f"I hear you saying: '{prompt_text}'. How can I support your wellness today?"
    if stream:
        class MockChunk:
            def __init__(self, text): self.text = text
        return [MockChunk(fallback_text)]
    return fallback_text

def get_ai_model(model_name=None):
    """Get AI model instance if available."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        return st.session_state.gemini_model
    return None

def get_game_insight(game_name, game_state):
    """Get AI-powered game insight."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Give a short, encouraging tip for a student playing {game_name} game. Current state: {game_state}. Keep it under 50 words."
            response = model.generate_content(prompt)
            return response.text
        except:
            return "Keep playing and having fun! 🌟"
    return "Keep playing and having fun! 🌟"

def get_ai_jumble_hint(word):
    """Get AI hint for jumble game."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Give a short, creative hint for the word '{word}' without giving it away. Keep it under 20 words."
            response = model.generate_content(prompt)
            return response.text
        except:
            hints = [
                "Think of the first letter.",
                "It rhymes with a calm word.",
                "Associate it with a place you relax.",
                "Focus on vowels first."
            ]
            return random.choice(hints)
    return random.choice(["Think of the first letter.", "Focus on vowels first."])

def get_ai_word_chain_response(word):
    """Get AI response for word chain game."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Respond with a single word that starts with '{word[-1]}' and is related to wellness, peace, or positivity."
            response = model.generate_content(prompt)
            return response.text.strip().upper()
        except Exception as e:
            print(f"Word chain AI error: {e}")
            return random.choice(["NATURE", "PEACE", "CALM", "MIND", "SOUL", "LOVE", "HOPE", "DREAM"])
    return random.choice(["NATURE", "PEACE", "CALM", "MIND", "SOUL", "LOVE", "HOPE", "DREAM"])

def get_ai_memory_strategy(level):
    """Get AI memory game strategy tip."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Give a short memory improvement tip for level {level} of a memory matching game. Keep it under 40 words."
            response = model.generate_content(prompt)
            return response.text
        except:
            return "Try to create short stories connecting pairs. 📖"
    return "Try to create short stories connecting pairs. 📖"

def get_ai_focus_encouragement(level, score, streak):
    """Get AI encouragement for focus game."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Give a short, encouraging message for a student playing a focus game. Level: {level}, Score: {score}, Streak: {streak}. Keep it under 40 words."
            response = model.generate_content(prompt)
            return response.text
        except:
            return f"Nice work — streak {streak}! Keep focused with short, timed practice. 💪"
    return f"Nice work — streak {streak}! Keep focused with short, timed practice. 💪"

def get_wellness_tip(stats):
    """Get AI wellness tip based on stats."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Wellness stats: Sleep {stats.get('Sleep', 5)}/10, Energy {stats.get('Energy', 5)}/10, Focus {stats.get('Focus', 5)}/10, Stress {stats.get('Stress', 5)}/10. Give one specific wellness tip for a student."
            response = model.generate_content(prompt)
            return response.text
        except: 
            return "Remember to take breaks and stay hydrated! 💧"
    return "Remember to take breaks and stay hydrated! 💧"

def get_breathing_insight(session_count):
    """Get AI insight about breathing practice."""
    if AI_ENABLED and hasattr(st.session_state, 'gemini_model'):
        try:
            model = st.session_state.gemini_model
            prompt = f"Share a calming insight about the benefits of breathing exercises after {session_count} sessions. Keep it poetic and under 50 words."
            response = model.generate_content(prompt)
            return response.text
        except: 
            return "Each breath brings you closer to peace. 🥰"
    return "Each breath brings you closer to peace. 🥰"

def check_winner(board):
    lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
    for l in lines:
        if board[l[0]] == board[l[1]] == board[l[2]] and board[l[0]] != "": return board[l[0]]
    if "" not in board: return "Draw"
    return None

def computer_move_smart(board):
    for i in range(9):
        if board[i] == "":
            board[i] = 'O'
            if check_winner(board) == 'O':
                board[i] = ""
                return i
            board[i] = ""
    for i in range(9):
        if board[i] == "":
            board[i] = 'X'
            if check_winner(board) == 'X':
                board[i] = ""
                return i
            board[i] = ""
    if board[4] == "": return 4
    corners = [0, 2, 6, 8]
    available_corners = [c for c in corners if board[c] == ""]
    if available_corners: return random.choice(available_corners)
    empty = [i for i, x in enumerate(board) if x == ""]
    return random.choice(empty) if empty else None

# JUMBLE
WORDS_DB = ["SERENITY", "BALANCE", "TRANQUIL", "MINDFUL", "BREATHE", "HARMONY", 
            "PEACEFUL", "CALMNESS", "MEDITATE", "RELAXATION", "WELLNESS", "HEALING",
            "POSITIVE", "ENERGY", "FOCUS", "CLARITY", "WISDOM", "STRENGTH"]

WORDS_DB_ADV = ["REJUVENATION", "MINDFULNESS", "SERENDIPITY", "TRANQUILITY", 
                "PROSPERITY", "RESILIENCE", "COMPASSION", "GRATITUDE",
                "TRANSFORMATION", "ENLIGHTENMENT", "CONSCIOUSNESS", "EQUILIBRIUM"]

def init_jumble():
    level = st.session_state.get('jumble_level', 1)
    pool = WORDS_DB if level < 3 else WORDS_DB + WORDS_DB_ADV
    word = random.choice([w for w in pool if len(w) <= 5 + level])
    if not word: word = random.choice(pool)
    st.session_state.jumble_target = word.upper()
    scrambled = list(word)
    random.shuffle(scrambled)
    st.session_state.jumble_word = "".join(scrambled)
    st.session_state.jumble_hint = get_ai_jumble_hint(word)

# MEMORY
MEMORY_EMOJIS = ['🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐨', '🐸', '🐧', '🐦', 
                 '🐤', '🐟', '🐠', '🐡', '🐙', '🦋', '🐞', '🐝', '🌸', '🌺', '🌻', '🌿']

def init_memory_game():
    level = st.session_state.memory_level
    num_pairs = min(4 + level, 12)
    selected_emojis = random.sample(MEMORY_EMOJIS, num_pairs)
    cards = selected_emojis * 2
    random.shuffle(cards)
    st.session_state.memory_cards = cards
    st.session_state.memory_solved = []
    st.session_state.memory_selected = []
    st.session_state.memory_moves = 0

# FOCUS
def init_focus_game():
    digits = st.session_state.focus_digits
    min_num = 10 ** (digits - 1)
    max_num = (10 ** digits) - 1
    st.session_state.focus_number = str(random.randint(min_num, max_num))
    st.session_state.focus_showing = True
    st.session_state.focus_time_up = False
    st.session_state.focus_total_played += 1

def calculate_accuracy():
    if st.session_state.focus_total_played == 0:
        return 0
    return (st.session_state.focus_total_correct / st.session_state.focus_total_played) * 100

# WORD FLOW
WORD_FLOW_WORDS = ["APPLE", "HOUSE", "TREE", "BOOK", "CAR", "DOG", "CAT", "SUN", "MOON", "STAR",
                   "PEACE", "LOVE", "HOPE", "DREAM", "LIFE", "TIME", "MIND", "SOUL", "HEART"]

# Calendar & Notification Functions
def get_month_days(year, month):
    if month == 12:
        return 31
    next_month = datetime(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day.day

def get_month_start_weekday(year, month):
    return datetime(year, month, 1).weekday()

def get_events_for_date(date_str):
    return [item for item in st.session_state.schedule_items if item["date"] == date_str]

def add_notification(message, type="upcoming", icon="📅"):
    notif_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
    notif_key = f"{message}_{datetime.now().strftime('%Y-%m-%d')}"
    
    if notif_key in st.session_state.sent_notifications: return
    
    notification = {
        "id": notif_id,
        "message": message,
        "time": datetime.now().strftime("%H:%M"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "type": type,
        "read": False,
        "icon": icon
    }
    st.session_state.notifications.insert(0, notification) # Newest first
    st.session_state.notification_read.insert(0, False)
    st.session_state.sent_notifications.add(notif_key)

def get_unread_count():
    return sum(1 for read in st.session_state.notification_read if not read)

def mark_all_read():
    for i in range(len(st.session_state.notification_read)):
        st.session_state.notification_read[i] = True
    for notification in st.session_state.notifications:
        notification['read'] = True

def clear_all_notifications():
    st.session_state.notifications = []
    st.session_state.notification_read = []

def mark_notification_read(index):
    if index < len(st.session_state.notification_read):
        st.session_state.notification_read[index] = True
        st.session_state.notifications[index]['read'] = True

def delete_notification(index):
    if index < len(st.session_state.notifications):
        st.session_state.notifications.pop(index)
        st.session_state.notification_read.pop(index)

def check_missed_tasks():
    """Check for missed tasks and create notifications"""
    current_time = datetime.now()
    current_hour = current_time.hour
    current_minute = current_time.minute
    
    for item in st.session_state.schedule_items:
        if not item["completed"] and item["date"] == datetime.now().strftime("%Y-%m-%d"):
            item_hour, item_minute = map(int, item["time"].split(":"))
            # Check if task is overdue by 30+ minutes
            if current_hour > item_hour or (current_hour == item_hour and current_minute > item_minute + 30):
                add_notification(f"Missed: {item['icon']} {item['activity']} at {item['time']}", "missed", "⚠️")

def check_upcoming_tasks():
    """Check for upcoming tasks (30 minutes before)"""
    current_time = datetime.now()
    current_hour = current_time.hour
    current_minute = current_time.minute
    
    for item in st.session_state.schedule_items:
        if not item["completed"] and item["date"] == datetime.now().strftime("%Y-%m-%d"):
            item_hour, item_minute = map(int, item["time"].split(":"))
            # Check if task is in 30 minutes
            time_diff = (item_hour * 60 + item_minute) - (current_hour * 60 + current_minute)
            if 25 <= time_diff <= 35:  # Within 30 minutes range
                add_notification(f"Upcoming: {item['icon']} {item['activity']} at {item['time']}", "upcoming", "⏰")

def add_schedule_item(time,activity,icon,date):
    time=time.strip(); activity=activity.strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",time): raise ValueError("Time must use HH:MM format.")
    if not activity: raise ValueError("Activity cannot be empty.")
    next_id=max([int(item.get("id",0)) for item in st.session_state.schedule_items] or [0])+1
    st.session_state.schedule_items.append({"id":next_id,"time":time,"activity":activity,"icon":icon,"completed":False,"date":date})
    add_notification(f"New: {icon} {activity} scheduled at {time}","upcoming","📅")

def render_game_stats():
    """Renders Arcade performance metrics with premium Bloom design."""
    st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
    st.markdown('<div class="phase-badge-premium" style="margin-bottom:1rem;">Neural Metrics</div>', unsafe_allow_html=True)
    st.markdown('<h2 class="bloom-title-gradient" style="font-size:2.5rem; margin-bottom:2rem;">Arcade Performance</h2>', unsafe_allow_html=True)
    
    s_col1, s_col2, s_col3, s_col4, s_col5 = st.columns(5)
    
    # Define stat items for cleaner rendering
    stats_items = [
        ("⚔️", st.session_state.tic_scores.get('X', 0), "Tic Wins", "var(--primary)"),
        ("📝", st.session_state.jumble_score, "Jumble", "var(--secondary)"),
        ("🔍", st.session_state.memory_score, "Memory", "var(--accent)"),
        ("🧠", st.session_state.focus_score, "Focus", "#f59e0b"),
        ("🗣️", st.session_state.word_score, "Flow", "#60a5fa")
    ]
    
    cols = [s_col1, s_col2, s_col3, s_col4, s_col5]
    
    for i, (icon, val, label, color) in enumerate(stats_items):
        with cols[i]:
            st.markdown(f"""
            <div class="wellness-card-stat" style="border-bottom: 3px solid {color}; background: rgba(255,255,255,0.02); padding: 1.2rem; border-radius: 24px;">
                <div style="font-size:1.5rem; margin-bottom: 5px;">{icon}</div>
                <div style="font-size:1.5rem; font-weight:900; color:white; font-family:'Outfit';">{val}</div>
                <p style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; margin:8px 0 0 0; letter-spacing:1px; font-weight:700;">{label}</p>
            </div>
            """, unsafe_allow_html=True)
        
    st.markdown('</div>', unsafe_allow_html=True)

# --- PAGINATION RENDER FUNCTION ---
def render_pagination(pagination_obj, pagination_key="default", page_state_key="notifications_page", per_page_state_key="notifications_per_page"):
    """Render pagination controls in a single compact row with configurable state keys"""
    meta = pagination_obj.get_metadata()
    cols = st.columns([0.8, 0.8, 1.5, 0.8, 0.8, 1.2])
    
    with cols[0]:
        if st.button("⏮️", key=f"first_page_{pagination_key}", disabled=not pagination_obj.has_previous, use_container_width=True, help="First Page"):
            st.session_state[page_state_key] = 1
            st.rerun()
    
    with cols[1]:
        if st.button("◀", key=f"prev_page_{pagination_key}", disabled=not pagination_obj.has_previous, use_container_width=True, help="Previous Page"):
            st.session_state[page_state_key] -= 1
            st.rerun()
    
    with cols[2]:
        st.markdown(f"""
        <div style="text-align: center; border: 1px solid rgba(255,255,255,0.1); border-radius: 15px; padding: 4px; background: rgba(255,255,255,0.05);">
            <span style="color: var(--primary); font-weight: 600;">{meta['current_page']}</span> / {meta['total_pages']}
        </div>
        """, unsafe_allow_html=True)
    
    with cols[3]:
        if st.button("▶", key=f"next_page_{pagination_key}", disabled=not pagination_obj.has_next, use_container_width=True, help="Next Page"):
            st.session_state[page_state_key] += 1
            st.rerun()
    
    with cols[4]:
        if st.button("⏭️", key=f"last_page_{pagination_key}", disabled=not pagination_obj.has_next, use_container_width=True, help="Last Page"):
            st.session_state[page_state_key] = pagination_obj.total_pages
            st.rerun()
            
    with cols[5]:
        current_per_page = st.session_state.get(per_page_state_key, 5)
        per_page_options = [3, 5, 10, 20, 50]
        try:
            sel_index = per_page_options.index(current_per_page)
        except ValueError:
            sel_index = 1 # Default to 5
            
        items_per_page = st.selectbox(
            "Show",
            per_page_options,
            index=sel_index,
            key=f"items_per_page_{pagination_key}",
            label_visibility="collapsed"
        )
        if items_per_page != st.session_state[per_page_state_key]:
            st.session_state[per_page_state_key] = items_per_page
            st.session_state[page_state_key] = 1
            st.rerun()


    # Small caption below for item counts
    if meta['total_items'] > 0:
        start_item = meta['start_index'] + 1
        end_item = min(meta['end_index'], meta['total_items'])
        st.markdown(f'<div style="text-align: center; font-size: 0.8rem; opacity: 0.6; margin-top: 0.5rem;">Showing {start_item} - {end_item} of {meta["total_items"]} items</div>', unsafe_allow_html=True)


# --- 6. PAGES & COMPONENTS ---

def render_sidebar():
    # Render actual sidebar content permanantly
    with st.sidebar:
        # Show AI status if enabled
        if AI_ENABLED:
            st.markdown(f"""
            <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid #f59e0b; border-radius: 20px; padding: 5px 10px; margin-bottom: 15px; text-align: center;">
                <span style="color: #f59e0b; font-size: 0.7rem; font-weight: 600;">🤖 AI MENTOR • ONLINE</span>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown("""
        <style>
        @keyframes cyber-brand-glow {
            0% { text-shadow: 0 0 5px rgba(245, 158, 11, 0.2); transform: scale(1); filter: hue-rotate(0deg); }
            50% { text-shadow: 0 0 20px rgba(245, 158, 11, 0.8), 0 0 30px rgba(251, 113, 133, 0.4); transform: scale(1.04); filter: hue-rotate(15deg); }
            100% { text-shadow: 0 0 5px rgba(245, 158, 11, 0.2); transform: scale(1); filter: hue-rotate(0deg); }
        }
        .animated-Chillmind-brand {
            font-family: 'Outfit', sans-serif;
            font-weight: 900;
            font-size: 1.35rem;
            letter-spacing: 2px;
            color: white;
            animation: cyber-brand-glow 3s infinite ease-in-out;
            display: inline-block;
        }
        </style>
        <div class="sidebar-brand" style="margin-bottom: 2rem; display: flex; align-items: center;">
            <div class="sidebar-logo-d" style="background:var(--primary); box-shadow: 0 0 20px var(--primary-glow);">C</div>
            <div class="animated-Chillmind-brand">ChillMind</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
            
        nav_items = [
            ("🏠 ChillMind Home", "Dashboard"),
            ("🧠 Mentor AI", "Chat"),
            ("🎮 Focus Games", "Games"),
            ("📅 Study Planner", "Calendar"),
            ("🔔 Campus Alerts", "Notifications"),
            ("🌬️ Study Break", "Breathing"),
            ("❓ FAQ", "FAQ"),
            ("🆘 Student Support", "Help")
        ]
        
        for label, view in nav_items:
            is_active = st.session_state.current_view == view
            if st.button(label, use_container_width=True, key=f"nav_{view}", type="primary" if is_active else "secondary"):
                st.session_state.current_view = view
                if view != "Games": 
                    st.session_state.active_game = None
                st.rerun()
        
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        
        # Mini Profile at bottom
        username = st.session_state.user_data.get('username', 'User')
        st.markdown(f"""
        <div class="profile-card-mini" style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px;">
            <div class="profile-avatar-mini" style="background:var(--primary); box-shadow: 0 0 15px var(--primary-glow);">{username[0].upper()}</div>
            <div class="profile-info-mini">
                <div class="profile-name-mini" style="font-weight:700;">{username}</div>
                <div class="profile-status-mini" style="color:var(--primary); font-weight:800;">● SYNCED</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<div style='height:15px'></div>", unsafe_allow_html=True)
        
        if st.button("👤 Student Identity", use_container_width=True):
            st.session_state.current_view = "Profile"
            st.rerun()
            
        if st.button("🚪 Logout Sequence", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

# --- BLOOM UI HELPERS ---
def render_bloom_background(theme_color="#f59e0b"):
    """Applies the immersive bloom background globally to the current view."""
    st.markdown(f"""
    <div class="login-bg-immersive" style="background: radial-gradient(circle at 50% 50%, #2a1b14 0%, #17120f 100%);"></div>
    <div style="position:fixed; top:0; left:0; width:100%; height:100%; z-index:-1; overflow:hidden; opacity:0.3;">
        <div style="position:absolute; top:-10%; left:-10%; width:60%; height:60%; background:radial-gradient(circle, {theme_color} 0%, transparent 70%); filter:blur(100px); animation: bloomMove 25s infinite alternate;"></div>
        <div style="position:absolute; bottom:-10%; right:-10%; width:60%; height:60%; background:radial-gradient(circle, #c2410c 0%, transparent 70%); filter:blur(100px); animation: bloomMove 30s infinite alternate-reverse;"></div>
    </div>
    <style>
        @keyframes bloomMove {{
            from {{ transform: translate(0,0) scale(1); }}
            to {{ transform: translate(5%, 10%) scale(1.1); }}
        }}
    </style>
    """, unsafe_allow_html=True)

# --- LOTTIE HELPERS ---
@st.cache_data
def load_lottieurl(url: str):
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def render_ai_module_analyzer(module_name: str, context_data: dict, bg_color: str = "rgba(249, 115, 22, 0.05)"):
    if not globals().get('AI_ENABLED', False) or 'gemini_model' not in st.session_state or st.session_state.gemini_model is None:
        return
        
    st.markdown(f'''
    <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 2rem; background: {bg_color}; border: 1px solid rgba(249, 115, 22, 0.2); box-shadow: 0 10px 30px rgba(249, 115, 22, 0.1);">
        <div class="card-header-premium" style="margin-bottom: 1rem; border: none; padding-bottom: 0;">
            <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #f97316, #fb7185);">🧠</div>
            <div>
                <h3 style="margin:0; font-size: 1.4rem;">AI Neural Analysis</h3>
                <p style="margin:0; font-size: 0.8rem; color: var(--text-muted);">Synthesize insights for {module_name}</p>
            </div>
        </div>
    ''', unsafe_allow_html=True)
    
    if st.button(f"Generate Neural Synthesis ({module_name})", key=f"btn_ai_synth_{module_name.replace(' ', '_')}", use_container_width=True):
        with st.spinner("Synthesizing data patterns..."):
            try:
                context_str = str(context_data)
                prompt = f"You are the 'Chillmind AI', an advanced cyberpunk/zen neural assistant. Analyze this user data for the '{module_name}' module. Keep it extremely brief (2-3 sentences max) and provide ONE specific, actionable piece of advice. Data: {context_str}"
                response = st.session_state.gemini_model.generate_content(prompt)
                st.markdown(f'''
                <div style="background: rgba(0,0,0,0.3); padding: 1.5rem; border-radius: 16px; border-left: 4px solid #f97316; margin-top: 1rem;">
                    <p style="color: white; font-size: 1.05rem; line-height: 1.6; margin: 0; font-family: 'Inter', sans-serif;">{response.text}</p>
                </div>
                ''', unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Neural uplink failed: {e}")
    st.markdown('</div>', unsafe_allow_html=True)

def page_login():
    main_zen=load_lottieurl("https://lottie.host/8086027c-02cf-46c5-9c98-132b8fa58025/vNlHOnP25P.json")
    render_bloom_background(theme_color="#f59e0b")
    st.markdown("""<style>.block-container{display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:100vh!important;padding-top:0!important}.auth-note{color:#c4b5a5;font-size:.82rem;text-align:center;margin-top:.75rem}</style>""",unsafe_allow_html=True)
    _,col,_=st.columns([1,1.8,1])
    with col:
        st.markdown('<div class="bloom-card" style="padding:3.5rem;">',unsafe_allow_html=True)
        if main_zen: st_lottie(main_zen,height=240,key="login_anim_final")
        st.markdown("""<div style="text-align:center;margin-bottom:2rem"><div class="phase-badge-premium">STUDENT ACCESS</div><h1 class="bloom-title-gradient" style="font-size:4.5rem;margin:.5rem 0">Chillmind</h1><p style="color:#c4b5a5">A calm workspace for study, wellness and focus.</p></div>""",unsafe_allow_html=True)
        mode=st.radio("Account mode",["Log in","Create account"],horizontal=True,label_visibility="collapsed")
        user=st.text_input("Username",key="login_user",placeholder="Enter your username")
        pw=st.text_input("Password",type="password",key="login_pw",placeholder="At least 8 characters")
        if mode=="Create account":
            confirm=st.text_input("Confirm password",type="password",key="login_pw_confirm",placeholder="Repeat your password")
            if st.button("Create account 🌱",type="primary",use_container_width=True):
                if pw!=confirm: st.error("Passwords do not match.")
                else:
                    ok,msg=create_user(user,pw)
                    if ok: authenticate_user(user,pw); st.session_state.page="survey"; st.rerun()
                    else: st.error(msg)
        else:
            if st.button("Log in ⚡",type="primary",use_container_width=True):
                if not user or not pw: st.error("Username and password are required.")
                elif authenticate_user(user,pw):
                    st.session_state.page="survey" if not st.session_state.user_data.get("onboarded") else "main"; st.session_state.current_view="Dashboard"; st.rerun()
                else: st.error("Incorrect username or password.")
        st.markdown('<div class="auth-note">Account data is stored locally in the app database. AI activates only when GOOGLE_API_KEY is configured.</div>',unsafe_allow_html=True)
        st.markdown('</div>',unsafe_allow_html=True)
    st.markdown("""<div style="position:fixed;bottom:20px;left:0;right:0;text-align:center;opacity:.3;font-size:10px;letter-spacing:2px">LOCAL-FIRST WELLNESS WORKSPACE • © 2026</div>""",unsafe_allow_html=True)

def page_survey():
    """Immersive Bloom Survey - Specialized organic UI with high-end animations."""
    lottie_bloom = load_lottieurl("https://lottie.host/9e4d588a-d790-410a-b28e-5b23456230f2/7D98KIsUoR.json")
    
    if 'survey_step' not in st.session_state:
        st.session_state.survey_step = -1
        st.session_state.survey_data = {}

    # Bloom Immersive Background
    st.markdown("""
    <div class="login-bg-immersive" style="background: radial-gradient(circle at 50% 50%, #2a1b14 0%, #17120f 100%);"></div>
    <div style="position:fixed; top:0; left:0; width:100%; height:100%; z-index:-1; overflow:hidden; opacity:0.4;">
        <div style="position:absolute; top:-10%; left:-10%; width:50%; height:50%; background:radial-gradient(circle, #f59e0b 0%, transparent 70%); filter:blur(80px); animation: bloomMove 15s infinite alternate;"></div>
        <div style="position:absolute; bottom:-10%; right:-10%; width:50%; height:50%; background:radial-gradient(circle, #ea580c 0%, transparent 70%); filter:blur(80px); animation: bloomMove 20s infinite alternate-reverse;"></div>
    </div>
    <style>
        @keyframes bloomMove {
            from { transform: translate(0,0) scale(1); }
            to { transform: translate(10%, 15%) scale(1.2); }
        }
    </style>
    """, unsafe_allow_html=True)
    
    themes = [
        {"color": "#f59e0b", "glow": "rgba(245, 158, 11, 0.4)", "bg": "linear-gradient(135deg, #f59e0b, #ea580c)"}, # Intro/Emotional
        {"color": "#f97316", "glow": "rgba(167, 139, 250, 0.3)", "bg": "linear-gradient(135deg, #f97316, #8b5cf6)"}, # Vitality
        {"color": "#fb7185", "glow": "rgba(251, 113, 133, 0.3)", "bg": "linear-gradient(135deg, #fb7185, #059669)"}  # Balance
    ]
    
    theme_idx = max(0, min(st.session_state.survey_step, len(themes) - 1))
    cur_theme = themes[theme_idx]

    st.markdown(f"""
    <style>
        .block-container {{
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
            align-items: center !important;
            min-height: 100vh !important;
            padding: 2rem !important;
        }}
        
        [data-testid="column"]:has(.bloom-survey-card) {{
            background: rgba(23, 18, 15, 0.4) !important;
            backdrop-filter: blur(24px) saturate(160%) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 50px !important;
            padding: 2.5rem !important;
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.5) !important;
            width: 100% !important;
            max-width: 900px !important;
            margin: auto !important;
            animation: bloomEntrance 0.8s ease-out;
        }}

        @keyframes bloomEntrance {{
            from {{ opacity: 0; transform: scale(0.9) translateY(40px); }}
            to {{ opacity: 1; transform: scale(1) translateY(0); }}
        }}

        .bloom-question-card {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 1.2rem;
            margin-bottom: 0.8rem;
            transition: all 0.3s ease;
        }}
        .bloom-question-card:hover {{
            background: rgba(255, 255, 255, 0.04);
            border-color: {cur_theme['color']}44;
        }}

        .bloom-title {{
            font-size: 2.8rem;
            font-weight: 800;
            background: {cur_theme['bg']};
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.8rem;
            line-height: 1.1;
        }}

        .phase-badge {{
            background: {cur_theme['color']}22;
            color: {cur_theme['color']};
            padding: 4px 12px;
            border-radius: 50px;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 2px;
            text-transform: uppercase;
            border: 1px solid {cur_theme['color']}44;
            display: inline-block;
            margin-bottom: 1.5rem;
        }}

        /* Survey Component Overrides */
        .survey-label {{
            font-size: 1rem;
            font-weight: 600;
            color: {cur_theme['color']};
            margin-bottom: 0.8rem;
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }}
        
        div[data-testid="stRadio"] [role="radiogroup"] {{
            gap: 1.2rem !important;
        }}
        
        div[data-testid="stRadio"] label {{
            background: transparent !important;
            border: none !important;
            padding: 4px 5px !important;
            font-size: 0.85rem !important;
            color: #c4b5a5 !important;
            border-bottom: 1px solid transparent !important;
            transition: all 0.3s ease;
        }}
        
        div[data-testid="stRadio"] label:hover {{
            color: {cur_theme['color']} !important;
        }}
        
        /* Note: Streamlit's internal layout can be tricky to override for 'selected' state 
           without affecting accessibility, so we keep it clean. */
    </style>
    """, unsafe_allow_html=True)

    total_steps = 3
    step_titles = ["Mindset & Focus", "Study Energy", "Academic flow"]
    
    _, main_container, _ = st.columns([1, 14, 1])
    
    with main_container:
        st.markdown('<div class="bloom-survey-card"></div>', unsafe_allow_html=True)
        
        if st.session_state.survey_step == -1:
            # Centered Landing Layout
            _, center_col, _ = st.columns([1, 10, 1])
            with center_col:
                st.markdown('<div style="text-align: center;">', unsafe_allow_html=True)
                if lottie_bloom: 
                    st_lottie(lottie_bloom, height=300, key="bloom_init")
                
                st.markdown(f"""
                    <div style="margin-top: 1rem;">
                        <span class="phase-badge">Student Induction</span>
                        <h1 class="bloom-title">Academic Alignment</h1>
                        <p style="font-size: 1.4rem; color: #fff; font-weight: 700; margin-bottom: 0.5rem; letter-spacing: 1px;">Welcome.</p>
                        <p style="font-size: 1.2rem; color: #c4b5a5; line-height: 1.6; margin-bottom: 1.5rem; max-width: 600px; margin-left: auto; margin-right: auto;">
                            Take a moment to answer 10 focused questions to help personalize your space.
                        </p>
                        <p style="font-size: 1.1rem; color: {cur_theme['color']}; opacity: 0.9; font-weight: 600; letter-spacing: 2px; margin-bottom: 2.5rem;">YOUR JOURNEY STARTS HERE</p>
                    </div>
                """, unsafe_allow_html=True)
                
                if st.button("Get Started ✨", type="primary", use_container_width=True):
                    st.session_state.survey_step = 0
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

        elif 0 <= st.session_state.survey_step < total_steps:
            # Simplified Single-Column Layout for better alignment
            st.markdown(f"""
                <div style="text-align: center; margin-bottom: 3rem;">
                    <span class="phase-badge">Step {st.session_state.survey_step + 1} of {total_steps}</span>
                    <h2 class="bloom-title" style="font-size: 3.2rem; margin-top: 0.5rem;">{step_titles[st.session_state.survey_step]}</h2>
                    <div style="display: flex; justify-content: center; gap: 10px; margin-top: 1rem;">
                        {"".join([f'<div style="width: 40px; height: 4px; border-radius: 10px; background: {"#fff" if i == st.session_state.survey_step else "rgba(255,255,255,0.1)"}; transition: 0.5s ease;"></div>' for i in range(total_steps)])}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            # Center column for questions
            _, q_container, _ = st.columns([1, 8, 1])
            
            with q_container:
                if st.session_state.survey_step == 0:
                    # EMOTIONAL
                    with st.container():
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>1️⃣</span> How are you feeling today?</div>', unsafe_allow_html=True)
                        st.radio("q1", ["😊 Great", "😐 Okay", "😔 Low", "😤 Stressed"], horizontal=True, key="bq1", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>2️⃣</span> Do you practice mindfulness or meditation?</div>', unsafe_allow_html=True)
                        st.radio("q7", ["Daily", "Sometimes", "Rarely"], horizontal=True, key="bq7", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>3️⃣</span> How motivated are you to study right now?</div>', unsafe_allow_html=True)
                        st.radio("q10", ["High", "Medium", "Low"], horizontal=True, key="bq10", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)

                elif st.session_state.survey_step == 1:
                    # VITALITY
                    with st.container():
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>4️⃣</span> How is your energy level today?</div>', unsafe_allow_html=True)
                        st.radio("q2", ["Very High", "Moderate", "Low"], horizontal=True, key="bq2", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>5️⃣</span> How well did you sleep last night?</div>', unsafe_allow_html=True)
                        st.radio("q3", ["Excellent", "Average", "Poor"], horizontal=True, key="bq3", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>6️⃣</span> How often do you exercise?</div>', unsafe_allow_html=True)
                        st.radio("q5", ["Regularly", "Sometimes", "Not really"], horizontal=True, key="bq5", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)

                elif st.session_state.survey_step == 2:
                    # BALANCE
                    with st.container():
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>7️⃣</span> How well can you focus when studying?</div>', unsafe_allow_html=True)
                        st.radio("q4", ["Very well", "Okay", "Hard to focus"], horizontal=True, key="bq4", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>8️⃣</span> How balanced is your study/life routine?</div>', unsafe_allow_html=True)
                        st.radio("q6", ["Balanced", "Getting by", "Overwhelmed"], horizontal=True, key="bq6", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>9️⃣</span> How stressed are you about academics?</div>', unsafe_allow_html=True)
                        st.radio("q8", ["Not much", "A little", "Very stressed"], horizontal=True, key="bq8", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        st.markdown(f'<div class="bloom-question-card"><div class="survey-label"><span>🔟</span> How is your social life at school?</div>', unsafe_allow_html=True)
                        st.radio("q9", ["Great", "Okay", "Could be better"], horizontal=True, key="bq9", label_visibility="collapsed")
                        st.markdown('</div>', unsafe_allow_html=True)

                # Control Buttons
                st.markdown("<div style='height:3rem;'></div>", unsafe_allow_html=True)
                b_c1, b_c2 = st.columns(2)
                with b_c1:
                    if st.button("← Previous Phase", use_container_width=True):
                        st.session_state.survey_step -= 1
                        st.rerun()
                with b_c2:
                    label = "Sync Profile ✨" if st.session_state.survey_step == 2 else "Continue →"
                    if st.button(label, type="primary", use_container_width=True):
                        st.session_state.survey_step += 1
                        st.rerun()

        else:
            # Simple loading screen
            st.markdown(f"""
                <div style="text-align:center; padding: 2rem;">
                    <h1 class="bloom-title-gradient" style="font-size:2.4rem; margin-bottom:0.5rem;">Setting up your space... ✨</h1>
                    <p style="color:#c4b5a5; font-size:1rem;">Personalizing your dashboard based on your answers.</p>
                </div>
            """, unsafe_allow_html=True)

            progress_bar = st.progress(0)
            status_text = st.empty()
            steps = [
                "Saving your preferences...",
                "Personalizing your dashboard...",
                "Almost ready!",
            ]
            for i in range(101):
                progress_bar.progress(i)
                if i < 40:
                    status_text.markdown(f"<p style='text-align:center; color:#c4b5a5;'>{steps[0]}</p>", unsafe_allow_html=True)
                elif i < 80:
                    status_text.markdown(f"<p style='text-align:center; color:#c4b5a5;'>{steps[1]}</p>", unsafe_allow_html=True)
                else:
                    status_text.markdown(f"<p style='text-align:center; color:#c4b5a5;'>{steps[2]}</p>", unsafe_allow_html=True)
                time.sleep(0.03)
            
            percent = 100
            time.sleep(0.3)

            # Update Session State with collected data
            mood_map = {
                'Great': 'Happy',
                'Okay': 'Neutral',
                'Low': 'Sad',
                'Stressed': 'Anxious'
            }
            raw_mood = st.session_state.get('bq1', 'Okay').split()[-1]
            mapped_mood = mood_map.get(raw_mood, 'Neutral')
            
            st.session_state.user_data.update({
                'name': st.session_state.user_data.get('username', 'Student'),
                'mood': mapped_mood
            })
            st.session_state.stats_data = {
                "Energy": 9 if "High" in st.session_state.get('bq2', '') else 6,
                "Focus": 8 if "Very well" in st.session_state.get('bq4', '') else 5,
                "Sleep": 8 if "Excellent" in st.session_state.get('bq3', '') else 7,
                "Stress": 3 if "Not much" in st.session_state.get('bq8', '') else 7
            }
            
            st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
            col1, col2, col3 = st.columns([1.5, 1, 1.5])
            with col2:
                if st.button("Go to Dashboard 🎓", type="primary", use_container_width=True):
                    st.session_state.user_data['onboarded']=True
                    persist_user_state()
                    st.session_state.page = 'main'
                    st.rerun()

def page_main():
    render_sidebar()

    # DASHBOARD - BLOOM UPGRADE
    if st.session_state.current_view == "Dashboard":
        render_bloom_background(theme_color="#f59e0b")
        
        # Check for missed and upcoming tasks
        check_missed_tasks()
        check_upcoming_tasks()
        
        # Get user data
        username = st.session_state.user_data.get('name', st.session_state.user_data.get('username', 'Friend'))
        current_time = datetime.now()
        hour = current_time.hour
        
        # Determine greeting based on time
        if hour < 12: greeting, greeting_emoji = "Golden Morning", "🌅"
        elif hour < 17: greeting, greeting_emoji = "Radiant Afternoon", "☀️"
        elif hour < 21: greeting, greeting_emoji = "Serene Evening", "🌆"
        else: greeting, greeting_emoji = "Deep Night", "🌙"
        
        # Mood-based message
        mood = st.session_state.user_data.get('mood', 'Happy')
        mood_messages = {
            'Happy': "Your focus is peak radiant! ✨",
            'Neutral': "You are in perfect study equilibrium. 🌱",
            'Anxious': "Inhale calm, exhale the exam stress. 💪",
            'Sad': "ChillMind is here to support your journey. 🌈"
        }
        mood_message = mood_messages.get(mood, "Stay balanced, stay productive. 🌟")
        
        notification_count = get_unread_count()
        sections = ["Overview", "Daily Tasks", "Wellness Tracker", "Schedule"]
        
        if st.session_state.current_section_index >= len(sections):
            st.session_state.current_section_index = 0

        # ========== BLOOM HEADER CARD ==========
        st.markdown(f"""
        <div class="bloom-card" style="padding: 2.2rem; margin-bottom: 1.5rem; position: relative; overflow: hidden;">
            <div style="position: absolute; top:0; right:0; width: 150px; height: 150px; background: radial-gradient(circle, var(--primary-glow) 0%, transparent 70%); opacity: 0.3; z-index: 0;"></div>
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1.5rem; position: relative; z-index: 1;">
                <div style="flex: 1; min-width: 280px;">
                    <div class="phase-badge-premium" style="margin-bottom: 0.8rem; background: rgba(245, 158, 11, 0.1); border-color: rgba(245, 158, 11, 0.3); color: #f59e0b;">{greeting_emoji} {greeting}</div>
                    <h1 class="bloom-title-gradient" style="font-size: 3.5rem; margin: 0; line-height: 1.1; letter-spacing: -1px;">
                        Hello, {username}
                    </h1>
                    <p style="font-size: 1.1rem; opacity: 0.8; font-weight: 300; letter-spacing: 0.5px; margin-top: 0.6rem; color: #c4b5a5;">
                        {mood_message}
                    </p>
                </div>
                <div style="background: rgba(23, 18, 15, 0.6); backdrop-filter: blur(10px); border: 1px solid rgba(245, 158, 11, 0.2); padding: 1.2rem 1.8rem; border-radius: 24px; display: flex; align-items: center; gap: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2);">
                    <div style="width: 45px; height: 45px; background: var(--primary); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; box-shadow: 0 0 15px var(--primary-glow);">🔔</div>
                    <div>
                        <div style="font-weight: 900; color: white; font-size: 1.4rem; line-height: 1;">{notification_count}</div>
                        <div style="font-size: 0.65rem; color: var(--text-muted); text-transform: uppercase; font-weight: 800; letter-spacing: 1.5px; margin-top: 3px;">Active Alerts</div>
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Clickable header stats row
        stat_col1, stat_col2, stat_col3 = st.columns(3)
        with stat_col1:
            if st.button(f"🔥 {st.session_state.user_data.get('streak', 0)} Day Streak", use_container_width=True, help="Your wellness streak"):
                st.session_state.current_view = "Profile"
                st.rerun()
        with stat_col2:
            if st.button(f"🧘 {len(st.session_state.breathing_history)} Meditations", use_container_width=True, help="Total breathing sessions"):
                st.session_state.current_view = "Breathing"
                st.rerun()
        with stat_col3:
            total_score = (
                st.session_state.tic_scores.get('X', 0) * 100 + 
                st.session_state.jumble_score + 
                st.session_state.memory_score + 
                st.session_state.focus_score + 
                st.session_state.word_score
            )
            if st.button(f"🏆 {total_score} Pts", use_container_width=True, help="Total arcade points"):
                st.session_state.current_view = "Games"
                st.rerun()
        
        # Handle URL parameters for navigation
        query_params = st.query_params
        if 'notifications' in query_params:
            st.session_state.current_view = "Notifications"
            st.query_params.clear()
            st.rerun()
        elif 'profile' in query_params:
            st.session_state.current_view = "Profile"
            st.query_params.clear()
            st.rerun()
        elif 'prev_section' in query_params:
            st.session_state.current_section_index = (st.session_state.current_section_index - 1) % len(sections)
            st.query_params.clear()
            st.rerun()
        elif 'next_section' in query_params:
            st.session_state.current_section_index = (st.session_state.current_section_index + 1) % len(sections)
            st.query_params.clear()
            st.rerun()
        
        # ========== CLEAN SECTION NAVIGATION ==========
        st.markdown('<div style="margin-bottom: 25px;">', unsafe_allow_html=True)
        nav_cols = st.columns([0.5] + [1] * len(sections) + [0.5])
        
        with nav_cols[0]:
            if st.button("◀", use_container_width=True, key="prev_section_btn"):
                st.session_state.current_section_index = (st.session_state.current_section_index - 1) % len(sections)
                st.rerun()
        
        for i, section_name in enumerate(sections):
            with nav_cols[i+1]:
                is_current = st.session_state.current_section_index == i
                btn_label = f"{section_name}"
                # We use the type parameter to distinguish active state visually in Streamlit
                if st.button(btn_label, key=f"pg_sec_{i}", type="primary" if is_current else "secondary", use_container_width=True):
                    st.session_state.current_section_index = i
                    st.rerun()
                        
        with nav_cols[-1]:
            if st.button("▶", use_container_width=True, key="next_section_btn"):
                st.session_state.current_section_index = (st.session_state.current_section_index + 1) % len(sections)
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Show current section info in a more integrated way
        st.markdown(f"""
        <div style="text-align: center; margin-top: -10px; margin-bottom: 25px;">
            <div class="phase-badge-premium" style="padding: 6px 20px; font-size: 0.9rem;">
                {sections[st.session_state.current_section_index].upper()} // {st.session_state.current_section_index + 1} OF {len(sections)}
            </div>
        </div>
        """, unsafe_allow_html=True)
        

        
        # ========== MAIN CONTENT BASED ON SELECTED SECTION ==========
        current_section = sections[st.session_state.current_section_index]
        
        if current_section == "Overview":
            col1, col2 = st.columns(2)
            
            with col1:
                # Inject AI Analyzer into Overview Module
                render_ai_module_analyzer("Dashboard Overview", {
                    "energy": st.session_state.stats_data.get('Energy', 7),
                    "focus": st.session_state.stats_data.get('Focus', 6),
                    "mood": st.session_state.user_data.get('mood', 'Happy'),
                    "streak": st.session_state.user_data.get('streak', 0)
                })
            
                # Wellness Overview Header
                st.markdown('''
                <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 1.5rem;">
                    <div class="card-header-premium" style="margin-bottom: 0; border: none; padding-bottom: 0;">
                        <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #f97316, #f59e0b);">📈</div>
                        <div>
                            <h3 style="margin:0; font-size: 1.4rem;">Wellness Check-in</h3>
                            <p style="margin:0; font-size: 0.8rem; color: var(--text-muted);">Current recorded wellness signals</p>
                        </div>
                    </div>
                </div>
                ''', unsafe_allow_html=True)
                
                # Only show recorded values; never fabricate historical performance.
                wellness_df=pd.DataFrame({"Metric":["Energy","Focus","Sleep","Stress"],"Score":[st.session_state.stats_data.get("Energy",0),st.session_state.stats_data.get("Focus",0),st.session_state.stats_data.get("Sleep",0),st.session_state.stats_data.get("Stress",0)]}).set_index("Metric")
                st.bar_chart(wellness_df,height=300,use_container_width=True)
                st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
            
            with col2:
                # Mood Tracker Mini
                st.markdown('''
                <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 1.5rem;">
                    <div class="card-header-premium" style="margin-bottom: 0; border: none; padding-bottom: 0;">
                        <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #fb7185, #fb7185);">🎭</div>
                        <h3 style="margin:0; font-size: 1.4rem;">Mood State</h3>
                    </div>
                </div>
                ''', unsafe_allow_html=True)
                
                mood = st.session_state.user_data.get('mood', 'Happy')
                mood_colors = {'Happy': '#fb7185', 'Neutral': '#f59e0b', 'Anxious': '#fbbf24', 'Sad': '#f97316'}
                mood_emojis = {"Happy":"😊","Neutral":"😐","Anxious":"😰","Sad":"😔"}
                color = mood_colors.get(mood, '#f59e0b')
                emoji = mood_emojis.get(mood, "🧘")
                
                st.markdown(f"""
                <div class="bloom-card" style="text-align:center; padding:2rem; margin-bottom: 1.5rem; background: radial-gradient(circle at center, {color}11 0%, transparent 70%); border-color: {color}33;">
                    <div class="pulse-effect" style="font-size:4.5rem; margin-bottom:1rem; filter: drop-shadow(0 0 15px {color}44);">{emoji}</div>
                    <div style="font-size:1.8rem; font-weight:900; color:{color}; letter-spacing:2px; text-shadow: 0 0 20px {color}33;">{mood.upper()}</div>
                    <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; margin-top:8px; font-weight:800; letter-spacing:2px;">Neural Equilibrium</div>
                </div>
                """, unsafe_allow_html=True)
                
                if st.button("Update Resonance", use_container_width=True, key="dash_update_mood"):
                    st.session_state.current_view = "Profile"
                    st.rerun()

                # Quick Wisdom
                quotes = [
                    "Breathe in peace, breathe out tension.",
                    "Your progress is valid, no matter how small.",
                    "Mindfulness is the key to deep clarity.",
                    "The soul knows how to find its own balance."
                ]
                st.markdown(f"""
                <div class="bloom-card" style="padding:1.5rem; text-align:center; border-style: dashed; border-color: rgba(255,255,255,0.1);">
                    <div style="font-size:0.75rem; letter-spacing:3px; color:var(--primary); font-weight:900; margin-bottom:12px; text-transform:uppercase; opacity:0.8;">Daily Resonance</div>
                    <p style="font-size:1.05rem; font-weight:300; line-height:1.6; color:white; font-style: italic;">"{quotes[datetime.now().timetuple().tm_yday % len(quotes)]}"</p>
                </div>
                """, unsafe_allow_html=True)
            
        elif current_section == "Daily Tasks":
            st.markdown('''
            <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 2rem;">
                <div class="card-header-premium" style="margin-bottom: 1.5rem;">
                    <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #f59e0b, #fb7185);">✅</div>
                    <h3 style="margin:0; font-size: 1.4rem;">Wellness Checklist</h3>
                </div>
            ''', unsafe_allow_html=True)
            
            tasks = [
                ("task_1", "💧 Drink 8 Glasses of Water", "Maintain cellular hydration"),
                ("task_2", "🧘 5-Minute Meditation", "Reset your internal frequency"),
                ("task_3", "🚶 15-Minute Nature Walk", "Sync with natural rhythms")
            ]
            
            for key, title, desc in tasks:
                t_col1, t_col2 = st.columns([0.15, 1])
                with t_col1:
                    val = st.checkbox("", value=st.session_state[key], key=f"check_{key}", label_visibility="collapsed")
                    if val != st.session_state[key]:
                        st.session_state[key] = val
                        st.rerun()
                with t_col2:
                    status_style = "opacity: 0.4; filter: grayscale(1); border-color: rgba(255,255,255,0.05);" if st.session_state[key] else ""
                    st.markdown(f"""
                    <div class="task-premium" style="{status_style} margin-top: -5px; margin-bottom: 0.8rem;">
                        <div class="task-icon-mini" style="color: #f59e0b;">✦</div>
                        <div>
                            <div style="font-weight:700; font-size:1.05rem; color:var(--text-main);">{title}</div>
                            <div style="font-size:0.85rem; color:var(--text-muted); opacity: 0.8;">{desc}</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Progress bar
            completed_count = sum([st.session_state.task_1, st.session_state.task_2, st.session_state.task_3])
            st.markdown(f"<div style='margin: 1.5rem 0 0.5rem 0; font-weight:700; display:flex; justify-content:space-between; font-size: 0.85rem; color: #c4b5a5;'><span>Daily Goal Metric</span><span>{completed_count}/3 Completed</span></div>", unsafe_allow_html=True)
            st.progress(completed_count/3)
            
            if completed_count == 3:
                st.success("✨ Peak Alignment! You've achieved all system objectives for today!")
            
            st.markdown('</div>', unsafe_allow_html=True)
            
        elif current_section == "Schedule":
            st.markdown('''
            <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 1.5rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0;">
                    <div class="card-header-premium" style="border:none; margin:0; padding:0;">
                        <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #fbbf24, #f59e0b);">📅</div>
                        <h3 style="margin:0; font-size: 1.4rem;">Today's Agenda</h3>
                    </div>
                </div>
            ''', unsafe_allow_html=True)
            
            # Action button for calendar
            if st.button("View Full Calendar 🗓️", use_container_width=True, key="dash_cal_view"):
                st.session_state.current_view = "Calendar"
                st.rerun()

            today = datetime.now().strftime("%Y-%m-%d")
            today_items = [item for item in st.session_state.schedule_items if item.get("date") == today]
            
            if today_items:
                st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
                for item in sorted(today_items, key=lambda x: x['time']):
                    completed = item.get('completed', False)
                    opacity = 0.5 if completed else 1.0
                    st.markdown(f"""
                    <div class="task-premium" style="opacity:{opacity}; margin-bottom: 0.8rem; border-color: rgba(255,255,255,0.05);">
                        <div style="font-weight:800; color:var(--primary); font-size:1.1rem; min-width:65px; letter-spacing:-0.5px;">{item['time']}</div>
                        <div style="font-size:1.4rem; margin:0 0.8rem;">{item['icon']}</div>
                        <div style="flex-grow:1; font-weight:600; font-size:1rem; color: #f1f5f9;">{item['activity']}</div>
                        <div style='font-size:1.1rem;'>{'✅' if completed else '⏳'}</div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.markdown('<p style="color: var(--text-muted); text-align: center; padding: 2rem 0;">System state: No prioritized tasks for this cycle.</p>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
            
        elif current_section == "Wellness Tracker":
            st.markdown('''
            <div class="bloom-card" style="padding: 1.5rem; margin-bottom: 1.5rem;">
                <div class="card-header-premium" style="margin-bottom: 2rem;">
                    <div class="card-icon-wrapper" style="background: linear-gradient(135deg, #f97316, #8b5cf6);">📊</div>
                    <h3 style="margin:0; font-size: 1.4rem;">Precision Analytics</h3>
                </div>
            ''', unsafe_allow_html=True)
            
            metrics = [
                ("⚡ Energy", f"{st.session_state.stats_data.get('Energy', 7)}", "var(--primary)"),
                ("🎯 Focus", f"{st.session_state.stats_data.get('Focus', 6)}", "var(--secondary)"),
                ("😴 Sleep", f"{st.session_state.stats_data.get('Sleep', 8)}", "var(--accent)"),
                ("😰 Stress", f"{st.session_state.stats_data.get('Stress', 4)}", "var(--error)")
            ]
            
            w_cols = st.columns(4)
            for i, (label, val, color) in enumerate(metrics):
                with w_cols[i]:
                    st.markdown(f"""
                <div class="stat-ring-container" style="border-color: {color}44; width: 110px; height: 110px;">
                    <div class="stat-ring-label" style="font-size: 0.6rem;">{label.split()[1]}</div>
                    <div class="stat-ring-value" style="font-size: 1.6rem;">{val}</div>
                    <div style="position:absolute; bottom:-10px; width:30%; height:2px; background:{color}; border-radius:10px;"></div>
                </div>
                    """, unsafe_allow_html=True)
            
            st.markdown("<div style='height:2rem'></div>", unsafe_allow_html=True)
            if st.button("📋 Calibrate Vital Metrics", use_container_width=True, key="dash_calibrate"):
                st.session_state.survey_step = -1
                st.session_state.page = 'survey'
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Show arcade performance here too with integrated header
            render_game_stats()

        # ========== MOTIVATIONAL QUOTE ==========
        quotes = [
            "🌿 Peace is the result of retraining your mind to process life as it is.",
            "💫 You are enough. You have always been enough.",
            "⭐ Difficult roads often lead to beautiful destinations.",
            "🌟 Wellness is the complete presence of mind, body, and soul.",
            "✨ Your health is an investment, not an expense.",
        ]
        
        st.markdown(f"""
        <div style="text-align:center; padding:2rem; margin-top:2rem; border-top:1px solid var(--glass-border);">
            <div style="font-size:2rem; margin-bottom:1rem;">🕊️</div>
            <div style="font-style:italic; font-size:1.1rem; color:var(--text-muted);">"{random.choice(quotes)}"</div>
        </div>
        """, unsafe_allow_html=True)

    # CHAT - MENTOR AI
    elif st.session_state.current_view == "Chat":
        render_bloom_background(theme_color="#f97316")
        st.markdown("""<div class="bloom-card" style="text-align:center"><div class="phase-badge-premium">MENTOR AI</div><h1 class="bloom-title-gradient">A calmer study conversation</h1><p style="color:var(--text-muted)">Ask about studying, focus, routines, or a difficult day.</p></div>""",unsafe_allow_html=True)
        if not AI_ENABLED: st.warning("Mentor AI is offline. Add GOOGLE_API_KEY to Streamlit secrets or the environment.")
        for msg in st.session_state.chat_history[-30:]:
            with st.chat_message(msg.get("role","assistant")): st.markdown(msg.get("content",""))
        prompt=st.chat_input("Talk to your mentor…")
        if prompt:
            prompt=prompt.strip()
            if prompt:
                st.session_state.chat_history.append({"role":"user","content":prompt})
                if AI_ENABLED:
                    with st.chat_message("assistant"):
                        with st.spinner("Thinking…"):
                            response=get_ai_response(prompt,history=st.session_state.chat_history[:-1])
                        response=response.text if hasattr(response,"text") else response; st.markdown(response)
                else:
                    response="AI chat needs a configured GOOGLE_API_KEY."; st.chat_message("assistant").write(response)
                st.session_state.chat_history.append({"role":"assistant","content":response}); persist_user_state(); st.rerun()
        if st.session_state.chat_history and st.button("Clear conversation",key="clear_chat"):
            st.session_state.chat_history=[]; persist_user_state(); st.rerun()

    # ARCADE - BLOOM VERSION
    elif st.session_state.current_view == "Games":
        render_bloom_background(theme_color="#fb7185")
        st.markdown(f"""
        <div class="bloom-card" style="text-align:center;">
            <div class="phase-badge-premium">Cognitive Lab</div>
            <h1 class="bloom-title-gradient">Mind Arcade</h1>
            <p style="opacity:0.8;">Sharpen your neural pathways through focused play.</p>
        </div>
        """, unsafe_allow_html=True)
        
        
        # Get user info for personalization
        username = st.session_state.user_data.get('name', st.session_state.user_data.get('username', 'Player'))
        if st.session_state.user_data.get('profile_pic'):
            avatar_html = f'<img src="{st.session_state.user_data["profile_pic"]}" style="width:40px; height:40px; border-radius:50%; object-fit:cover;">'
        else:
            avatar_html = f'<div style="width:40px; height:40px; border-radius:50%; background:var(--primary); display:flex; align-items:center; justify-content:center; color:white; font-size:1.2rem;">👤</div>'

        if st.session_state.active_game is None:
            # Selection Screen
            st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
            st.markdown('<div class="card-header-premium">', unsafe_allow_html=True)
            st.markdown('<div class="card-icon-wrapper">🎲</div>', unsafe_allow_html=True)
            st.markdown('<h3 style="margin:0; font-size:2rem;">Neural Training Center</h3>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
            
            game_cols = st.columns(3)
            games_data = [
                ("🎯 Mind Focus", "FOCUS", "Train your recall & memory", "🧠"),
                ("🔤 Word Jumble", "JUMBLE", "Unscramble wellness terms", "📝"),
                ("🃏 Card Match", "MEMORY", "Classic pairs matching game", "🔍"),
                ("⭕ TicTacToe", "TIC", "Beat the smart wellness AI", "⚔️"),
                ("🌊 Word Flow", "FLOW", "Chain words for vocabulary", "🗣️")
            ]
            
            for idx, (title, key, desc, icon) in enumerate(games_data):
                with game_cols[idx % 3]:
                    best_score = st.session_state.get(f'{key.lower()}_high_score', st.session_state.get(f'{key.lower()}_score', 0))
                    st.markdown(f"""
                    <div class="action-card-mini">
                        <div class="action-icon-premium">{icon}</div>
                        <h4 style="margin:0; font-family:'Outfit';">{title}</h4>
                        <p style="font-size:0.85rem; color:var(--text-muted); margin:0.5rem 0 0.8rem 0;">{desc}</p>
                        <div style="display:flex; flex-direction:column; align-items:center; gap:5px;">
                            <div class="game-lvl-badge" style="background:rgba(255,255,255,0.1); border:1px solid var(--primary);">LVL {st.session_state.get(key.lower()+'_level', 1)}</div>
                            <div style="font-size:0.7rem; color:var(--primary); font-weight:600;">BEST: {best_score}</div>
                            <div style="font-size:0.6rem; opacity:0.5; text-transform:uppercase; letter-spacing:1px; margin-top:4px;">👤 {username}</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"Play {title.split()[-1]}", key=f"start_{key}", use_container_width=True):
                        st.session_state.active_game = key
                        if key == "MEMORY": 
                            init_memory_game()
                        if key == "JUMBLE": init_jumble()
                        if key == "FLOW": 
                            st.session_state.last_ai_word = "PEACE"
                            st.session_state.word_chain = ["🤖 AI: START WITH PEACE"]
                            st.session_state.word_score = 0
                            st.session_state.word_streak = 0
                        if key == "TIC": st.session_state.tic_board = [""]*9
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            render_game_stats()
                
            st.markdown("<br>", unsafe_allow_html=True)

        else:
            game_info = {
                "TIC": ("⭕ TicTacToe", "⚔️"),
                "FOCUS": ("🎯 Mind Focus", "🧠"),
                "JUMBLE": ("🔤 Word Jumble", "📝"),
                "MEMORY": ("🃏 Card Match", "🔍"),
                "FLOW": ("🌊 Word Flow", "🗣️")
            }
            g_title, g_icon = game_info.get(st.session_state.active_game, ("Game", "🎮"))
            
            # Active Game Header Card
            st.markdown(f"""
            <div class="bloom-card" style="padding: 1.5rem 2rem; margin-bottom: 1.5rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 1.5rem;">
                        <div class="card-icon-wrapper" style="width:60px; height:60px; font-size:2rem;">{g_icon}</div>
                        <div>
                            <h3 style="margin:0; font-size:2rem;">{g_title}</h3>
                            <div style="display:flex; align-items:center; gap:8px; font-size:0.9rem; opacity:0.8;">
                                {avatar_html} <span>Linked: <b>{username}</b></span>
                            </div>
                        </div>
                    </div>
                    <div class="phase-badge-premium" style="margin-bottom:0;">
                        LEVEL {st.session_state.get(st.session_state.active_game.lower() + '_level', st.session_state.get('focus_digits', 3))}
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            pass # Sidebar navigation handles exit state now
            
            st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
            
            # Dispatch to specific games
            if st.session_state.active_game == "TIC":
                st.markdown("### TicTacToe vs AI")
                board = st.session_state.tic_board
                cols = st.columns(3)
                for i in range(9):
                    with cols[i % 3]:
                        if board[i] == "":
                            if st.button(" ", key=f"tic_{i}", use_container_width=True):
                                board[i] = "X"
                                w = check_winner(board)
                                if not w:
                                    comp_move = computer_move_smart(board)
                                    if comp_move is not None: board[comp_move] = "O"
                                st.rerun()
                        else:
                            st.button(board[i], key=f"tic_{i}", disabled=True, use_container_width=True)
                
                winner = check_winner(board)
                if winner == "X": 
                    st.success("You Won!"); 
                    # st.balloons() removed
                    st.session_state.tic_scores['X'] += 1
                    st.session_state.tic_level += 1
                elif winner == "O": 
                    st.error("AI Won!")
                    st.session_state.tic_level = max(1, st.session_state.tic_level - 1)
                elif winner == "Draw": st.info("Draw!")
                
                if st.button("Reset Board"):
                    st.session_state.tic_board = [""]*9
                    st.rerun()

            # --- FOCUS GAME logic ---
            elif st.session_state.active_game == "FOCUS":
                col1, col2 = st.columns([1, 1])
                accuracy = (st.session_state.focus_total_correct / st.session_state.focus_total_played * 100) if st.session_state.focus_total_played > 0 else 0
                
                with col1:
                    st.markdown(f"### Level: {st.session_state.focus_digits} Digits")
                    st.markdown(f"#### Streak: {st.session_state.focus_streak} 🔥")
                with col2:
                    st.markdown(f"### Score: {st.session_state.focus_score}")
                    st.markdown(f"#### High: {st.session_state.focus_high_score}")

                if not st.session_state.focus_showing:
                    if st.button("🚀 Show Number", type="primary", use_container_width=True):
                        st.session_state.focus_number = "".join([str(random.randint(0, 9)) for _ in range(st.session_state.focus_digits)])
                        st.session_state.focus_showing = True
                        st.session_state.focus_total_played += 1
                        st.rerun()
                else:
                    st.markdown(f"<div style='text-align:center; font-size:4.5rem; letter-spacing:10px; font-weight:900; color:var(--primary); margin:2rem 0;'>{st.session_state.focus_number}</div>", unsafe_allow_html=True)
                    st.write("Memorize quickly!")
                    time.sleep(1.5)
                    st.session_state.focus_showing = False
                    st.rerun()
                
                if st.session_state.focus_number and not st.session_state.focus_showing:
                    ans = st.text_input("Enter what you saw:", key="focus_input")
                    if st.button("Submit Guess"):
                        if ans == st.session_state.focus_number:
                            # st.balloons() removed
                            st.success("Correct!")
                            st.session_state.focus_score += 10 * st.session_state.focus_digits
                            st.session_state.focus_streak += 1
                            if st.session_state.focus_streak % 2 == 0: 
                                st.session_state.focus_digits += 1
                                st.session_state.focus_level = st.session_state.focus_digits - 2
                            if st.session_state.focus_score > st.session_state.focus_high_score: st.session_state.focus_high_score = st.session_state.focus_score
                        else:
                            st.error(f"Incorrect! The number was {st.session_state.focus_number}")
                            st.session_state.focus_streak = 0
                            st.session_state.focus_digits = max(3, st.session_state.focus_digits - 1)
                        st.session_state.focus_number = ""
                        time.sleep(1)
                        st.rerun()

            # --- JUMBLE GAME logic ---
            elif st.session_state.active_game == "JUMBLE":
                st.markdown(f"### Unscramble: `{st.session_state.jumble_word}`")
                st.write(f"Hint: {st.session_state.jumble_hint}")
                guess = st.text_input("Answer:", key="jumble_input").upper()
                if st.button("Check Jumble"):
                        if guess == st.session_state.jumble_target:
                            # st.balloons() removed
                            st.session_state.jumble_score += 50
                            st.session_state.jumble_streak += 1
                            if st.session_state.jumble_streak % 3 == 0:
                                st.session_state.jumble_level += 1
                            st.success(f"Bravo! New Score: {st.session_state.jumble_score}")
                            init_jumble()
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Try again!")
                if st.button("Skip"):
                    init_jumble()
                    st.rerun()

            # --- MEMORY GAME logic ---
            elif st.session_state.active_game == "MEMORY":
                total_pairs = len(st.session_state.memory_cards) // 2
                pairs_found = len(st.session_state.memory_solved) // 2
                st.markdown(f"### Pair Match: {pairs_found}/{total_pairs} found")
                
                cols = st.columns(4)
                for i in range(len(st.session_state.memory_cards)):
                    with cols[i % 4]:
                        if i in st.session_state.memory_solved:
                            st.button(st.session_state.memory_cards[i], key=f"solved_{i}", disabled=True, use_container_width=True)
                        elif i in st.session_state.memory_selected:
                            st.button(st.session_state.memory_cards[i], key=f"sel_{i}", use_container_width=True)
                        else:
                            if st.button("❓", key=f"card_{i}", use_container_width=True):
                                st.session_state.memory_selected.append(i)
                                if len(st.session_state.memory_selected) == 2:
                                    st.rerun()
                
                if len(st.session_state.memory_selected) == 2:
                    i1, i2 = st.session_state.memory_selected
                    if st.session_state.memory_cards[i1] == st.session_state.memory_cards[i2]:
                        st.session_state.memory_solved.extend([i1, i2])
                        st.session_state.memory_score += 100
                        st.success("Match!")
                    else:
                        st.error("Mismatch!")
                    time.sleep(1)
                    st.session_state.memory_selected = []
                    st.rerun()
                
                if pairs_found == total_pairs:
                    st.snow()
                    st.success(f"Board Cleared! Moving to Level {st.session_state.memory_level + 1}...")
                    if st.button("Start Next Level"):
                        st.session_state.memory_level += 1
                        init_memory_game()
                        st.rerun()

            # WORD FLOW
            elif st.session_state.active_game == "FLOW":
                st.markdown(f"### Score: {st.session_state.word_score}  |  Streak: {st.session_state.word_streak} 🔥")
                for msg in st.session_state.word_chain[-6:]:
                    st.write(msg)
                # Ensure last_ai_word exists
                if not st.session_state.get('last_ai_word'):
                    st.session_state.last_ai_word = "PEACE"
                
                req_letter = st.session_state.last_ai_word[-1]
                st.info(f"🔤 Enter a word starting with '{req_letter.upper()}'")
                user_word = st.text_input("Your word:", key="flow_word").upper()
                if st.button("✅ Submit", use_container_width=True):
                    if user_word and user_word[0] == req_letter.upper():
                        st.session_state.word_chain.append(f"👤 You: {user_word}")
                        st.session_state.word_score += len(user_word) * 5
                        st.session_state.word_streak += 1
                        with st.spinner("AI thinking..."):
                                ai_word = get_ai_word_chain_response(user_word)
                                if ai_word and ai_word[0] == user_word[-1]:
                                    st.session_state.word_chain.append(f"🤖 AI: {ai_word}")
                                    st.session_state.last_ai_word = ai_word
                                else:
                                    # Fix: Ensure fallback word starts with the required letter
                                    valid_fallbacks = [w for w in WORD_FLOW_WORDS if w.startswith(user_word[-1])]
                                    fallback = random.choice(valid_fallbacks) if valid_fallbacks else random.choice(WORD_FLOW_WORDS)
                                    st.session_state.word_chain.append(f"🤖 AI: {fallback}")
                                    st.session_state.last_ai_word = fallback
                        st.rerun()
                    else:
                        st.error(f"❌ Word must start with '{req_letter.upper()}'!")
                        st.session_state.word_streak = 0
            
            st.markdown('</div>', unsafe_allow_html=True) # Close game bloom-card

    # BREATHING - ENHANCED VERSION
    elif st.session_state.current_view == "Breathing":
        page_breathing()

    # FAQ - BLOOM VERSION
    # FAQ - SIMPLIFIED BLOOM VERSION
    elif st.session_state.current_view == "FAQ":
        render_bloom_background(theme_color="#f59e0b")
        st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
        st.markdown('<div class="phase-badge-premium">KNOWLEDGE BASE</div>', unsafe_allow_html=True)
        st.markdown('<h1 class="bloom-title-gradient" style="font-size:3rem;">Chillmind FAQ</h1>', unsafe_allow_html=True)
        st.markdown('<p style="opacity:0.8; margin-bottom:2rem;">Common questions about your neural wellness companion.</p>', unsafe_allow_html=True)
        
        faqs = [
            ("Is my wellness data private?", "Yes, all data is stored locally in your session and cleared upon logout."),
            ("How does the AI work?", "We use advanced Neural Engine (Gemini 1.5) to provide personalized wellness insights."),
            ("Are the games scientifically designed?", "Our games are inspired by cognitive training exercises for memory, focus, and vocabulary."),
            ("Can I use this on mobile?", "Absolutely! The interface is fully responsive and optimized for all devices.")
        ]
        
        for q, a in faqs:
            with st.expander(f"✨ {q}"):
                st.write(a)
        st.markdown('</div>', unsafe_allow_html=True)

    # HELP - SIMPLIFIED BLOOM VERSION
    elif st.session_state.current_view == "Help":
        render_bloom_background(theme_color="#ea580c")
        st.markdown('<div class="bloom-card" style="text-align:center;">', unsafe_allow_html=True)
        st.markdown('<div class="phase-badge-premium">SUPPORT ORACLE</div>', unsafe_allow_html=True)
        st.markdown('<h1 class="bloom-title-gradient" style="font-size:3rem; background:linear-gradient(135deg, #ea580c, #f59e0b); -webkit-background-clip:text;">Support & Resources</h1>', unsafe_allow_html=True)
        st.markdown('<p style="opacity:0.8; margin-bottom:2rem;">Need assistance with your neural experience?</p>', unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#f59e0b; margin-top:1.5rem;'>📧 App Support</h4>", unsafe_allow_html=True)
        st.markdown("""
        <div style="font-size:1.1rem; line-height:1.8;">
        For technical issues, feedback, or general inquiries:<br>
        <b>Email:</b> support@Chillmind.ai<br>
        <b>Discord:</b> Chillmind Community
        </div>
        """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # CALENDAR
    elif st.session_state.current_view == "Calendar":
        render_bloom_background(theme_color="#f59e0b")
        page_calendar()

    # NOTIFICATIONS
    elif st.session_state.current_view == "Notifications":
        render_bloom_background(theme_color="#c2410c")
        page_notifications()

    # PROFILE - ENHANCED VERSION
    elif st.session_state.current_view == "Profile":
        page_profile()

def page_breathing():
    """Enhanced 4-7-8 Breathing Module with better UI and features"""
    # Calculate stats
    total_sessions = len(st.session_state.breathing_history)
    
    # Calculate total breathing time (3 cycles * 19 seconds = 57 seconds per session for default)
    total_seconds = total_sessions * 57
    total_minutes = total_seconds // 60
    remaining_seconds = total_seconds % 60
    
    # Header with animation
    render_bloom_background(theme_color="#fbbf24")
    st.markdown("""
    <div class="bloom-card" style="text-align:center;">
        <div class="phase-badge-premium">MIND & BODY SYNC</div>
        <h1 class="bloom-title-gradient">Breath Flow</h1>
        <p style="font-size:1.2rem; opacity:0.9; font-weight:300;">The Ultimate Neural Relaxation Sequence</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Stats row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"""
        <div class="wellness-card-stat">
            <div style="font-size:2rem; margin-bottom:0.8rem;">🧘</div>
            <div class="stat-value-large">{total_sessions}</div>
            <p style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; font-weight:600; margin:0.5rem 0 0 0;">Total Sessions</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="wellness-card-stat">
            <div style="font-size:2rem; margin-bottom:0.8rem;">⏱️</div>
            <div class="stat-value-large" style="font-size:1.4rem;">{total_minutes}m {remaining_seconds}s</div>
            <p style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; font-weight:600; margin:0.5rem 0 0 0;">Focused Time</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        streak = st.session_state.breathing_streak
        st.markdown(f"""
        <div class="wellness-card-stat">
            <div style="font-size:2rem; margin-bottom:0.8rem;">🔥</div>
            <div class="stat-value-large">{streak}</div>
            <p style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; font-weight:600; margin:0.5rem 0 0 0;">Day Streak</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        level = min(10, total_sessions // 5 + 1)
        st.markdown(f"""
        <div class="wellness-card-stat">
            <div style="font-size:2rem; margin-bottom:0.8rem;">⭐</div>
            <div class="stat-value-large">Lvl {level}</div>
            <p style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; font-weight:600; margin:0.5rem 0 0 0;">Zen Mastery</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Main breathing card
    st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
    
    # Breathing animation circle
    st.markdown("""
    <div class="breathing-circle">
        <div class="breathing-circle-inner">
            <div>4-7-8</div>
            <div style="font-size: 1rem;">Breathing</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Breathing instructions
    st.markdown("""
    <div style="text-align: center; margin: 2rem 0;">
        <h3>Follow the guided breathing exercise below</h3>
        <div style="display: flex; justify-content: center; gap: 2rem; margin: 1rem 0;">
            <div style="text-align: center;">
                <div style="font-size: 2rem; color: #667eea;">4s</div>
                <div>Inhale</div>
            </div>
            <div style="font-size: 3rem; color: rgba(255,255,255,0.3);">→</div>
            <div style="text-align: center;">
                <div style="font-size: 2rem; color: #f093fb;">7s</div>
                <div>Hold</div>
            </div>
            <div style="font-size: 3rem; color: rgba(255,255,255,0.3);">→</div>
            <div style="text-align: center;">
                <div style="font-size: 2rem; color: #4facfe;">8s</div>
                <div>Exhale</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Session controls
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        # Session options
        session_options = st.selectbox(
            "Select Session Duration",
            ["Quick Session (3 cycles)", "Relaxation (5 cycles)", "Deep Relaxation (7 cycles)"],
            index=0
        )
        
        cycles = 3
        if "Relaxation" in session_options:
            cycles = 5
        elif "Deep" in session_options:
            cycles = 7
    
    # AI Insight (if sessions exist)
    if total_sessions > 0:
        insight = get_breathing_insight(total_sessions)
        st.markdown(f"<div class='breathing-tip'>🧘 {insight}</div>", unsafe_allow_html=True)
    
    # Start Session Button
    if st.button("🌸 Start Breathing Session", type="primary", use_container_width=True):
        with st.status("Preparing relaxation space...", expanded=False) as status:
            time.sleep(1)
            status.update(label="Space ready. Focus on your breath.", state="complete")
        
        # Create placeholders for dynamic updates
        phase_display = st.empty()
        timer_display = st.empty()
        progress_bar = st.progress(0)
        
        # Session phases with enhanced visuals
        for cycle in range(cycles):
            # Inhale Phase (4 seconds)
            with phase_display.container():
                st.markdown("""
                <div class="breathing-phase phase-inhale">
                    🌬️ Inhale Slowly...
                </div>
                """, unsafe_allow_html=True)
            
            for i in range(40):  # 4 seconds * 10 updates per second
                timer_display.markdown(f"<div class='breathing-timer'>{4 - i/10:.1f}s</div>", unsafe_allow_html=True)
                progress_bar.progress((i + 1) / 40)
                time.sleep(0.1)
            
            # Hold Phase (7 seconds)
            with phase_display.container():
                st.markdown("""
                <div class="breathing-phase phase-hold">
                    😶 Hold Your Breath...
                </div>
                """, unsafe_allow_html=True)
            
            for i in range(70):  # 7 seconds * 10 updates per second
                timer_display.markdown(f"<div class='breathing-timer'>{7 - i/10:.1f}s</div>", unsafe_allow_html=True)
                progress_bar.progress(1.0)  # Keep full during hold
                time.sleep(0.1)
            
            # Exhale Phase (8 seconds)
            with phase_display.container():
                st.markdown("""
                <div class="breathing-phase phase-exhale">
                    💨 Exhale Completely...
                </div>
                """, unsafe_allow_html=True)
            
            for i in range(80):  # 8 seconds * 10 updates per second
                timer_display.markdown(f"<div class='breathing-timer'>{8 - i/10:.1f}s</div>", unsafe_allow_html=True)
                progress_bar.progress(1.0 - (i + 1) / 80)
                time.sleep(0.1)
        
        # Clear displays
        phase_display.empty()
        timer_display.empty()
        progress_bar.empty()
        
        # Session complete celebration
        # st.balloons() removed
        st.success(f"✨ Amazing! You completed {cycles} breathing cycles!")
        
        # Update session history
        st.session_state.breathing_history.append({
            'timestamp': datetime.now(),
            'cycles': cycles,
            'duration': cycles * 19  # 4+7+8 = 19 seconds per cycle
        })
        
        # Update streak
        last_session = st.session_state.breathing_history[-1]['timestamp'].date()
        if len(st.session_state.breathing_history) > 1:
            prev_session = st.session_state.breathing_history[-2]['timestamp'].date()
            if (last_session - prev_session).days == 1:
                st.session_state.breathing_streak += 1
            elif (last_session - prev_session).days > 1:
                st.session_state.breathing_streak = 1
        else:
            st.session_state.breathing_streak = 1
        
        # Show calming message
        calm_quotes = [
            "🧘 Peace comes from within. Do not seek it without.",
            "🌅 Every breath we take, every step we make, can be filled with peace, joy, and serenity.",
            "💫 The present moment is filled with joy and happiness. If you are attentive, you will see it.",
            "🌸 Let go of your mind and then be mindful. Close your ears and then listen.",
            "🌟 Your breathing should flow like a river, like a snake moving through water."
        ]
        st.markdown(f"<div class='breathing-quote'>{random.choice(calm_quotes)}</div>", unsafe_allow_html=True)
        
        # Milestone achievements
        if total_sessions + 1 in [5, 10, 25, 50, 100]:
            st.markdown(f"""
            <div style="text-align: center; padding: 1rem; background: linear-gradient(135deg, #667eea20, #764ba220); border-radius: 50px; margin-top: 1rem;">
                🏆 <strong>Milestone Unlocked!</strong> You've completed {total_sessions + 1} breathing sessions!
            </div>
            """, unsafe_allow_html=True)
        
        time.sleep(2)
        st.rerun()
    
    st.markdown('</div>', unsafe_allow_html=True)
    
    # History and Insights Section
    if total_sessions > 0:
        st.markdown("<br>", unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
            st.markdown('<div class="card-title">📜 Session Log</div>', unsafe_allow_html=True)
            
            # Show last 5 sessions
            for session in st.session_state.breathing_history[-5:][::-1]:
                session_time = session['timestamp'].strftime("%b %d, %I:%M %p")
                cycles_count = session['cycles']
                duration = session['duration']
                
                st.markdown(f"""
                <div class="breathing-history-item">
                    <div>
                        <span style="font-weight: 600;">{session_time}</span>
                        <span style="margin-left: 1rem; color: var(--primary);">{cycles_count} cycles</span>
                    </div>
                    <div style="color: rgba(255,255,255,0.6);">{duration}s</div>
                </div>
                """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
            st.markdown('<div class="card-title">🏆 Focus Milestones</div>', unsafe_allow_html=True)
            
            achievements = []
            
            # Session count achievements
            if total_sessions >= 5:
                achievements.append(("🌱", "Beginner", "5 sessions", "unlocked"))
            else:
                achievements.append(("🌱", "Beginner", f"{5 - total_sessions} more", "locked"))
            
            if total_sessions >= 25:
                achievements.append(("🌿", "Regular", "25 sessions", "unlocked"))
            else:
                achievements.append(("🌿", "Regular", f"{25 - total_sessions} more", "locked"))
            
            if total_sessions >= 100:
                achievements.append(("🌳", "Master", "100 sessions", "unlocked"))
            else:
                achievements.append(("🌳", "Master", f"{100 - total_sessions} more", "locked"))
            
            # Streak achievements
            if st.session_state.breathing_streak >= 7:
                achievements.append(("🔥", "7-Day Streak", "Week of calm", "unlocked"))
            else:
                achievements.append(("🔥", "7-Day Streak", f"{7 - st.session_state.breathing_streak} days", "locked"))
            
            if st.session_state.breathing_streak >= 30:
                achievements.append(("⚡", "30-Day Streak", "Month of peace", "unlocked"))
            else:
                achievements.append(("⚡", "30-Day Streak", f"{30 - st.session_state.breathing_streak} days", "locked"))
            
            # Display achievements
            for icon, title, desc, status in achievements:
                opacity = "0.5" if status == "locked" else "1"
                st.markdown(f"""
                <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; opacity: {opacity};">
                    <div style="font-size: 2rem;">{icon}</div>
                    <div style="flex: 1;">
                        <div style="font-weight: 600;">{title}</div>
                        <div style="font-size: 0.8rem; color: rgba(255,255,255,0.6);">{desc}</div>
                    </div>
                    <div>{'✅' if status == 'unlocked' else '🔒'}</div>
                </div>
                """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
    
    # Tips and Resources
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">💡 Why Breathing Matters for Students</div>', unsafe_allow_html=True)
    
    benefits = [
        ("😌", "Reduces anxiety and stress"),
        ("😴", "Helps fall asleep faster"),
        ("🧠", "Improves focus and concentration"),
        ("❤️", "Lowers heart rate and blood pressure"),
        ("🔄", "Regulates nervous system"),
        ("✨", "Increases mindfulness and awareness")
    ]
    
    cols = st.columns(3)
    for i, (icon, benefit) in enumerate(benefits):
        with cols[i % 3]:
            st.markdown(f"""
            <div style="text-align: center; padding: 1rem;">
                <div style="font-size: 2rem;">{icon}</div>
                <div style="font-size: 0.9rem;">{benefit}</div>
            </div>
            """, unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)

def page_profile():
    """Next-Gen User Improvement & Analytics Hub"""
    # Base User Info
    username = st.session_state.user_data.get('name', 'Achiever')
    if not username or username.strip() == "":
        username = st.session_state.user_data.get('username', 'Achiever')
        
    focus_motto = st.session_state.user_data.get('bio', 'Constantly evolving and improving.')
    
    if 'first_login' not in st.session_state:
        st.session_state.first_login = datetime.now()
    days_active = (datetime.now() - st.session_state.first_login).days + 1
    
    # Avatar Rendering
    if st.session_state.user_data.get('profile_pic'):
        avatar_html = f'<img src="{st.session_state.user_data["profile_pic"]}" style="width:100%; height:100%; object-fit:cover;">'
    else:
        mood = st.session_state.user_data.get('mood', 'Focused')
        mood_avatars = {'Focused': '🎯', 'Energetic': '⚡', 'Tired': '🔋', 'Relaxed': '🌊', 'Stressed': '🔥', 'Happy': '✨', 'Neutral': '🧠', 'Anxious': '📈', 'Sad': '🌱'}
        avatar_html = f'<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:4rem; color:white; filter: drop-shadow(0 10px 15px rgba(0,0,0,0.3));">{mood_avatars.get(mood, "🚀")}</div>'

    # Master KPI Header
    # Master KPI Header
    html_block = f"""
<div class="bloom-card" style="margin-top: 1rem; border: 1px solid rgba(129, 140, 248, 0.4); background: linear-gradient(180deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.95));">
<div style="display: flex; flex-wrap: wrap; gap: 3rem; align-items: center; position: relative;">
<div style="position: relative;">
<div style="width: 180px; height: 180px; border-radius: 20px; padding: 4px; background: linear-gradient(135deg, #3b82f6, #8b5cf6, #ec4899); box-shadow: 0 20px 40px rgba(139, 92, 246, 0.4); transform: rotate(-3deg);">
<div style="width: 100%; height: 100%; border-radius: 16px; overflow: hidden; background: var(--bg-dark); transform: rotate(3deg);">
{avatar_html}
</div>
</div>
<div class="phase-badge-premium" style="position: absolute; bottom: -10px; left: 50%; transform: translateX(-50%); font-size: 0.8rem; box-shadow: 0 5px 15px rgba(0,0,0,0.5); font-weight: 800; background: linear-gradient(90deg, #ec4899, #8b5cf6); color: white; border: none; letter-spacing: 2px;">GROWTH STAGE</div>
</div>
<div style="flex: 1; min-width: 300px;">
<h1 class="bloom-title-gradient" style="font-size: 3.5rem; margin: 0; line-height: 1; background: linear-gradient(90deg, #fff, #a78bfa); -webkit-background-clip: text;">jaro</h1>
<div style="font-size: 1.2rem; font-weight: 500; color: #cbd5e1; margin-top: 12px; font-style: italic;">"Architecting the future."</div>
<div style="display: flex; gap: 1.5rem; margin-top: 2rem; flex-wrap: wrap;">
<div style="background: rgba(0,0,0,0.3); padding: 1rem 1.5rem; border-radius: 16px; border: 1px solid rgba(255,255,255,0.05); backdrop-filter: blur(10px); flex: 1;">
<div style="font-size: 0.75rem; color: #a78bfa; text-transform: uppercase; font-weight: 800; letter-spacing: 1.5px;">Improvement Velocity</div>
<div style="font-size: 1.8rem; font-weight: 800; color: white; margin-top: 4px; display: flex; align-items: baseline; gap: 8px;">
+12.4% <span style="font-size: 1rem; color: #34d399;">▲ High</span>
</div>
</div>
<div style="background: rgba(0,0,0,0.3); padding: 1rem 1.5rem; border-radius: 16px; border: 1px solid rgba(255,255,255,0.05); backdrop-filter: blur(10px); flex: 1;">
<div style="font-size: 0.75rem; color: #34d399; text-transform: uppercase; font-weight: 800; letter-spacing: 1.5px;">Consistency Streak</div>
<div style="font-size: 1.8rem; font-weight: 800; color: white; margin-top: 4px;">
🔥 {st.session_state.user_data.get('streak', 0)} Days
</div>
</div>
</div>
<div style="margin-top: 1.5rem; padding: 1rem 1.5rem; background: linear-gradient(90deg, rgba(52,211,153,0.1), rgba(16,185,129,0.1)); border-radius: 16px; border-left: 4px solid #10b981; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
<div style="font-size: 0.8rem; color: #10b981; text-transform: uppercase; font-weight: 800; letter-spacing: 1px; margin-bottom: 5px;">🧬 NEURAL SYNTHESIS PROTOCOL</div>
<div style="display: flex; align-items: center; justify-content: space-between;">
<div style="font-size: 1.1rem; color: white; font-weight: 600;">System Architecture Mastery • Phase 3</div>
<div style="font-size: 1rem; color: #cbd5e1;">75%</div>
</div>
<div style="width: 100%; height: 6px; background: rgba(0,0,0,0.5); border-radius: 5px; margin-top: 8px; overflow: hidden;">
<div style="height: 100%; width: 75%; background: linear-gradient(90deg, #10b981, #34d399); border-radius: 5px;"></div>
</div>
</div>
</div>
</div>
</div>
"""
    st.markdown(html_block, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Advanced Tab Interface
    tab1, tab2, tab3 = st.tabs(["🚀 Improvement Analytics", "⚙️ Identity & Identity", "🎯 Predictor Matrix"])
    
    with tab1:
        st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
        st.markdown('<div class="phase-badge-premium" style="margin-bottom: 1rem; background: linear-gradient(90deg, #3b82f6, #2dd4bf); color: white; border:none;">METRICS & INSIGHTS</div>', unsafe_allow_html=True)
        st.markdown('<h3 class="bloom-title-gradient" style="margin-bottom: 2rem; font-size: 2.2rem;">Core Improvement Analytics</h3>', unsafe_allow_html=True)
        
        ca1, ca2 = st.columns([1, 1.5])
        
        with ca1:
            st.markdown(f"""
            <div class="wellness-card-stat" style="margin-bottom: 1.5rem; border-left: 4px solid #3b82f6; background: rgba(59, 130, 246, 0.05);">
                <div style="font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; letter-spacing: 1px;">Cognitive Load Capacity</div>
                <div style="font-size: 3rem; font-weight: 900; color: white;">{st.session_state.stats_data.get('Focus', 6) * 11.2:.1f}%</div>
                <div style="font-size: 0.9rem; color: #34d399; margin-top: 5px; font-weight: 600;">↑ 4.2% from last week</div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div class="wellness-card-stat" style="margin-bottom: 1.5rem; border-left: 4px solid #8b5cf6; background: rgba(139, 92, 246, 0.05);">
                <div style="font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; letter-spacing: 1px;">Deep Work Hours</div>
                <div style="font-size: 3rem; font-weight: 900; color: white;">{st.session_state.get('focus_total_played', 0)}<span style="font-size:1.5rem; color:var(--text-muted);">h</span></div>
                <div style="font-size: 0.9rem; color: #f43f5e; margin-top: 5px; font-weight: 600;">↓ 1.1h from peak</div>
            </div>
            """, unsafe_allow_html=True)

            st.markdown(f"""
            <div class="wellness-card-stat" style="border-left: 4px solid #ec4899; background: rgba(236, 72, 153, 0.05);">
                <div style="font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; letter-spacing: 1px;">Recovery Index</div>
                <div style="font-size: 3rem; font-weight: 900; color: white;">{st.session_state.stats_data.get('Sleep', 7) * 9.8:.1f}/100</div>
                <div style="font-size: 0.9rem; color: #34d399; margin-top: 5px; font-weight: 600;">Optimal Regeneration</div>
            </div>
            """, unsafe_allow_html=True)

        with ca2:
            st.markdown('<p style="font-weight: 700; color: var(--text-main); font-size: 1.2rem; margin-bottom: 1.5rem;">Performance Trajectory (30-Day View)</p>', unsafe_allow_html=True)
            
            # Simulated complex performance data
            days = range(1, 31)
            np.random.seed(42)  # For consistent graph shape
            base_trend = np.linspace(40, 85, 30)
            noise = np.random.normal(0, 5, 30)
            performance = np.clip(base_trend + noise, 0, 100)
            
            chart_data = pd.DataFrame(
                performance,
                columns=["Improvement Score"],
                index=days
            )
            
            # Using line chart to show trajectory
            st.line_chart(chart_data, color="#8b5cf6", height=280)
            
            st.markdown("""
            <div style="display: flex; gap: 1rem; margin-top: 1.5rem; font-size: 0.9rem; color: var(--text-muted); background: rgba(255,255,255,0.02); padding: 1rem; border-radius: 12px; border: 1px solid rgba(255,255,255,0.05);">
                <div style="flex:1;">
                    <div style="color: white; font-weight: 700; margin-bottom: 5px;">Trend Analysis</div>
                    Pattern shows a steady upward trajectory. Minor dips correlate with weekends or low-sleep days. 
                    <br><strong style="color: #34d399;">Action:</strong> Maintain current workload intensity.
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("Generate Detailed AI Insights 🧠", use_container_width=True):
                    with st.spinner("Analyzing your data footprint..."):
                        time.sleep(1.5)
                        st.info("Analysis Complete: Your neuro-plasticity peaks during morning sessions. Shift complex tasks to 9AM-11AM window for a 15% efficiency boost.")
            with col_btn2:
                if st.button("Download Raw Dataset 📥", use_container_width=True):
                    st.success("Dataset exported to local machine as user_metrics_v2.csv")

        st.markdown('</div>', unsafe_allow_html=True)

    with tab2:
        st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
        col_main, col_side = st.columns([1.5, 1])
        
        with col_main:
            st.markdown('<h3 class="bloom-title-gradient" style="margin-bottom: 1.5rem; font-size: 1.8rem;">Core Identity Settings</h3>', unsafe_allow_html=True)
            with st.form("identity_profile_form"):
                f1, f2 = st.columns(2)
                with f1:
                    new_name = st.text_input("Alias / Name", value=st.session_state.user_data.get('name', ''))
                    role = st.text_input("Primary Domain", value=st.session_state.user_data.get('major', 'Software Engineering'))
                with f2:
                    m_opt = ["🎯 Focused", "⚡ High Energy", "🌊 Flow State", "🧠 Deep Thinker", "🔥 Grind Mode"]
                    c_mood_raw = st.session_state.user_data.get('mood', 'Focused')
                    
                    # Fuzzy match mood
                    c_mood = "🎯 Focused"
                    for opt in m_opt:
                        if c_mood_raw in opt:
                            c_mood = opt
                            break
                            
                    s_mood = st.selectbox("Current Operational State", m_opt, index=m_opt.index(c_mood))
                    st.markdown("<div style='height: 0.3rem;'></div>", unsafe_allow_html=True)
                    uploaded_file = st.file_uploader("Update Avatar Lens", type=['png', 'jpg', 'jpeg'], label_visibility="collapsed")
                
                b = st.text_area("Prime Directive (Motto)", st.session_state.user_data.get('bio', ''), max_chars=200, placeholder="E.g., Master the fundamentals, build the future.")
                
                st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
                if st.form_submit_button("Lock In Identity Preferences 🔐", use_container_width=True):
                    if uploaded_file is not None:
                        bytes_data = uploaded_file.getvalue()
                        b64_img = base64.b64encode(bytes_data).decode()
                        img_mime = uploaded_file.type
                        st.session_state.user_data['profile_pic'] = f"data:{img_mime};base64,{b64_img}"
                        
                    clean_mood = s_mood.split(" ")[1] if " " in s_mood else s_mood
                    st.session_state.user_data.update({'name': new_name, 'major': role, 'bio': b, 'mood': clean_mood})
                    st.success("Identity vector updated successfully! 🌌")
                    time.sleep(1)
                    st.rerun()
                    
        with col_side:
            st.markdown('<h3 class="bloom-title-gradient" style="margin-bottom: 1.5rem; font-size: 1.8rem;">System Limits</h3>', unsafe_allow_html=True)
            st.toggle("Aggressive Rest Alerts (Enforce breaks)", True)
            st.toggle("Deep Work Mode (Block non-essential UI)", False)
            st.toggle("Neuro-Acoustic Ambience", True)
            
            st.markdown("<br><hr style='opacity:0.1; border-color: white;'><br>", unsafe_allow_html=True)
            st.markdown('<div class="wellness-card-stat" style="border: 1px solid #ef4444; background: rgba(239, 68, 68, 0.05);">', unsafe_allow_html=True)
            st.markdown('<p style="color:#ef4444; font-weight:800; font-size:0.9rem;">DANGER ZONE</p>', unsafe_allow_html=True)
            if st.button("Purge All Tracking Data", type="primary", use_container_width=True):
                st.session_state.clear()
                st.warning("All metrics incinerated. Rebooting...")
                time.sleep(2)
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)
        
    with tab3:
        st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
        st.markdown('<div class="phase-badge-premium" style="margin-bottom: 1rem; background: linear-gradient(90deg, #a855f7, #ec4899); color: white; border:none;">AI FORECAST</div>', unsafe_allow_html=True)
        st.markdown('<h3 class="bloom-title-gradient" style="margin-bottom: 2rem; font-size: 2.2rem;">Future Probability Matrix</h3>', unsafe_allow_html=True)
        
        st.markdown("""
        <p style="color: var(--text-muted); font-size: 1.1rem; line-height: 1.6; margin-bottom: 2rem;">
            Based on your current improvement velocity and consistency streaks, the AI has extrapolated your skill acquisition timeline.
        </p>
        """, unsafe_allow_html=True)
        
        projections = [
            ("Week 2", "Cognitive stamina increase by 12%", "High Probability", "#34d399"),
            ("Month 1", "Mastery of current focus domain fundamentals", "Very High Probability", "#3b82f6"),
            ("Month 3", "Top 5% efficiency rating in peer group", "Medium Probability", "#f59e0b"),
            ("Year 1", "Expert tier pattern recognition and output", "Variable Probability", "#ec4899")
        ]
        
        for time_frame, outcome, prob, color in projections:
            st.markdown(f"""
            <div style="display: flex; align-items: center; justify-content: space-between; padding: 1.5rem; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05); border-radius: 16px; margin-bottom: 1rem; transition: all 0.3s ease;">
                <div style="display: flex; align-items: center; gap: 1.5rem;">
                    <div style="background: rgba(255,255,255,0.1); padding: 0.5rem 1rem; border-radius: 8px; font-weight: 800; font-family: 'Outfit', sans-serif; color: white; width: 100px; text-align: center;">
                        {time_frame}
                    </div>
                    <div style="font-size: 1.1rem; color: #e2e8f0; font-weight: 500;">
                        {outcome}
                    </div>
                </div>
                <div style="color: {color}; font-weight: 700; font-size: 0.9rem; letter-spacing: 1px; text-transform: uppercase;">
                    {prob}
                </div>
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

def page_notifications():
    """Notifications module - Refined with premium design."""
    st.markdown(f"""
    <div class="bloom-card" style="text-align:center;">
        <div class="phase-badge-premium">STUDENT ALERTS</div>
        <h1 class="bloom-title-gradient">Campus Alerts</h1>
        <p style="font-size:1.1rem; opacity:0.8; margin-top:0.5rem;">Stay updated with your academic progress</p>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.notifications:
         st.markdown('<div class="bloom-card" style="text-align:center; padding:3rem;">', unsafe_allow_html=True)
         st.markdown("### 📭 No alerts recorded")
         st.write("We'll alert you about upcoming tasks and achievements.")
         st.markdown('</div>', unsafe_allow_html=True)
         return

    # Filter actions
    st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header-premium">', unsafe_allow_html=True)
    st.markdown('<div class="card-icon-wrapper">📊</div>', unsafe_allow_html=True)
    st.markdown('<h3 style="margin:0;">Activity Overview</h3>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        st.write(f"**Total:** {len(st.session_state.notifications)}")
    with c2:
        unread = len([n for n in st.session_state.notifications if not n.get('read')])
        st.write(f"**Unread:** {unread}")
    with c3:
        if st.button("Mark All Read", use_container_width=True, key="notif_mark_all"):
            for n in st.session_state.notifications: n['read'] = True
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # Notifications List with Pagination Class
    notifs = st.session_state.notifications
    per_page = st.session_state.get('notifications_per_page', 5)
    paginator = Pagination(notifs, st.session_state.notifications_page, per_page)
    
    # Update current page in state if Pagination adjusted it
    st.session_state.notifications_page = paginator.current_page

    for i, n in enumerate(paginator.paginated_items):
        is_read = n.get('read', False)
        bg = "rgba(255,255,255,0.02)" if is_read else "rgba(245, 158, 11, 0.1)"
        border = "var(--glass-border)" if is_read else "rgba(245, 158, 11, 0.3)"
        
        msg = n.get('text') or n.get('message', 'No message')
        time_str = ""
        if 'timestamp' in n:
             time_str = n['timestamp'].strftime('%H:%M · %b %d') if isinstance(n['timestamp'], datetime) else str(n['timestamp'])
        else:
             time_str = f"{n.get('time', '')} · {n.get('date', '')}"

        actual_idx = paginator.start_index + i
        
        st.markdown(f"""
        <div class="bloom-card" style="background:{bg}; border-color:{border}; margin-bottom:0.8rem; padding:1.2rem;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; align-items:center; gap:1.2rem;">
                    <div style="font-size:1.8rem;">{n.get('icon', '🔔')}</div>
                    <div>
                        <div style="font-weight:{'normal' if is_read else 'bold'}; font-size:1rem;">{msg}</div>
                        <div style="font-size:0.8rem; color:var(--text-muted);">{time_str}</div>
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        if not is_read:
            if st.button(f"Mark as Read", key=f"read_btn_{actual_idx}"):
                n['read'] = True
                st.rerun()

    # Pagination controls using render_pagination if available or manual
    st.markdown("<br>", unsafe_allow_html=True)
    render_pagination(paginator, "notifs_pagination", "notifications_page", "notifications_per_page")

def page_calendar():
    """Calendar module with premium design."""
    st.markdown(f"""
    <div class="bloom-card" style="text-align:center;">
        <div class="phase-badge-premium">ACADEMIC TIMELINE</div>
        <h1 class="bloom-title-gradient">Study Planner</h1>
        <p style="font-size:1.1rem; opacity:0.8; margin-top:0.5rem;">Optimize your study schedule and focus sessions</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Month navigation
    month_names = ["January", "February", "March", "April", "May", "June", 
                  "July", "August", "September", "October", "November", "December"]
    
    st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header-premium">', unsafe_allow_html=True)
    st.markdown('<div class="card-icon-wrapper">📅</div>', unsafe_allow_html=True)
    st.markdown(f'<h3 style="margin:0;">{month_names[st.session_state.selected_month-1]} {st.session_state.selected_year}</h3>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button("◀ Prev", use_container_width=True, key="cal_prev"):
            if st.session_state.selected_month == 1:
                st.session_state.selected_month = 12
                st.session_state.selected_year -= 1
            else:
                st.session_state.selected_month -= 1
            st.rerun()
    with c2:
        if st.button("Today", use_container_width=True):
            st.session_state.selected_month = datetime.now().month
            st.session_state.selected_year = datetime.now().year
            st.session_state.selected_date = datetime.now().strftime("%Y-%m-%d")
            st.rerun()
    with c3:
        if st.button("Next ▶", use_container_width=True):
            if st.session_state.selected_month == 12:
                st.session_state.selected_month = 1
                st.session_state.selected_year += 1
            else:
                st.session_state.selected_month += 1
            st.rerun()
    
    # Weekdays header
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cols = st.columns(7)
    for i, day in enumerate(weekdays):
        cols[i].markdown(f"<div style='text-align:center; font-weight:700; color:var(--primary);'>{day}</div>", unsafe_allow_html=True)
    
    # Calendar grid
    days_in_month = get_month_days(st.session_state.selected_year, st.session_state.selected_month)
    start_weekday = get_month_start_weekday(st.session_state.selected_year, st.session_state.selected_month)
    
    current_day = 1
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for week in range(6):
        if current_day > days_in_month: break
        cols = st.columns(7)
        for i in range(7):
            if (week == 0 and i < start_weekday) or current_day > days_in_month:
                cols[i].markdown('<div style="height:60px;"></div>', unsafe_allow_html=True)
            else:
                date_str = f"{st.session_state.selected_year}-{st.session_state.selected_month:02d}-{current_day:02d}"
                is_today = date_str == today_str
                is_selected = date_str == st.session_state.selected_date
                
                # Check for events
                day_events = [e for e in st.session_state.schedule_items if e.get('date') == date_str]
                
                # Style logic
                bg_color = "rgba(99, 102, 241, 0.2)" if is_selected else ("rgba(255,255,255,0.05)" if not is_today else "rgba(16, 185, 129, 0.2)")
                border_color = "var(--primary)" if is_selected else "transparent"
                
                with cols[i]:
                    if st.button(f"{current_day}", key=f"day_{date_str}", use_container_width=True):
                        st.session_state.selected_date = date_str
                        st.rerun()
                    
                    if day_events:
                        st.markdown(f'<div style="text-align:center; font-size:10px; margin-top:-10px;">• {len(day_events)}</div>', unsafe_allow_html=True)
                
                current_day += 1
    st.markdown('</div>', unsafe_allow_html=True)

    # Event Details for Selected Day
    if st.session_state.selected_date:
        st.markdown('<div class="bloom-card">', unsafe_allow_html=True)
        col_ed_h, col_ed_b = st.columns([3, 1])
        with col_ed_h:
            st.subheader(f"📅 Events for {st.session_state.selected_date}")
        with col_ed_b:
            st.markdown('<div class="orbital-add-btn">', unsafe_allow_html=True)
            if st.button("➕", use_container_width=False, key="cal_add_btn", help="Synchronize New Task"):
                st.session_state.show_add_schedule = not st.session_state.show_add_schedule
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        
        if st.session_state.show_add_schedule:
            with st.form("quick_add_event"):
                c_a, c_b, c_c = st.columns([1, 2, 1])
                new_time = c_a.text_input("Time", "12:00")
                new_activity = c_b.text_input("Activity", "New Mindful Task")
                new_icon = c_c.selectbox("Icon", ["🧘", "💧", "🚶", "🥗", "😴", "📚", "💼"])
                if st.form_submit_button("Save Event", use_container_width=True):
                    add_schedule_item(new_time, new_activity, new_icon, st.session_state.selected_date)
                    st.session_state.show_add_schedule = False
                    st.success("Event added!")
                    st.rerun()
        
        day_items = [item for item in st.session_state.schedule_items if item.get("date") == st.session_state.selected_date]
        if day_items:
            for item in sorted(day_items, key=lambda x: x['time']):
                col1, col2, col3 = st.columns([1, 4, 1])
                with col1:
                    st.markdown(f"**{item['time']}**")
                with col2:
                    st.markdown(f"{item['icon']} {item['activity']}")
                with col3:
                    if st.button("🗑️", key=f"del_{item['time']}_{item['activity']}"):
                        st.session_state.schedule_items.remove(item)
                        st.rerun()
        else:
            st.info("Nothing scheduled for this day yet.")
        st.markdown('</div>', unsafe_allow_html=True)

# --- 7. ROUTING ---
# Applied based on settings
if st.session_state.get('snow_theme', False):
    if 'snow_start_time' not in st.session_state:
        st.session_state.snow_start_time = time.time()
    
    elapsed = time.time() - st.session_state.snow_start_time
    if elapsed < 15:
        # Custom "Very Smaller" Snow CSS
        st.markdown(f"""
            <style>
            @keyframes snow-fall-logic {{
                0% {{ background-position: 0px 0px, 0px 0px, 0px 0px; }}
                100% {{ background-position: 500px 1000px, 400px 400px, 300px 300px; }}
            }}
            .custom-snow-layer {{
                position: fixed;
                top: 0; left: 0;
                width: 100%; height: 100%;
                z-index: 99999;
                pointer-events: none;
                background-image: 
                    radial-gradient(0.8px 0.8px at 20% 20%, white, transparent),
                    radial-gradient(0.6px 0.6px at 50% 50%, white, transparent),
                    radial-gradient(0.7px 0.7px at 80% 80%, white, transparent),
                    radial-gradient(0.5px 0.5px at 10% 90%, white, transparent),
                    radial-gradient(0.7px 0.7px at 90% 15%, white, transparent),
                    radial-gradient(0.8px 0.8px at 35% 65%, white, transparent);
                background-size: 400px 400px;
                animation: snow-fall-logic 15s linear infinite;
                opacity: 0.5;
            }}
            </style>
            <div class="custom-snow-layer"></div>
        """, unsafe_allow_html=True)
    else:
        st.session_state.snow_theme = False
        if 'snow_start_time' in st.session_state:
            del st.session_state.snow_start_time

# Only show login page initially
if st.session_state.page == 'login': 
    page_login()
elif st.session_state.page == 'survey': 
    page_survey()
elif st.session_state.page == 'main': 
    page_main()

persist_user_state()
