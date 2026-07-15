import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("ticketmp.db")

_lock = threading.Lock()

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row
cursor = db.cursor()


def save():
    with _lock:
        db.commit()


def execute(query, params=()):
    with _lock:
        cur = db.execute(query, params)
        db.commit()
        return cur


def fetchone(query, params=()):
    with _lock:
        cur = db.execute(query, params)
        return cur.fetchone()


def fetchall(query, params=()):
    with _lock:
        cur = db.execute(query, params)
        return cur.fetchall()


# =====================================================
# TABLES
# =====================================================

def create_tables():

    execute("""
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id TEXT PRIMARY KEY,
        premium INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS levels (
        guild_id TEXT,
        user_id TEXT,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS economy (
        guild_id TEXT,
        user_id TEXT,
        money INTEGER DEFAULT 0,
        bank INTEGER DEFAULT 0,
        last_daily INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS birthdays (
        guild_id TEXT,
        user_id TEXT,
        birthday TEXT,
        PRIMARY KEY (guild_id, user_id)
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT,
        user_id TEXT,
        message TEXT,
        status TEXT DEFAULT 'pending',
        upvotes INTEGER DEFAULT 0,
        downvotes INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS custom_commands (
        guild_id TEXT,
        command_name TEXT,
        response TEXT,
        PRIMARY KEY (guild_id, command_name)
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS social_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT,
        platform TEXT,
        channel_id TEXT,
        target_id TEXT
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        guild_id TEXT PRIMARY KEY,
        plan TEXT DEFAULT 'free',
        expires_at INTEGER DEFAULT 0
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT,
        reporter_id TEXT,
        target_id TEXT,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS autoroles (
        guild_id TEXT,
        role_id TEXT,
        PRIMARY KEY (guild_id, role_id)
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS reaction_roles (
        guild_id TEXT,
        message_id TEXT,
        emoji TEXT,
        role_id TEXT,
        PRIMARY KEY (
            guild_id,
            message_id,
            emoji
        )
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS recurring_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT,
        channel_id TEXT,
        message TEXT,
        interval_seconds INTEGER
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT,
        user_id TEXT,
        channel_id TEXT,
        category TEXT,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    execute("""
    CREATE TABLE IF NOT EXISTS advanced_logs (
        guild_id TEXT,
        event_type TEXT,
        channel_id TEXT,
        enabled INTEGER DEFAULT 1,
        PRIMARY KEY (
            guild_id,
            event_type
        )
    )
    """)


# =====================================================
# GUILDS
# =====================================================

def create_guild_if_missing(guild_id):

    execute("""
    INSERT OR IGNORE INTO guild_config
    (guild_id)
    VALUES (?)
    """, (str(guild_id),))


def guild_exists(guild_id):

    row = fetchone("""
    SELECT guild_id
    FROM guild_config
    WHERE guild_id=?
    """, (str(guild_id),))

    return row is not None


# =====================================================
# LEVELS
# =====================================================

def get_user_level(guild_id, user_id):

    create_guild_if_missing(guild_id)

    execute("""
    INSERT OR IGNORE INTO levels
    (guild_id,user_id)
    VALUES (?,?)
    """, (
        str(guild_id),
        str(user_id)
    ))

    row = fetchone("""
    SELECT *
    FROM levels
    WHERE guild_id=?
    AND user_id=?
    """, (
        str(guild_id),
        str(user_id)
    ))

    return row


def add_xp(
        guild_id,
        user_id,
        amount):

    get_user_level(
        guild_id,
        user_id
    )

    execute("""
    UPDATE levels
    SET xp = xp + ?
    WHERE guild_id=?
    AND user_id=?
    """, (
        amount,
        str(guild_id),
        str(user_id)
    ))


# =====================================================
# ECONOMY
# =====================================================

def get_wallet(
        guild_id,
        user_id):

    execute("""
    INSERT OR IGNORE INTO economy
    (guild_id,user_id)
    VALUES (?,?)
    """, (
        str(guild_id),
        str(user_id)
    ))

    return fetchone("""
    SELECT *
    FROM economy
    WHERE guild_id=?
    AND user_id=?
    """, (
        str(guild_id),
        str(user_id)
    ))


def add_money(
        guild_id,
        user_id,
        amount):

    get_wallet(
        guild_id,
        user_id
    )

    execute("""
    UPDATE economy
    SET money = money + ?
    WHERE guild_id=?
    AND user_id=?
    """, (
        amount,
        str(guild_id),
        str(user_id)
    ))


# =====================================================
# CUSTOM COMMANDS
# =====================================================

def add_custom_command(
        guild_id,
        command,
        response):

    execute("""
    INSERT OR REPLACE
    INTO custom_commands
    VALUES (?,?,?)
    """, (
        str(guild_id),
        command.lower(),
        response
    ))


def get_custom_command(
        guild_id,
        command):

    return fetchone("""
    SELECT *
    FROM custom_commands
    WHERE guild_id=?
    AND command_name=?
    """, (
        str(guild_id),
        command.lower()
    ))


# =====================================================
# INIT
# =====================================================

create_tables()

print("✅ Base de données chargée.")