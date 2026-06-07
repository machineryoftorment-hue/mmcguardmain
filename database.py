import sqlite3
import threading

DB_PATH = "mmcguard.db"
_lock = threading.Lock()

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _lock:
        conn = get_conn()
        cur = conn.cursor()

        # Players table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS players (
                username TEXT PRIMARY KEY,
                status TEXT
            )
        """)

        # Orders table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                content TEXT
            )
        """)

        # Explosives table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS explosives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                item TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

# -----------------------------
# PLAYER FUNCTIONS
# -----------------------------

def set_player_status(username, status):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO players (username, status)
            VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET status=excluded.status
        """, (username, status))
        conn.commit()
        conn.close()

# -----------------------------
# ORDER FUNCTIONS
# -----------------------------

def add_order(username, content):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (username, content) VALUES (?, ?)", (username, content))
        conn.commit()
        conn.close()

def get_orders(username):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT content FROM orders WHERE username = ?", (username,))
    rows = cur.fetchall()
    conn.close()
    return [row["content"] for row in rows]

def get_all_orders():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT username, content FROM orders ORDER BY username")
    rows = cur.fetchall()
    conn.close()
    return rows

# -----------------------------
# EXPLOSIVE FUNCTIONS
# -----------------------------

def log_explosive(username, item):
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO explosives (username, item) VALUES (?, ?)", (username, item))
        conn.commit()
        conn.close()
