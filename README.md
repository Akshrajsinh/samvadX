# SamvadX

A privacy-first social app: an Instagram-style feed (posts, likes, comments,
follow graph, explore, profiles) combined with an encrypted 1:1 chat system.
Built with Flask + SQLite + raw WebSockets — no external services required.

## Quick start

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000**

- Register a couple of accounts to try the feed + chat together (two browser
  windows, or one normal + one incognito).
- Admin panel: **http://localhost:5000/admin/login** — username `admin`,
  password `admin123` (change this in production — see `database.py`).

## What's inside

### The feed (Instagram-style)
- Upload photo posts with a caption and location
- Like / unlike, comment, delete your own posts and comments
- Follow / unfollow, with **private accounts** that require approval —
  requests land in the notification bell as a dedicated "Follow requests"
  section with Confirm/Delete buttons, and the follower's profile view
  updates automatically (polling) once accepted, so their posts unlock
  without a manual refresh
- Explore tab to discover public posts and search for people
- Notifications for likes, comments, and follows
- Profile pages with post grid, stats, bio, and an edit-profile flow

### The chat (encrypted, "unique idea" implementation)
The brief asked for a unique security idea for chat — this build does two
things most demo chat apps skip:

1. **Per-thread AES-256-GCM keys.** Every 1:1 conversation gets its own
   encryption key the first time the two people connect. Messages are
   stored as ciphertext in the database; the server only holds plaintext
   in memory long enough to relay/render it. Compromising one thread's
   key or DB row does not expose any other conversation.

2. **Visible safety numbers.** Each thread has a fingerprint (a short code
   derived from its key) shown to both participants — the same idea Signal
   and WhatsApp use so two people can confirm, through some other channel
   (in person, a phone call), that they're really talking to each other and
   not through a tampered session. Find it via the lock icon in any chat
   thread.

This is "E2E-style" rather than textbook end-to-end encryption: the server
still generates and holds the thread key, so the whole thing runs from one
Flask process without a client-side key-exchange UI. For a production
system you'd move key generation/exchange to the client (X3DH / Double
Ratchet) so the server never sees plaintext or keys — the storage shape
here (one authenticated key per thread, safety numbers, rotation-ready) is
the same shape you'd grow into.

Other chat features:
- Friend-request gating — you can only message someone after they accept
- Disappearing messages with a per-message timer (30s / 5m / 1h / 24h),
  swept by a background thread
- Replies, emoji reactions, edit, delete, image attachments
- Typing indicators, online/offline presence, read receipts
- All via a single raw WebSocket connection per user (`/ws/<username>`)

### Security hardening (account + platform level)
- Passwords hashed with PBKDF2-SHA256 (`werkzeug.security`)
- Account lockout after 5 failed logins (10-minute cooldown) + login audit log
- Session cookies are `HttpOnly` + `SameSite=Lax`; secret key persisted to
  a local `.secret_key` file (gitignore this in real deployments)
- Upload validation: extension allow-list **and** magic-byte content
  sniffing, so a renamed `.php` can't pass itself off as an image
- Security response headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`)

### Admin panel
- Dashboard: user counts, online users, message/post/like/comment totals,
  failed-login count
- User management: block / unblock accounts
- Post moderation: remove any post

## Project structure

```
app.py              Flask app: routes, WebSocket handler, all API endpoints
database.py          SQLite schema + connection helper
crypto_utils.py       AES-256-GCM thread encryption + safety-number fingerprints
requirements.txt
static/
  css/                theme.css (design tokens), shell.css, feed.css, chat.css, auth.css, admin.css
  js/                  shell.js, feed.js, explore.js, profile.js, chat.js
  uploads/             profile photos + chat attachments
  posts/               post images
templates/
  login.html, register.html, feed.html, explore.html, profile.html, chat.html
  admin/                login.html, dashboard.html, users.html, posts.html, _sidebar.html
  _rail.html            shared left navigation, included on every logged-in page
```

## Notes for going further

- Swap SQLite for Postgres/MySQL by changing `database.py`'s `get_db()` —
  the rest of the app talks to it through plain SQL, no ORM lock-in.
- The thread-key cache in `app.py` is in-process memory; for multi-process
  deployment (gunicorn with >1 worker) move it to Redis or re-derive on
  every request from the DB row (already the fallback path).
- For real E2EE, move `crypto_utils.encrypt_message` /
  `decrypt_message` to the browser (e.g. via WebCrypto) and have the server
  store only ciphertext it never has the key for.

## Fixes applied (this round)

Three real bugs were found and fixed in the seeded demo data and the chat
module:

1. **Chat history showed "[unable to decrypt]".** An earlier seed batch had
   inserted 3,500 message rows with the literal placeholder text
   `'Demo message content'` sitting directly in the `ciphertext` column and
   no `nonce` — never actually encrypted. Every real decrypt attempt failed.
   Fixed by `fix_demo_messages.py`, which properly AES-256-GCM encrypts each
   one with that conversation's real per-thread key (creating the key if it
   doesn't exist yet, the same way the live app does).

2. **300 demo accounts could never log in.** Those same seeded accounts had
   the plaintext string `'demo123'` in their `password` column instead of a
   real Werkzeug hash, so `check_password_hash` always rejected them. Fixed
   by `fix_demo_passwords.py`, which re-hashes them with the same demo
   password the themed brand accounts already use: **`Demo@1234`**.

3. **The Chats/People tabs were doing one HTTP request *per user in the
   database*** — sequentially for the Chats tab, all-at-once for People.
   With 346 seeded users that's 346+ round trips just to open a tab, which
   looked frozen/broken. Fixed by adding `/api/chat-contacts` (one query)
   and folding each user's request status into `/api/users` directly via
   SQL subqueries, instead of a separate per-user fetch. `chat.js` was
   updated to match. Confirmed ~300x fewer requests for the same view.

4. **Generic `userN` usernames** (e.g. `user283`) shown anywhere a username
   is rendered — feed post headers, the People tab, profile pages — were
   renamed to realistic, unique Indian first+last names (e.g.
   `arjun_chandra`, `priya_sharma`) by `rename_demo_users.py`. The rename
   cascades safely across every table that stores a username as text
   (posts, likes, comments, follows, notifications, chat_requests,
   messages, thread_keys, login_audit) inside one transaction — verified
   zero orphaned rows and zero duplicate usernames afterward.

All four scripts (`rename_demo_users.py`, `fix_demo_messages.py`,
`fix_demo_passwords.py`) have already been run against the `vaultgram.db`
shipped in this zip — you don't need to run them again unless you re-seed
from scratch. They're idempotent (safe to re-run) if you ever do.

