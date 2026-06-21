"""
database.py
------------
All SQLite access for SamvadX lives here: schema, connection helper,
and the default-data seed (a sample admin account).

SQLite keeps the whole app zero-setup: no external DB server, just
`python app.py`. WAL mode is enabled so the WebSocket chat writes and
the feed reads don't block each other under light concurrent use.
"""

import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vaultgram.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT,
    password        TEXT NOT NULL,
    profile_pic     TEXT DEFAULT 'default.png',
    bio             TEXT DEFAULT '',
    is_blocked      INTEGER DEFAULT 0,
    is_private      INTEGER DEFAULT 0,
    last_seen       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    public_key      TEXT,
    failed_logins   INTEGER DEFAULT 0,
    locked_until    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT UNIQUE NOT NULL,
    password  TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);

-- ---------------------------------------------------------------
-- Social graph
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS follows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    follower    TEXT NOT NULL,
    followee    TEXT NOT NULL,
    status      TEXT DEFAULT 'accepted',   -- accepted | pending (for private accounts)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(follower, followee)
);

-- ---------------------------------------------------------------
-- Feed: posts, likes, comments
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    image       TEXT NOT NULL,
    caption     TEXT DEFAULT '',
    location    TEXT DEFAULT '',
    media_type  TEXT DEFAULT 'image',   -- 'image' | 'video'
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS likes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(post_id, username)
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,        -- recipient
    actor       TEXT NOT NULL,        -- who did the thing
    type        TEXT NOT NULL,        -- like | comment | follow | follow_request | message
    post_id     INTEGER,
    is_read     INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------
-- Chat: friend/contact gating + encrypted messages
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sender     TEXT NOT NULL,
    receiver   TEXT NOT NULL,
    status     TEXT DEFAULT 'pending',  -- pending | accepted | rejected
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sender          TEXT NOT NULL,
    receiver        TEXT NOT NULL,
    ciphertext      TEXT,             -- AES-GCM ciphertext, base64
    nonce           TEXT,             -- AES-GCM nonce, base64
    attachment      TEXT,             -- optional image filename (also encrypted at rest on disk)
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_seen         INTEGER DEFAULT 0,
    is_deleted      INTEGER DEFAULT 0,
    reply_to        INTEGER,
    reaction        TEXT,
    expires_at      TIMESTAMP,        -- disappearing messages
    burned          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS thread_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a      TEXT NOT NULL,        -- alphabetically smaller username
    user_b      TEXT NOT NULL,
    enc_key     TEXT NOT NULL,        -- server-held symmetric key, base64 (demo-grade E2E-style encryption)
    fingerprint TEXT NOT NULL,        -- safety-number style verification code
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_a, user_b)
);

CREATE TABLE IF NOT EXISTS login_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    success     INTEGER NOT NULL,
    ip          TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db():
    """Return a new SQLite connection with row access by column name."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db(seed_admin_username="admin", seed_admin_password="admin123"):
    """Create tables if missing and make sure a default admin exists."""
    con = get_db()
    try:
        con.executescript(SCHEMA)

        # --- Lightweight migration for DBs created before video posts ----
        # existing installs already have a `posts` table without this
        # column; CREATE TABLE IF NOT EXISTS above won't retrofit it.
        try:
            con.execute("ALTER TABLE posts ADD COLUMN media_type TEXT DEFAULT 'image'")
        except sqlite3.OperationalError:
            pass  # column already present
        con.execute("UPDATE posts SET media_type='image' WHERE media_type IS NULL")

        existing = con.execute(
            "SELECT 1 FROM admin WHERE username=?", (seed_admin_username,)
        ).fetchone()

        if not existing:
            con.execute(
                "INSERT INTO admin(username, password, is_active) VALUES (?, ?, 1)",
                (seed_admin_username, generate_password_hash(seed_admin_password)),
            )
        con.commit()
    finally:
        con.close()
