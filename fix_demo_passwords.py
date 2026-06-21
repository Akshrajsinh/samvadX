"""
fix_demo_passwords.py
-----------------------
The 300 userN-style placeholder accounts (now renamed to real-looking
Indian names by rename_demo_users.py) were seeded with the literal
plaintext string 'demo123' sitting in the `password` column -- not a
real password hash. The app's login route calls
check_password_hash(row["password"], password), which simply fails
against a non-hash string, so none of these 300 accounts could ever
log in at all.

This re-hashes every account whose password isn't already a real
Werkzeug hash, using the same demo password the other seeded accounts
already use (see seed_themed_demo.py's DEMO_PASSWORD), so the whole
demo dataset shares one documented login.

Safe to re-run: accounts that already have a real hash are skipped.

Usage:
    python fix_demo_passwords.py
"""

import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vaultgram.db"

DEMO_PASSWORD = "Demo@1234"  # matches seed_themed_demo.py


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    broken = cur.execute(
        """SELECT username FROM users
           WHERE password NOT LIKE 'pbkdf2:%' AND password NOT LIKE 'scrypt:%'"""
    ).fetchall()

    if not broken:
        print("Every account already has a real password hash. Nothing to do.")
        con.close()
        return

    new_hash = generate_password_hash(DEMO_PASSWORD, method="pbkdf2:sha256")
    usernames = [r[0] for r in broken]

    cur.executemany(
        "UPDATE users SET password=?, failed_logins=0, locked_until=NULL WHERE username=?",
        [(new_hash, u) for u in usernames],
    )
    con.commit()
    con.close()

    print(f"Fixed {len(usernames)} accounts with non-functional passwords.")
    print(f"They can now log in with the password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
