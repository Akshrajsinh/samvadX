"""
rename_demo_users.py
---------------------
The very first seed batch in vaultgram.db created 300 placeholder
accounts named user1, user2, ... user300 (visible anywhere a username
is rendered, e.g. the People tab shows literally "user283"). This
script renames every account that still matches that pattern to a
realistic, unique Indian first+last name, and cascades the rename
across every table that stores a username as plain text (there are no
real foreign keys in this SQLite schema, so this is done explicitly
and safely inside one transaction).

Safe to re-run: it only touches usernames matching ^user\\d+$, so once
they're renamed, running it again is a no-op.

Usage:
    python rename_demo_users.py
"""

import random
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vaultgram.db"

PLACEHOLDER_RE = re.compile(r"^user\d+$")

# A broad, regionally-mixed pool of common Indian first names and surnames.
# first * last gives 70 * 70 = 4900 unique combinations, comfortably more
# than the ~300 placeholder accounts we need to rename.
FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Krishna", "Ishaan",
    "Rohan", "Karan", "Yash", "Aryan", "Dhruv", "Nikhil", "Rahul", "Amit",
    "Vikram", "Siddharth", "Akash", "Gaurav", "Manish", "Sahil", "Tushar", "Varun",
    "Harsh", "Mohit", "Kunal", "Naveen", "Parth", "Pranav", "Raghav", "Sai",
    "Shaurya", "Uday", "Vivek", "Yogesh", "Abhinav", "Chirag", "Devansh", "Eshan",
    "Priya", "Ananya", "Sneha", "Pooja", "Neha", "Riya", "Kavya", "Diya",
    "Tanvi", "Meera", "Sunita", "Deepika", "Shreya", "Pallavi", "Swati", "Anjali",
    "Kritika", "Bhavna", "Juhi", "Nidhi", "Radhika", "Tara", "Yamini", "Zara",
    "Bhumika", "Divya", "Falguni", "Hema", "Jaya", "Lavanya", "Madhavi", "Oviya",
    "Sapna", "Urvi", "Vidya", "Ishita", "Komal", "Aishwarya", "Rhea", "Simran",
]

LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Patel", "Shah", "Mehta", "Iyer", "Nair",
    "Menon", "Reddy", "Rao", "Naidu", "Pillai", "Joshi", "Desai", "Trivedi",
    "Pandey", "Mishra", "Tiwari", "Yadav", "Chauhan", "Rathod", "Solanki", "Thakur",
    "Bhatt", "Vyas", "Agarwal", "Bansal", "Kapoor", "Khanna", "Malhotra", "Chopra",
    "Sethi", "Arora", "Bose", "Banerjee", "Chatterjee", "Mukherjee", "Das", "Ghosh",
    "Dutta", "Roy", "Sarkar", "Kulkarni", "Deshmukh", "Patil", "Jadhav", "Shetty",
    "Hegde", "Kamath", "Bhat", "Gowda", "Krishnan", "Raman", "Nambiar", "Warrier",
    "Chandra", "Saxena", "Kapadia", "Doshi", "Parekh", "Modi", "Pawar", "Bhosale",
    "Gandhi", "Bhalla", "Kohli", "Goyal", "Jain", "Bajaj", "Chawla", "Ahluwalia",
]

# Username-referencing columns across the whole schema: (table, column).
REFERENCE_COLUMNS = [
    ("posts", "username"),
    ("likes", "username"),
    ("comments", "username"),
    ("follows", "follower"),
    ("follows", "followee"),
    ("notifications", "username"),
    ("notifications", "actor"),
    ("chat_requests", "sender"),
    ("chat_requests", "receiver"),
    ("messages", "sender"),
    ("messages", "receiver"),
    ("thread_keys", "user_a"),
    ("thread_keys", "user_b"),
    ("login_audit", "username"),
]


def build_username(first, last, taken):
    base = f"{first.lower()}_{last.lower()}"
    if base not in taken:
        return base
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = OFF")  # we cascade by hand below
    cur = con.cursor()

    all_usernames = {r[0] for r in cur.execute("SELECT username FROM users")}
    placeholder_users = sorted(u for u in all_usernames if PLACEHOLDER_RE.match(u))

    if not placeholder_users:
        print("No userN-style placeholder accounts found. Nothing to do.")
        con.close()
        return

    combos = [(f, l) for f in FIRST_NAMES for l in LAST_NAMES]
    random.shuffle(combos)
    if len(combos) < len(placeholder_users):
        raise SystemExit("Not enough name combinations for the number of accounts.")

    taken = set(all_usernames) - set(placeholder_users)
    rename_map = {}

    combo_iter = iter(combos)
    for old_username in placeholder_users:
        first, last = next(combo_iter)
        new_username = build_username(first, last, taken)
        taken.add(new_username)
        rename_map[old_username] = new_username

    print(f"Renaming {len(rename_map)} placeholder accounts...")

    try:
        for old, new in rename_map.items():
            new_email = f"{new}@gmail.com"

            cur.execute(
                "UPDATE users SET username=?, email=? WHERE username=?",
                (new, new_email, old),
            )
            for table, column in REFERENCE_COLUMNS:
                cur.execute(
                    f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, old)
                )

        # thread_keys stores pairs with user_a as the alphabetically-smaller
        # name (see app.py's thread_pair()). Renaming can change which side
        # of a pair sorts first, so re-normalize every row to keep that
        # invariant true -- otherwise a future lookup for the same pair
        # would miss this row and silently create a duplicate key.
        rows = cur.execute("SELECT id, user_a, user_b FROM thread_keys").fetchall()
        for row_id, user_a, user_b in rows:
            correct_a, correct_b = sorted([user_a, user_b])
            if (correct_a, correct_b) != (user_a, user_b):
                cur.execute(
                    "UPDATE thread_keys SET user_a=?, user_b=? WHERE id=?",
                    (correct_a, correct_b, row_id),
                )

        con.commit()
    except Exception:
        con.rollback()
        raise

    print("Done. Sample renames:")
    for old, new in list(rename_map.items())[:10]:
        print(f"  {old:>10}  ->  {new}")

    con.close()


if __name__ == "__main__":
    main()
