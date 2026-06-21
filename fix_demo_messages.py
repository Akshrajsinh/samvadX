"""
fix_demo_messages.py
----------------------
Every message row inserted by an earlier (now-missing) seed script has
the literal string 'Demo message content' sitting in the `ciphertext`
column with no `nonce` -- it was never actually encrypted. The app's
real chat code always tries to AES-GCM decrypt that column, so every
one of these rows fails decryption and the chat thread shows
"[unable to decrypt]" instead of a message.

This script finds every message with nonce IS NULL, gives it a
plausible casual line, and properly encrypts it with that pair's real
per-thread key -- the exact same crypto_utils path the live app uses
(database.py / app.py), so the result decrypts cleanly through the
normal /api/history flow. Thread keys that don't exist yet are created
exactly as the app would on first message.

Safe to re-run: once a row has a nonce, it's left untouched.

Usage:
    python fix_demo_messages.py
"""

import random
import sqlite3
from pathlib import Path

import crypto_utils

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vaultgram.db"

CASUAL_LINES = [
    "Hey! How's it going?",
    "Long time no talk, how have you been?",
    "Are we still on for this weekend?",
    "Haha that's hilarious",
    "Did you see the match last night?",
    "Sure, sounds good to me",
    "Can't talk right now, call you later?",
    "Happy birthday! Have a great one",
    "Where are you right now?",
    "That movie was actually really good",
    "Let's catch up soon, it's been a while",
    "Just landed, will message you in a bit",
    "Thanks so much for yesterday",
    "Lol no way, really?",
    "Running a little late, sorry!",
    "Did you finish the thing we talked about?",
    "Miss hanging out, we should plan something",
    "All good here, how about you?",
    "Sending you the details in a sec",
    "That's exactly what I needed to hear today",
    "Can you send that photo again?",
    "Omg yes I remember that",
    "Let me know when you're free",
    "Good morning! Hope you slept well",
    "On my way, see you soon",
    "This made my day, thank you",
    "Congrats, you totally deserve it",
    "Same here, super busy this week",
    "Let's grab food this weekend",
    "Just saw your post, looked great",
    "No worries at all, take your time",
    "I'll be there by evening",
    "That's such good news!",
    "Can we reschedule to tomorrow?",
    "Totally agree with you on that",
    "Sounds like a plan",
    "Hope everything's okay, thinking of you",
    "Tell me everything, I have time now",
    "Just got back, exhausted but happy",
    "See you at the usual place?",
]


def thread_pair(user1, user2):
    return tuple(sorted([user1, user2]))


def get_or_create_thread_key(con, cache, user1, user2):
    a, b = thread_pair(user1, user2)
    if (a, b) in cache:
        return cache[(a, b)]

    row = con.execute(
        "SELECT enc_key FROM thread_keys WHERE user_a=? AND user_b=?", (a, b)
    ).fetchone()

    if row:
        key = crypto_utils.key_from_b64(row[0])
    else:
        key = crypto_utils.new_thread_key()
        fingerprint = crypto_utils.compute_fingerprint(key, a, b)
        con.execute(
            "INSERT INTO thread_keys(user_a, user_b, enc_key, fingerprint) VALUES (?, ?, ?, ?)",
            (a, b, crypto_utils.key_to_b64(key), fingerprint),
        )

    cache[(a, b)] = key
    return key


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    broken = cur.execute(
        "SELECT id, sender, receiver FROM messages WHERE nonce IS NULL OR nonce=''"
    ).fetchall()

    if not broken:
        print("No broken (unencrypted placeholder) messages found. Nothing to do.")
        con.close()
        return

    print(f"Found {len(broken)} unencrypted placeholder messages. Re-encrypting...")

    key_cache = {}
    fixed = 0

    for msg_id, sender, receiver in broken:
        key = get_or_create_thread_key(con, key_cache, sender, receiver)
        text = random.choice(CASUAL_LINES)
        ciphertext, nonce = crypto_utils.encrypt_message(key, text)
        cur.execute(
            "UPDATE messages SET ciphertext=?, nonce=? WHERE id=?",
            (ciphertext, nonce, msg_id),
        )
        fixed += 1

    con.commit()
    print(f"Re-encrypted {fixed} messages across {len(key_cache)} chat threads.")
    con.close()


if __name__ == "__main__":
    main()
