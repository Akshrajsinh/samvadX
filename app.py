"""
app.py
------
SamvadX - a privacy-first social app combining an Instagram-style
feed (posts, likes, comments, follow graph) with an encrypted 1:1 chat
system, built on Flask + SQLite + raw WebSockets.

Security highlights (the "unique idea" requested for the chat half):
  - Every chat thread gets its own AES-256-GCM key; messages are stored
    encrypted at rest and decrypted only in memory to relay/display.
  - Each thread shows a Signal-style "safety number" fingerprint so two
    users can visually confirm they're talking to each other and not a
    tampered session.
  - Disappearing messages: optional per-message TTL, swept by a
    background thread.
  - Session hardening: account lockout after repeated failed logins,
    session cookie flags (HttpOnly/SameSite), login audit log,
    password strength checks, secure filename + content-type checks
    on every upload.

Run with:
    pip install -r requirements.txt
    python app.py

Then open http://localhost:5000
Admin panel:  http://localhost:5000/admin/login  (admin / admin123)
"""

import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, abort
)
from flask_sock import Sock
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import database
import crypto_utils

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
POST_DIR = BASE_DIR / "static" / "posts"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
POST_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "ogg"}
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 10
DISAPPEARING_CHOICES = {0, 30, 300, 3600, 86400}  # off, 30s, 5m, 1h, 1d

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB upload cap (video posts need more room)

# --- Session / cookie hardening -------------------------------------------
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

_secret_file = BASE_DIR / ".secret_key"
if _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    _secret_file.write_text(app.secret_key)

sock = Sock(app)

# ---------------------------------------------------------------------------
# In-memory WebSocket registry: username -> ws connection
# ---------------------------------------------------------------------------
connected_users = {}
connected_lock = threading.Lock()

# In-memory cache of decrypted thread keys (bytes), keyed by sorted pair,
# so we don't hit the DB + b64-decode on every single message.
_thread_key_cache = {}
_thread_key_lock = threading.Lock()


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def allowed_video(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def sniff_is_image(file_storage) -> bool:
    """Cheap content sniff so a renamed .php can't sneak in as .png."""
    head = file_storage.stream.read(12)
    file_storage.stream.seek(0)
    signatures = [
        b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF",
    ]
    return any(head.startswith(sig) for sig in signatures)


def sniff_is_video(file_storage) -> bool:
    """Cheap content sniff for the handful of video containers we accept,
    so a renamed non-video file can't be uploaded as a video post."""
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return True  # mp4 / mov / m4v (ISO base media file format)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return True  # webm / matroska
    if head.startswith(b"OggS"):
        return True  # ogg
    return False


def save_upload(file_storage, dest_dir: Path):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename) or not sniff_is_image(file_storage):
        return None
    safe_name = secure_filename(file_storage.filename)
    file_name = f"{int(time.time() * 1000)}_{secrets.token_hex(4)}_{safe_name}"
    file_storage.save(dest_dir / file_name)
    return file_name


def save_post_media(file_storage):
    """Validate + save an image OR video post upload into POST_DIR.

    Returns (file_name, media_type) on success, or (None, None) if the
    upload is missing or fails the extension + content-sniff checks.
    """
    if not file_storage or not file_storage.filename:
        return None, None

    if allowed_image(file_storage.filename) and sniff_is_image(file_storage):
        media_type = "image"
    elif allowed_video(file_storage.filename) and sniff_is_video(file_storage):
        media_type = "video"
    else:
        return None, None

    safe_name = secure_filename(file_storage.filename)
    file_name = f"{int(time.time() * 1000)}_{secrets.token_hex(4)}_{safe_name}"
    file_storage.save(POST_DIR / file_name)
    return file_name, media_type


def hash_password(password):
    return generate_password_hash(password, method="pbkdf2:sha256")


def password_strength_error(password: str):
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        return "Password must include both letters and numbers"
    return None


def client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def current_user_row(con):
    return con.execute(
        "SELECT * FROM users WHERE username=?", (session.get("user"),)
    ).fetchone()


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ---------------------------------------------------------------------------
# Static / uploads
# ---------------------------------------------------------------------------
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/postimg/<path:filename>")
def post_image(filename):
    return send_from_directory(POST_DIR, filename)


@app.errorhandler(413)
def handle_too_large(e):
    msg = "That file is too large. Photos and videos must be under 60 MB."
    if request.path.startswith("/api/"):
        return jsonify({"error": msg}), 413
    return msg, 413


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
@app.route("/landing")
def landing():
    """Landing page for non-authenticated users"""
    if session.get("user"):
        return redirect(url_for("feed_page"))
    
    # Get some stats for the landing page
    con = database.get_db()
    try:
        total_users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        total_posts = con.execute("SELECT COUNT(*) c FROM posts WHERE is_deleted=0").fetchone()["c"]
        total_messages = con.execute("SELECT COUNT(*) c FROM messages WHERE is_deleted=0").fetchone()["c"]
    except Exception:
        total_users = 0
        total_posts = 0
        total_messages = 0
    finally:
        con.close()
    
    return render_template(
        "index.html",
        total_users=total_users,
        total_posts=total_posts,
        total_messages=total_messages
    )


@app.route("/")
def index():
    if session.get("user"):
        return redirect(url_for("feed_page"))
    return redirect(url_for("landing"))


# ---------------------------------------------------------------------------
# Login / Register / Logout  (with lockout + audit logging)
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("user"):
            return redirect(url_for("feed_page"))
        return render_template(
            "login.html",
            error=request.args.get("error"),
            success=request.args.get("success"),
        )

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not username or not password:
        return redirect(url_for("login", error="empty"))

    con = database.get_db()
    try:
        row = con.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()

        # Use the canonical stored casing from here on, so audit logs,
        # lockout tracking, and the session match the real username.
        if row:
            username = row["username"]

        if row and row["locked_until"]:
            if row["locked_until"] > now_iso():
                return redirect(url_for("login", error="locked"))

        if row and check_password_hash(row["password"], password):
            if row["is_blocked"]:
                return redirect(url_for("login", error="blocked"))

            con.execute(
                "UPDATE users SET failed_logins=0, locked_until=NULL, last_seen=? WHERE username=?",
                (now_iso(), username),
            )
            con.execute(
                "INSERT INTO login_audit(username, success, ip) VALUES (?, 1, ?)",
                (username, client_ip()),
            )
            con.commit()

            session.clear()
            session.permanent = True
            session["user"] = username
            session["session_token"] = secrets.token_hex(16)
            return redirect(url_for("feed_page"))

        # Failed attempt -- track + maybe lock the account
        if row:
            failed = (row["failed_logins"] or 0) + 1
            locked_until = None
            if failed >= MAX_FAILED_LOGINS:
                locked_until = (datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
            con.execute(
                "UPDATE users SET failed_logins=?, locked_until=? WHERE username=?",
                (failed, locked_until, username),
            )
        con.execute(
            "INSERT INTO login_audit(username, success, ip) VALUES (?, 0, ?)",
            (username, client_ip()),
        )
        con.commit()

        return redirect(url_for("login", error="invalid"))
    except Exception:
        return redirect(url_for("login", error="server"))
    finally:
        con.close()


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login", success="logout"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", error=request.args.get("error"))

    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""
    email = (request.form.get("email") or "").strip().lower()

    if not username or not password:
        return redirect(url_for("register", error="empty"))

    if not re.match(r"^[a-z0-9_.]{3,20}$", username):
        return redirect(url_for("register", error="badusername"))

    strength_error = password_strength_error(password)
    if strength_error:
        return redirect(url_for("register", error="weak"))

    con = database.get_db()
    try:
        exists = con.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone()
        if exists:
            return redirect(url_for("register", error="exists"))

        file_name = save_upload(request.files.get("profile"), UPLOAD_DIR) or "default.png"

        con.execute(
            "INSERT INTO users(username, email, password, profile_pic) VALUES (?, ?, ?, ?)",
            (username, email, hash_password(password), file_name),
        )
        con.commit()
        return redirect(url_for("login", success="registered"))
    except Exception:
        return redirect(url_for("register", error="server"))
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Pages: feed / explore / profile / chat
# ---------------------------------------------------------------------------
@app.route("/feed")
@login_required
def feed_page():
    con = database.get_db()
    try:
        me = current_user_row(con)
    finally:
        con.close()
    return render_template("feed.html", username=session["user"],
                           profile_pic=me["profile_pic"] if me else "default.png")


@app.route("/explore")
@login_required
def explore_page():
    con = database.get_db()
    try:
        me = current_user_row(con)
    finally:
        con.close()
    return render_template("explore.html", username=session["user"],
                           profile_pic=me["profile_pic"] if me else "default.png")


@app.route("/profile/<username>")
@login_required
def profile_page(username):
    username = username.strip()
    con = database.get_db()
    try:
        me = current_user_row(con)
        profile_user = con.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()
        if not profile_user:
            abort(404)
    finally:
        con.close()
    return render_template(
        "profile.html",
        username=session["user"],
        profile_pic=me["profile_pic"] if me else "default.png",
        view_user=profile_user["username"],
    )


@app.route("/chat")
@login_required
def chat_page():
    con = database.get_db()
    try:
        me = current_user_row(con)
    finally:
        con.close()
    return render_template("chat.html", username=session["user"],
                           profile_pic=me["profile_pic"] if me else "default.png")


# ---------------------------------------------------------------------------
# API: profile / settings
# ---------------------------------------------------------------------------
def is_following(con, follower, followee):
    row = con.execute(
        "SELECT status FROM follows WHERE follower=? AND followee=?",
        (follower, followee),
    ).fetchone()
    return row["status"] if row else None


def add_notification(con, username, actor, ntype, post_id=None):
    if username == actor:
        return
    con.execute(
        "INSERT INTO notifications(username, actor, type, post_id) VALUES (?, ?, ?, ?)",
        (username, actor, ntype, post_id),
    )


@app.route("/api/profile/<username>")
@login_required
def api_profile(username):
    username = username.strip()
    current = session["user"]
    con = database.get_db()
    try:
        user = con.execute(
            "SELECT username, profile_pic, bio, is_private, created_at FROM users WHERE username=? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if not user:
            return jsonify({"error": "not found"}), 404

        # Use the canonical, stored casing for every subsequent lookup so
        # posts/follows (stored with the original case) are found correctly.
        username = user["username"]

        post_count = con.execute(
            "SELECT COUNT(*) c FROM posts WHERE username=? AND is_deleted=0", (username,)
        ).fetchone()["c"]
        follower_count = con.execute(
            "SELECT COUNT(*) c FROM follows WHERE followee=? AND status='accepted'", (username,)
        ).fetchone()["c"]
        following_count = con.execute(
            "SELECT COUNT(*) c FROM follows WHERE follower=? AND status='accepted'", (username,)
        ).fetchone()["c"]

        follow_status = is_following(con, current, username)

        can_view_posts = (
            username == current
            or not user["is_private"]
            or follow_status == "accepted"
        )

        posts = []
        if can_view_posts:
            rows = con.execute(
                """SELECT id, image, caption, media_type, created_at FROM posts
                   WHERE username=? AND is_deleted=0 ORDER BY id DESC""",
                (username,),
            ).fetchall()
            for r in rows:
                like_count = con.execute(
                    "SELECT COUNT(*) c FROM likes WHERE post_id=?", (r["id"],)
                ).fetchone()["c"]
                comment_count = con.execute(
                    "SELECT COUNT(*) c FROM comments WHERE post_id=? AND is_deleted=0", (r["id"],)
                ).fetchone()["c"]
                posts.append({
                    "id": r["id"], "image": r["image"], "caption": r["caption"],
                    "mediaType": r["media_type"] or "image",
                    "createdAt": r["created_at"], "likeCount": like_count, "commentCount": comment_count,
                })
    finally:
        con.close()

    return jsonify({
        "username": user["username"],
        "profilePic": user["profile_pic"],
        "bio": user["bio"] or "",
        "isPrivate": bool(user["is_private"]),
        "isSelf": username == current,
        "followStatus": follow_status,
        "postCount": post_count,
        "followerCount": follower_count,
        "followingCount": following_count,
        "canViewPosts": can_view_posts,
        "posts": posts,
    })


@app.route("/api/profile/update", methods=["POST"])
@login_required
def api_profile_update():
    current = session["user"]
    bio = (request.form.get("bio") or "")[:150]
    is_private = 1 if request.form.get("is_private") == "true" else 0

    con = database.get_db()
    try:
        file_name = save_upload(request.files.get("profile"), UPLOAD_DIR)
        if file_name:
            con.execute(
                "UPDATE users SET bio=?, is_private=?, profile_pic=? WHERE username=?",
                (bio, is_private, file_name, current),
            )
        else:
            con.execute(
                "UPDATE users SET bio=?, is_private=? WHERE username=?",
                (bio, is_private, current),
            )
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# API: follow graph
# ---------------------------------------------------------------------------
@app.route("/api/follow", methods=["POST"])
@login_required
def api_follow():
    current = session["user"]
    target = (request.form.get("username") or "").strip().lower()
    if not target or target == current:
        return jsonify({"error": "invalid target"}), 400

    con = database.get_db()
    try:
        target_row = con.execute("SELECT is_private FROM users WHERE username=?", (target,)).fetchone()
        if not target_row:
            return jsonify({"error": "not found"}), 404

        existing = con.execute(
            "SELECT status FROM follows WHERE follower=? AND followee=?", (current, target)
        ).fetchone()
        if existing:
            return jsonify({"status": existing["status"]})

        status = "pending" if target_row["is_private"] else "accepted"
        con.execute(
            "INSERT INTO follows(follower, followee, status) VALUES (?, ?, ?)",
            (current, target, status),
        )
        add_notification(con, target, current, "follow_request" if status == "pending" else "follow")
        con.commit()
        return jsonify({"status": status})
    finally:
        con.close()


@app.route("/api/unfollow", methods=["POST"])
@login_required
def api_unfollow():
    current = session["user"]
    target = (request.form.get("username") or "").strip().lower()
    con = database.get_db()
    try:
        con.execute("DELETE FROM follows WHERE follower=? AND followee=?", (current, target))
        con.commit()
        return jsonify({"status": "none"})
    finally:
        con.close()


@app.route("/api/follow-requests")
@login_required
def api_follow_requests():
    current = session["user"]
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT f.id, f.follower, u.profile_pic FROM follows f
               JOIN users u ON u.username = f.follower
               WHERE f.followee=? AND f.status='pending' ORDER BY f.id DESC""",
            (current,),
        ).fetchall()
    finally:
        con.close()
    return jsonify([{"id": r["id"], "username": r["follower"], "profilePic": r["profile_pic"]} for r in rows])


@app.route("/api/follow-requests/respond", methods=["POST"])
@login_required
def api_follow_respond():
    current = session["user"]
    req_id = request.form.get("id")
    action = request.form.get("action")  # accept | reject

    con = database.get_db()
    try:
        row = con.execute(
            "SELECT * FROM follows WHERE id=? AND followee=?", (req_id, current)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        if action == "accept":
            con.execute("UPDATE follows SET status='accepted' WHERE id=?", (req_id,))
            add_notification(con, row["follower"], current, "follow")
        else:
            con.execute("DELETE FROM follows WHERE id=?", (req_id,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# API: posts / feed / likes / comments
# ---------------------------------------------------------------------------
@app.route("/api/feed")
@login_required
def api_feed():
    current = session["user"]
    before_id = request.args.get("before", type=int)

    con = database.get_db()
    try:
        following = [
            r["followee"] for r in con.execute(
                "SELECT followee FROM follows WHERE follower=? AND status='accepted'", (current,)
            ).fetchall()
        ]
        visible_authors = following + [current]
        placeholders = ",".join("?" for _ in visible_authors)

        query = f"""SELECT p.*, u.profile_pic FROM posts p
                    JOIN users u ON u.username = p.username
                    WHERE p.username IN ({placeholders}) AND p.is_deleted=0"""
        params = list(visible_authors)
        if before_id:
            query += " AND p.id < ?"
            params.append(before_id)
        query += " ORDER BY p.id DESC LIMIT 10"

        rows = con.execute(query, params).fetchall()

        posts = []
        for r in rows:
            like_count = con.execute("SELECT COUNT(*) c FROM likes WHERE post_id=?", (r["id"],)).fetchone()["c"]
            liked_by_me = con.execute(
                "SELECT 1 FROM likes WHERE post_id=? AND username=?", (r["id"], current)
            ).fetchone() is not None
            comment_count = con.execute(
                "SELECT COUNT(*) c FROM comments WHERE post_id=? AND is_deleted=0", (r["id"],)
            ).fetchone()["c"]
            top_comments = con.execute(
                """SELECT username, text FROM comments WHERE post_id=? AND is_deleted=0
                   ORDER BY id DESC LIMIT 2""",
                (r["id"],),
            ).fetchall()

            posts.append({
                "id": r["id"],
                "username": r["username"],
                "profilePic": r["profile_pic"] or "default.png",
                "image": r["image"],
                "mediaType": r["media_type"] or "image",
                "caption": r["caption"],
                "location": r["location"],
                "createdAt": r["created_at"],
                "likeCount": like_count,
                "likedByMe": liked_by_me,
                "commentCount": comment_count,
                "topComments": [{"username": c["username"], "text": c["text"]} for c in reversed(top_comments)],
                "isOwner": r["username"] == current,
            })
    finally:
        con.close()

    return jsonify(posts)


@app.route("/api/explore")
@login_required
def api_explore():
    current = session["user"]
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT p.*, u.profile_pic, u.is_private FROM posts p
               JOIN users u ON u.username = p.username
               WHERE p.is_deleted=0 AND u.is_private=0 AND p.username<>?
               ORDER BY p.id DESC LIMIT 30""",
            (current,),
        ).fetchall()
        posts = []
        for r in rows:
            like_count = con.execute("SELECT COUNT(*) c FROM likes WHERE post_id=?", (r["id"],)).fetchone()["c"]
            posts.append({
                "id": r["id"], "username": r["username"], "image": r["image"],
                "mediaType": r["media_type"] or "image",
                "likeCount": like_count,
            })
    finally:
        con.close()
    return jsonify(posts)


@app.route("/api/posts", methods=["POST"])
@login_required
def api_create_post():
    current = session["user"]
    caption = (request.form.get("caption") or "")[:2200]
    location = (request.form.get("location") or "")[:100]

    media = request.files.get("image")
    file_name, media_type = save_post_media(media)
    if not file_name:
        return jsonify({"error": "A valid image or video is required"}), 400

    con = database.get_db()
    try:
        cur = con.execute(
            "INSERT INTO posts(username, image, caption, location, media_type) VALUES (?, ?, ?, ?, ?)",
            (current, file_name, caption, location, media_type),
        )
        con.commit()
        return jsonify({"ok": True, "id": cur.lastrowid, "mediaType": media_type})
    finally:
        con.close()


@app.route("/api/posts/<int:post_id>")
@login_required
def api_get_post(post_id):
    """Full detail for a single post: used by the post-detail modal that
    opens when a post is clicked from the profile grid (or explore)."""
    current = session["user"]
    con = database.get_db()
    try:
        row = con.execute(
            """SELECT p.*, u.profile_pic, u.is_private FROM posts p
               JOIN users u ON u.username = p.username
               WHERE p.id=? AND p.is_deleted=0""",
            (post_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        owner = row["username"]
        if owner != current and row["is_private"]:
            if is_following(con, current, owner) != "accepted":
                return jsonify({"error": "not allowed"}), 403

        like_count = con.execute("SELECT COUNT(*) c FROM likes WHERE post_id=?", (post_id,)).fetchone()["c"]
        liked_by_me = con.execute(
            "SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, current)
        ).fetchone() is not None
        comments = con.execute(
            """SELECT c.id, c.username, c.text, c.created_at, u.profile_pic FROM comments c
               JOIN users u ON u.username = c.username
               WHERE c.post_id=? AND c.is_deleted=0 ORDER BY c.id ASC""",
            (post_id,),
        ).fetchall()

        return jsonify({
            "id": row["id"],
            "username": owner,
            "profilePic": row["profile_pic"] or "default.png",
            "image": row["image"],
            "mediaType": row["media_type"] or "image",
            "caption": row["caption"],
            "location": row["location"],
            "createdAt": row["created_at"],
            "likeCount": like_count,
            "likedByMe": liked_by_me,
            "commentCount": len(comments),
            "isOwner": owner == current,
            "comments": [
                {"id": c["id"], "username": c["username"], "text": c["text"],
                 "createdAt": c["created_at"], "profilePic": c["profile_pic"]}
                for c in comments
            ],
        })
    finally:
        con.close()


@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
@login_required
def api_delete_post(post_id):
    current = session["user"]
    con = database.get_db()
    try:
        row = con.execute("SELECT username FROM posts WHERE id=?", (post_id,)).fetchone()
        if not row or row["username"] != current:
            return jsonify({"error": "not allowed"}), 403
        con.execute("UPDATE posts SET is_deleted=1 WHERE id=?", (post_id,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
@login_required
def api_like_post(post_id):
    current = session["user"]
    con = database.get_db()
    try:
        post = con.execute("SELECT username FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return jsonify({"error": "not found"}), 404

        existing = con.execute(
            "SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, current)
        ).fetchone()

        if existing:
            con.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, current))
            liked = False
        else:
            con.execute("INSERT INTO likes(post_id, username) VALUES (?, ?)", (post_id, current))
            add_notification(con, post["username"], current, "like", post_id)
            liked = True

        con.commit()
        count = con.execute("SELECT COUNT(*) c FROM likes WHERE post_id=?", (post_id,)).fetchone()["c"]
        return jsonify({"liked": liked, "likeCount": count})
    finally:
        con.close()


@app.route("/api/posts/<int:post_id>/comments")
@login_required
def api_get_comments(post_id):
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT c.id, c.username, c.text, c.created_at, u.profile_pic FROM comments c
               JOIN users u ON u.username = c.username
               WHERE c.post_id=? AND c.is_deleted=0 ORDER BY c.id ASC""",
            (post_id,),
        ).fetchall()
    finally:
        con.close()
    return jsonify([
        {"id": r["id"], "username": r["username"], "text": r["text"],
         "createdAt": r["created_at"], "profilePic": r["profile_pic"]}
        for r in rows
    ])


@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
@login_required
def api_add_comment(post_id):
    current = session["user"]
    text = (request.form.get("text") or "").strip()[:500]
    if not text:
        return jsonify({"error": "empty comment"}), 400

    con = database.get_db()
    try:
        post = con.execute("SELECT username FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return jsonify({"error": "not found"}), 404

        cur = con.execute(
            "INSERT INTO comments(post_id, username, text) VALUES (?, ?, ?)",
            (post_id, current, text),
        )
        add_notification(con, post["username"], current, "comment", post_id)
        con.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    finally:
        con.close()


@app.route("/api/comments/<int:comment_id>", methods=["DELETE"])
@login_required
def api_delete_comment(comment_id):
    current = session["user"]
    con = database.get_db()
    try:
        row = con.execute(
            """SELECT c.username AS commenter, p.username AS post_owner FROM comments c
               JOIN posts p ON p.id = c.post_id WHERE c.id=?""",
            (comment_id,),
        ).fetchone()
        if not row or current not in (row["commenter"], row["post_owner"]):
            return jsonify({"error": "not allowed"}), 403
        con.execute("UPDATE comments SET is_deleted=1 WHERE id=?", (comment_id,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.route("/api/search-users")
@login_required
def api_search_users():
    current = session["user"]
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT username, profile_pic FROM users
               WHERE username LIKE ? AND username<>? ORDER BY username ASC LIMIT 20""",
            (f"%{q}%", current),
        ).fetchall()
    finally:
        con.close()
    return jsonify([{"username": r["username"], "profilePic": r["profile_pic"]} for r in rows])


@app.route("/api/notifications")
@login_required
def api_notifications():
    current = session["user"]
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT n.*, u.profile_pic FROM notifications n
               JOIN users u ON u.username = n.actor
               WHERE n.username=? ORDER BY n.id DESC LIMIT 50""",
            (current,),
        ).fetchall()
        con.execute("UPDATE notifications SET is_read=1 WHERE username=?", (current,))
        con.commit()
    finally:
        con.close()
    return jsonify([
        {"id": r["id"], "actor": r["actor"], "type": r["type"], "postId": r["post_id"],
         "profilePic": r["profile_pic"], "createdAt": r["created_at"], "isRead": bool(r["is_read"])}
        for r in rows
    ])


@app.route("/api/notifications/unread-count")
@login_required
def api_unread_count():
    current = session["user"]
    con = database.get_db()
    try:
        count = con.execute(
            "SELECT COUNT(*) c FROM notifications WHERE username=? AND is_read=0", (current,)
        ).fetchone()["c"]
    finally:
        con.close()
    return jsonify({"count": count})


# ---------------------------------------------------------------------------
# API: chat requests (gates who can message whom)
# ---------------------------------------------------------------------------
@app.route("/api/users")
@login_required
def api_users():
    current = session["user"]
    con = database.get_db()
    try:
        # One query for the user list, with the current user's chat-request
        # status/direction toward each row folded in via correlated
        # subqueries. The People and Chats tabs used to fetch this with a
        # separate /api/request-status call per user (N+1 -- painfully slow
        # once there are a few hundred users); now it's a single round trip.
        rows = con.execute(
            """SELECT u.username, u.profile_pic,
                      (SELECT cr.status FROM chat_requests cr
                       WHERE (cr.sender=:me AND cr.receiver=u.username)
                          OR (cr.sender=u.username AND cr.receiver=:me)
                       ORDER BY cr.id DESC LIMIT 1) AS req_status,
                      (SELECT cr.sender FROM chat_requests cr
                       WHERE (cr.sender=:me AND cr.receiver=u.username)
                          OR (cr.sender=u.username AND cr.receiver=:me)
                       ORDER BY cr.id DESC LIMIT 1) AS req_sender
               FROM users u
               WHERE u.username<>:me
               ORDER BY u.username ASC""",
            {"me": current},
        ).fetchall()
    finally:
        con.close()

    results = []
    for r in rows:
        status = r["req_status"] or "none"
        direction = None
        if r["req_sender"] is not None:
            direction = "sent" if r["req_sender"] == current else "received"
        results.append({
            "username": r["username"],
            "profile": r["profile_pic"] or "default.png",
            "status": status,
            "direction": direction,
        })

    return jsonify(results)


@app.route("/api/chat-contacts")
@login_required
def api_chat_contacts():
    """Users the current user has an *accepted* chat request with -- i.e.
    everyone who should show up in the Chats tab. One query, regardless of
    how many total users exist."""
    current = session["user"]
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT DISTINCT u.username, u.profile_pic
               FROM users u
               JOIN chat_requests cr
                 ON (cr.sender=:me AND cr.receiver=u.username)
                 OR (cr.receiver=:me AND cr.sender=u.username)
               WHERE cr.status='accepted'
               ORDER BY u.username ASC""",
            {"me": current},
        ).fetchall()
    finally:
        con.close()

    return jsonify([
        {"username": r["username"], "profile": r["profile_pic"] or "default.png"}
        for r in rows
    ])


@app.route("/api/requests")
@login_required
def api_requests():
    current = session["user"]
    con = database.get_db()
    try:
        rows = con.execute(
            "SELECT id, sender FROM chat_requests WHERE receiver=? AND status='pending' ORDER BY id DESC",
            (current,),
        ).fetchall()
    finally:
        con.close()

    return jsonify([{"id": r["id"], "sender": r["sender"]} for r in rows])


@app.route("/api/request-status")
@login_required
def api_request_status():
    current = session["user"]
    partner = (request.args.get("user") or "").strip().lower()

    con = database.get_db()
    try:
        row = con.execute(
            """SELECT sender, status FROM chat_requests
               WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
               ORDER BY id DESC LIMIT 1""",
            (current, partner, partner, current),
        ).fetchone()
    finally:
        con.close()

    if not row:
        return jsonify({"status": "none"})

    direction = "sent" if row["sender"] == current else "received"
    return jsonify({"status": row["status"], "direction": direction})


@app.route("/api/send-request", methods=["POST"])
@login_required
def api_send_request():
    sender = session["user"]
    receiver = (request.form.get("receiver") or "").strip().lower()

    if not receiver:
        return jsonify({"message": "Select a user first"}), 400
    if sender == receiver:
        return jsonify({"message": "You cannot send a request to yourself"}), 400

    con = database.get_db()
    try:
        row = con.execute(
            """SELECT id, status FROM chat_requests
               WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)""",
            (sender, receiver, receiver, sender),
        ).fetchone()

        if row:
            status = row["status"]
            if status == "accepted":
                return jsonify({"message": "Already connected"})
            if status == "pending":
                return jsonify({"message": "Request already pending"})
            if status == "rejected":
                con.execute(
                    "UPDATE chat_requests SET status='pending', sender=?, receiver=? WHERE id=?",
                    (sender, receiver, row["id"]),
                )
                con.commit()
                return jsonify({"message": "Request sent again"})

        con.execute(
            "INSERT INTO chat_requests(sender, receiver, status) VALUES (?, ?, 'pending')",
            (sender, receiver),
        )
        con.commit()
        return jsonify({"message": "Request sent successfully"})
    except Exception:
        return jsonify({"message": "Server error"}), 500
    finally:
        con.close()


@app.route("/api/accept-request", methods=["POST"])
@login_required
def api_accept_request():
    req_id = request.form.get("id")
    if not req_id:
        return jsonify({"message": "Invalid ID"}), 400

    con = database.get_db()
    try:
        row = con.execute("SELECT * FROM chat_requests WHERE id=?", (req_id,)).fetchone()
        if not row:
            return jsonify({"message": "Request not found"}), 404

        con.execute("UPDATE chat_requests SET status='accepted' WHERE id=?", (req_id,))
        con.commit()
        # Establishing the encrypted thread key happens lazily on first
        # message (get_or_create_thread_key), so nothing else needed here.
        return jsonify({"message": "Accepted", "partner": row["sender"]})
    except Exception as e:
        return jsonify({"message": f"Error: {e}"}), 500
    finally:
        con.close()


@app.route("/api/reject-request", methods=["POST"])
@login_required
def api_reject_request():
    req_id = request.form.get("id")
    con = database.get_db()
    try:
        cur = con.execute("UPDATE chat_requests SET status='rejected' WHERE id=?", (req_id,))
        con.commit()
        return jsonify({"message": "Rejected" if cur.rowcount > 0 else "Failed"})
    except Exception:
        return jsonify({"message": "Error"}), 500
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Thread key management (per-pair AES-256-GCM key + safety number)
# ---------------------------------------------------------------------------
def thread_pair(user1, user2):
    return tuple(sorted([user1.lower(), user2.lower()]))


def get_or_create_thread_key(con, user1, user2):
    a, b = thread_pair(user1, user2)
    with _thread_key_lock:
        cached = _thread_key_cache.get((a, b))
        if cached:
            return cached

    row = con.execute(
        "SELECT enc_key, fingerprint FROM thread_keys WHERE user_a=? AND user_b=?", (a, b)
    ).fetchone()

    if row:
        key = crypto_utils.key_from_b64(row["enc_key"])
        fingerprint = row["fingerprint"]
    else:
        key = crypto_utils.new_thread_key()
        fingerprint = crypto_utils.compute_fingerprint(key, a, b)
        con.execute(
            "INSERT INTO thread_keys(user_a, user_b, enc_key, fingerprint) VALUES (?, ?, ?, ?)",
            (a, b, crypto_utils.key_to_b64(key), fingerprint),
        )
        con.commit()

    with _thread_key_lock:
        _thread_key_cache[(a, b)] = key
    return key


@app.route("/api/thread-info")
@login_required
def api_thread_info():
    current = session["user"]
    partner = (request.args.get("user") or "").strip().lower()
    if not partner:
        return jsonify({"error": "missing user"}), 400

    con = database.get_db()
    try:
        if not are_connected(con, current, partner):
            return jsonify({"error": "not connected"}), 403
        get_or_create_thread_key(con, current, partner)
        a, b = thread_pair(current, partner)
        row = con.execute(
            "SELECT fingerprint, created_at FROM thread_keys WHERE user_a=? AND user_b=?", (a, b)
        ).fetchone()
    finally:
        con.close()

    return jsonify({
        "fingerprint": row["fingerprint"],
        "establishedAt": row["created_at"],
        "encryption": "AES-256-GCM (per-thread key)",
    })


@app.route("/api/history")
@login_required
def api_history():
    current = session["user"]
    partner = (request.args.get("user") or "").strip().lower()

    if not partner:
        return jsonify([])

    con = database.get_db()
    try:
        if not are_connected(con, current, partner):
            return jsonify([])

        key = get_or_create_thread_key(con, current, partner)

        rows = con.execute(
            """SELECT id, sender, receiver, ciphertext, nonce, attachment, sent_at,
                      is_seen, reply_to, reaction, expires_at, burned
               FROM messages
               WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?))
               AND (is_deleted IS NULL OR is_deleted=0)
               ORDER BY id ASC""",
            (current, partner, partner, current),
        ).fetchall()

        results = []
        for r in rows:
            if r["burned"]:
                continue
            try:
                text = crypto_utils.decrypt_message(key, r["ciphertext"], r["nonce"]) if r["ciphertext"] else ""
            except Exception:
                text = "[unable to decrypt]"
            results.append({
                "id": r["id"],
                "sender": r["sender"],
                "receiver": r["receiver"],
                "message": text,
                "attachment": r["attachment"],
                "sentAt": r["sent_at"],
                "isSeen": bool(r["is_seen"]),
                "replyTo": r["reply_to"],
                "reaction": r["reaction"] or "",
                "expiresAt": r["expires_at"],
            })
    finally:
        con.close()

    return jsonify(results)


@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    file_name = save_upload(request.files.get("file"), UPLOAD_DIR)
    return file_name or ""


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        if session.get("admin"):
            return redirect(url_for("admin_dashboard"))
        return render_template("admin/login.html", error=request.args.get("error"))

    username = request.form.get("username") or ""
    password = request.form.get("password") or ""

    con = database.get_db()
    try:
        row = con.execute(
            "SELECT password FROM admin WHERE username=? AND is_active=1", (username,)
        ).fetchone()
    finally:
        con.close()

    if row and check_password_hash(row["password"], password):
        session["admin"] = username
        return redirect(url_for("admin_dashboard"))

    return redirect(url_for("admin_login", error="invalid"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    con = database.get_db()
    try:
        total_users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

        cutoff = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
        online_users = con.execute(
            "SELECT COUNT(*) c FROM users WHERE last_seen > ?", (cutoff,)
        ).fetchone()["c"]

        total_messages = con.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        total_posts = con.execute("SELECT COUNT(*) c FROM posts WHERE is_deleted=0").fetchone()["c"]
        total_comments = con.execute("SELECT COUNT(*) c FROM comments WHERE is_deleted=0").fetchone()["c"]
        total_likes = con.execute("SELECT COUNT(*) c FROM likes").fetchone()["c"]
        failed_logins_today = con.execute(
            "SELECT COUNT(*) c FROM login_audit WHERE success=0 AND created_at > ?",
            ((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),),
        ).fetchone()["c"]
        recent_posts = con.execute(
            "SELECT id, username, image, created_at FROM posts WHERE is_deleted=0 ORDER BY id DESC LIMIT 8"
        ).fetchall()
    finally:
        con.close()

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        online_users=online_users,
        total_messages=total_messages,
        total_posts=total_posts,
        total_comments=total_comments,
        total_likes=total_likes,
        failed_logins_today=failed_logins_today,
        recent_posts=recent_posts,
    )


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    con = database.get_db()
    try:
        if request.method == "POST":
            username = request.form.get("username")
            status = int(request.form.get("status", 0))
            con.execute("UPDATE users SET is_blocked=? WHERE username=?", (status, username))
            con.commit()
            return redirect(url_for("admin_users"))

        rows = con.execute(
            """SELECT u.username, u.is_blocked, u.created_at, u.last_seen,
                      (SELECT COUNT(*) FROM posts WHERE username=u.username AND is_deleted=0) AS post_count
               FROM users u ORDER BY u.username ASC"""
        ).fetchall()
    finally:
        con.close()

    users = [
        {"username": r["username"], "is_blocked": r["is_blocked"],
         "created_at": r["created_at"], "last_seen": r["last_seen"], "post_count": r["post_count"]}
        for r in rows
    ]
    return render_template("admin/users.html", users=users)


@app.route("/admin/posts")
@admin_required
def admin_posts():
    con = database.get_db()
    try:
        rows = con.execute(
            """SELECT p.id, p.username, p.image, p.caption, p.media_type, p.created_at,
                      (SELECT COUNT(*) FROM likes WHERE post_id=p.id) AS like_count,
                      (SELECT COUNT(*) FROM comments WHERE post_id=p.id AND is_deleted=0) AS comment_count
               FROM posts p WHERE p.is_deleted=0 ORDER BY p.id DESC LIMIT 100"""
        ).fetchall()
    finally:
        con.close()
    posts = [dict(r) for r in rows]
    return render_template("admin/posts.html", posts=posts)


@app.route("/admin/posts/<int:post_id>/remove", methods=["POST"])
@admin_required
def admin_remove_post(post_id):
    con = database.get_db()
    try:
        con.execute("UPDATE posts SET is_deleted=1 WHERE id=?", (post_id,))
        con.commit()
    finally:
        con.close()
    return redirect(url_for("admin_posts"))


# ---------------------------------------------------------------------------
# WebSocket: real-time encrypted chat
# Protocol (pipe-delimited, mirrors the original design but adds encryption
# + disappearing messages):
#   TYPING|<receiver>
#   DELETE|<id>
#   EDIT|<id>|<urlencoded text>
#   REACT|<id>|<emoji>
#   CHAT|<receiver>|<urlencoded message>|<replyToId or 0>|<ttlSeconds or 0>
# ---------------------------------------------------------------------------
def send_to(username, payload: dict):
    """Send a JSON payload to a user if they're currently connected."""
    with connected_lock:
        ws = connected_users.get(username)
    if not ws:
        return
    try:
        ws.send(json.dumps(payload))
    except Exception:
        with connected_lock:
            if connected_users.get(username) is ws:
                del connected_users[username]


def broadcast_status(username, online):
    with connected_lock:
        targets = list(connected_users.items())
    payload = {"type": "status", "user": username, "online": online}
    for name, ws in targets:
        try:
            ws.send(json.dumps(payload))
        except Exception:
            pass


def update_last_seen(username):
    con = database.get_db()
    try:
        con.execute(
            "UPDATE users SET last_seen=? WHERE username=?", (now_iso(), username)
        )
        con.commit()
    finally:
        con.close()


def are_connected(con, user1, user2):
    row = con.execute(
        """SELECT 1 FROM chat_requests
           WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?))
           AND status='accepted'""",
        (user1, user2, user2, user1),
    ).fetchone()
    return row is not None


def handle_chat(sender, receiver, text, reply_to, ttl_seconds, ws):
    con = database.get_db()
    try:
        if not are_connected(con, sender, receiver):
            ws.send(json.dumps({"type": "system", "message": "Request not accepted \u274c"}))
            return

        key = get_or_create_thread_key(con, sender, receiver)
        ciphertext, nonce = crypto_utils.encrypt_message(key, text)

        sent_at = now_iso()
        expires_at = None
        if ttl_seconds and ttl_seconds in DISAPPEARING_CHOICES and ttl_seconds > 0:
            expires_at = (datetime.now() + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%S")

        cur = con.execute(
            """INSERT INTO messages(sender, receiver, ciphertext, nonce, sent_at, is_seen, reply_to, expires_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (sender, receiver, ciphertext, nonce, sent_at, reply_to, expires_at),
        )
        con.commit()
        msg_id = cur.lastrowid

        payload = {
            "type": "chat",
            "id": msg_id,
            "sender": sender,
            "receiver": receiver,
            "message": text,
            "replyTo": reply_to,
            "sentAt": sent_at,
            "expiresAt": expires_at,
        }

        ws.send(json.dumps(payload))  # echo to sender

        with connected_lock:
            receiver_ws = connected_users.get(receiver)

        if receiver_ws:
            try:
                receiver_ws.send(json.dumps(payload))
                con.execute(
                    "UPDATE messages SET is_seen=1 WHERE receiver=? AND sender=? AND is_seen=0",
                    (receiver, sender),
                )
                con.commit()
                send_to(sender, {"type": "seen", "id": msg_id})
            except Exception:
                with connected_lock:
                    if connected_users.get(receiver) is receiver_ws:
                        del connected_users[receiver]
    finally:
        con.close()


def handle_delete(sender, msg_id):
    con = database.get_db()
    try:
        row = con.execute(
            "SELECT sender, receiver FROM messages WHERE id=?", (msg_id,)
        ).fetchone()
        if not row or row["sender"] != sender:
            return
        con.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
        con.commit()
        payload = {"type": "delete", "id": msg_id}
        send_to(row["sender"], payload)
        send_to(row["receiver"], payload)
    finally:
        con.close()


def handle_edit(sender, msg_id, new_text):
    con = database.get_db()
    try:
        row = con.execute(
            "SELECT sender, receiver FROM messages WHERE id=?", (msg_id,)
        ).fetchone()
        if not row or row["sender"] != sender:
            return

        key = get_or_create_thread_key(con, row["sender"], row["receiver"])
        ciphertext, nonce = crypto_utils.encrypt_message(key, new_text)

        con.execute(
            "UPDATE messages SET ciphertext=?, nonce=? WHERE id=? AND sender=?",
            (ciphertext, nonce, msg_id, sender),
        )
        con.commit()
        payload = {"type": "edit", "id": msg_id, "message": new_text}
        send_to(row["sender"], payload)
        send_to(row["receiver"], payload)
    finally:
        con.close()


def handle_react(msg_id, emoji):
    con = database.get_db()
    try:
        row = con.execute(
            "SELECT sender, receiver FROM messages WHERE id=?", (msg_id,)
        ).fetchone()
        if not row:
            return
        con.execute("UPDATE messages SET reaction=? WHERE id=?", (emoji, msg_id))
        con.commit()
        payload = {"type": "react", "id": msg_id, "emoji": emoji}
        send_to(row["sender"], payload)
        send_to(row["receiver"], payload)
    finally:
        con.close()


@sock.route("/ws/<username>")
def ws_chat(ws, username):
    username = username.strip().lower()

    # Only allow the socket to bind to the session's own logged-in user,
    # so one tab can't eavesdrop by opening a socket as someone else.
    if session.get("user") != username:
        ws.close()
        return

    with connected_lock:
        connected_users[username] = ws

    update_last_seen(username)
    broadcast_status(username, True)

    try:
        while True:
            message = ws.receive()
            if message is None:
                break

            update_last_seen(username)

            try:
                if message.startswith("TYPING|"):
                    receiver = message.split("|", 1)[1]
                    send_to(receiver, {"type": "typing", "sender": username})

                elif message.startswith("DELETE|"):
                    msg_id = int(message.split("|", 1)[1])
                    handle_delete(username, msg_id)

                elif message.startswith("EDIT|"):
                    _, msg_id, new_text = message.split("|", 2)
                    from urllib.parse import unquote
                    handle_edit(username, int(msg_id), unquote(new_text))

                elif message.startswith("REACT|"):
                    _, msg_id, emoji = message.split("|", 2)
                    handle_react(int(msg_id), emoji)

                elif message.startswith("CHAT|"):
                    from urllib.parse import unquote
                    parts = message.split("|", 4)
                    receiver = parts[1].strip().lower()
                    text = unquote(parts[2])
                    reply_to = None
                    if len(parts) >= 4 and parts[3] and parts[3] != "0":
                        try:
                            reply_to = int(parts[3])
                        except ValueError:
                            reply_to = None
                    ttl_seconds = 0
                    if len(parts) == 5:
                        try:
                            ttl_seconds = int(parts[4])
                        except ValueError:
                            ttl_seconds = 0
                    handle_chat(username, receiver, text, reply_to, ttl_seconds, ws)
            except Exception:
                pass
    finally:
        with connected_lock:
            if connected_users.get(username) is ws:
                del connected_users[username]
        broadcast_status(username, False)


# ---------------------------------------------------------------------------
# Background sweeper: burns expired disappearing messages and notifies
# both participants so the UI can remove them live.
# ---------------------------------------------------------------------------
def disappearing_message_sweeper():
    while True:
        try:
            con = database.get_db()
            try:
                rows = con.execute(
                    """SELECT id, sender, receiver FROM messages
                       WHERE expires_at IS NOT NULL AND expires_at <= ? AND burned=0""",
                    (now_iso(),),
                ).fetchall()
                for r in rows:
                    con.execute("UPDATE messages SET burned=1 WHERE id=?", (r["id"],))
                    payload = {"type": "burn", "id": r["id"]}
                    send_to(r["sender"], payload)
                    send_to(r["receiver"], payload)
                if rows:
                    con.commit()
            finally:
                con.close()
        except Exception:
            pass
        time.sleep(5)


if __name__ == "__main__":
    database.init_db()
    threading.Thread(target=disappearing_message_sweeper, daemon=True).start()
    print("SamvadX running at http://localhost:5000")
    print("Admin panel:        http://localhost:5000/admin/login  (admin / admin123)")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)

# =====================
# UPDATE NOTES
# =====================
# Add the api_edit_post() route discussed in chat.
# Real-time '2h ago' timestamps should be implemented in frontend JS
# using the createdAt value returned by the API.
