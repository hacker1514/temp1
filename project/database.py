import sqlite3
import os
import hashlib
import secrets

DB_PATH = os.path.join(os.path.dirname(__file__), "extensions.db")
EXTENSIONS_DIR = os.path.join(os.path.dirname(__file__), "extensions")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(EXTENSIONS_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        bio TEXT DEFAULT '',
        website TEXT DEFAULT '',
        avatar_url TEXT DEFAULT '',
        is_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        folder_name TEXT NOT NULL,
        name TEXT NOT NULL,
        version TEXT DEFAULT '1.0',
        description TEXT DEFAULT '',
        author TEXT DEFAULT '',
        website TEXT DEFAULT '',
        icon_filename TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        is_published INTEGER DEFAULT 0,
        publish_date TEXT,
        views INTEGER DEFAULT 0,
        downloads INTEGER DEFAULT 0,
        likes_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, folder_name),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, project_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    conn.close()


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return pw_hash, salt


def verify_password(password, password_hash, salt):
    h, _ = hash_password(password, salt)
    return h == password_hash


def create_session(user_id):
    token = secrets.token_hex(32)
    conn = get_db()
    conn.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (user_id, token))
    conn.commit()
    conn.close()
    return token


def get_user_by_token(token):
    if not token:
        return None
    conn = get_db()
    row = conn.execute("""
        SELECT u.* FROM users u
        JOIN sessions s ON s.user_id = u.id
        WHERE s.token = ?
    """, (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_project_folder(user_id, folder_name):
    return os.path.join(EXTENSIONS_DIR, str(user_id), folder_name)
