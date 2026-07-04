import sqlite3

DB_NAME = "resi.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tracked_resi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            courier TEXT NOT NULL,
            resi TEXT NOT NULL,
            label TEXT,
            last_status TEXT,
            last_updated TEXT,
            UNIQUE(chat_id, courier, resi)
        )
    """)
    conn.commit()
    conn.close()

def add_resi(chat_id, courier, resi, label=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO tracked_resi (chat_id, courier, resi, label) VALUES (?, ?, ?, ?)",
            (chat_id, courier.lower(), resi, label)
        )
        conn.commit()
        result = True
    except sqlite3.IntegrityError:
        result = False
    conn.close()
    return result

def remove_resi(chat_id, resi, courier=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if courier:
        c.execute(
            "DELETE FROM tracked_resi WHERE chat_id=? AND resi=? AND courier=?",
            (chat_id, resi, courier.lower())
        )
    else:
        c.execute("DELETE FROM tracked_resi WHERE chat_id=? AND resi=?", (chat_id, resi))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_all_resi(chat_id=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if chat_id:
        c.execute(
            "SELECT id, chat_id, courier, resi, label, last_status FROM tracked_resi WHERE chat_id=?",
            (chat_id,)
        )
    else:
        c.execute("SELECT id, chat_id, courier, resi, label, last_status FROM tracked_resi")
    rows = c.fetchall()
    conn.close()
    return rows

def update_status(row_id, new_status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE tracked_resi SET last_status=?, last_updated=datetime('now') WHERE id=?",
        (new_status, row_id)
    )
    conn.commit()
    conn.close()