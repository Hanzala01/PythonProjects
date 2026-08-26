#!/usr/bin/env python3
"""
celestial_bridge — where the board's data actually lives.

Until now every trade, level and preference sat in one browser's localStorage.
Clear the cache and it was gone; open the page on a phone and it was empty.
This is the other half: a small server and one SQLite file, so the data
belongs to an account instead of to a browser.

    pip install fastapi uvicorn
    python3 celestial_bridge.py

It listens on 127.0.0.1:8770 and writes celestial.db beside this file. That
is the whole deployment. Point the page at it from Settings.

WHAT IT DELIBERATELY IS NOT
    It is not multi-tenant infrastructure and it does not want to be. It
    binds to localhost, holds one household's data, and has no admin, no
    sharing, no roles. If it is ever put on a public address it must sit
    behind TLS — the login sends a password, and over plain http on a
    network you do not own that password is readable in transit.

WHAT IS STORED
    Exactly the keys the page already writes, by name — ca_journal,
    ca_major_levels, ca_risk_prefs and the rest. Values are opaque JSON
    strings; the server never parses or validates their shape, so the page
    can change what it puts in them without the server needing to know.

WHAT IS NOT STORED
    API keys. ca_jarvis holds the Claude and ElevenLabs keys, so it is
    refused by name — syncing it would copy those keys to a database and
    then to every device, turning one exposure into several. They stay in
    the browser they were typed into. Broker credentials do belong on a
    server, but not in this one yet; that is a separate job with its own
    encryption, and pretending otherwise here would be worse than the gap.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

DB_PATH = os.environ.get("CELESTIAL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "celestial.db"))
HOST = os.environ.get("CELESTIAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("CELESTIAL_PORT", "8770"))

# A value is one JSON blob for one key. The journal is the big one and it is
# a few hundred rows of small objects; a megabyte is far past generous, and
# the cap exists so a runaway loop in the page cannot fill the disk.
MAX_VALUE = 1_048_576
MAX_KEYS = 64

# Only the page's own namespace, and never the one holding the keys.
KEY_PREFIX = "ca_"
# ca_jarvis holds the API keys. The ca_sync_* keys hold this server's own
# session token and the pre-sign-in backup — round-tripping either through
# the server would be circular at best.
KEY_DENY = {"ca_jarvis", "ca_sync", "ca_sync_ts", "ca_sync_shadow"}

SESSION_TTL = 60 * 60 * 24 * 30  # a month, refreshed on use


# ── database ──────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  salt       BLOB NOT NULL,
  pw         BLOB NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  seen_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS store (
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  k          TEXT NOT NULL,
  v          TEXT NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (user_id, k)
);
CREATE INDEX IF NOT EXISTS store_user ON store(user_id);
CREATE INDEX IF NOT EXISTS sess_user  ON sessions(user_id);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    # The page can have several tabs open and each one writes. WAL lets a
    # read run while a write is in flight instead of returning "database is
    # locked" to whichever tab lost the race.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript(SCHEMA)
    # The file holds password hashes and every trade taken. On a shared
    # machine the default umask would let other accounts read it.
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


# ── passwords ─────────────────────────────────────────────────────────────
# scrypt, from the standard library. The point of a slow hash is that a
# stolen database cannot be turned into a list of passwords at speed; a
# plain sha256 of the password would be readable in minutes.
def hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def check_pw(password: str, salt: bytes, expected: bytes) -> bool:
    # compare_digest, not ==, so the comparison does not leak how much of the
    # hash matched through how long it took
    return hmac.compare_digest(hash_pw(password, salt), expected)


# ── login attempts ────────────────────────────────────────────────────────
# A password that can be guessed at machine speed is not a password. This is
# in memory and resets when the server restarts, which is the right trade for
# something this small — it is a brake, not a vault.
_FAILS: dict[str, list[float]] = {}
FAIL_WINDOW = 900
FAIL_MAX = 8


def note_fail(who: str):
    now = time.time()
    hits = [t for t in _FAILS.get(who, []) if now - t < FAIL_WINDOW]
    hits.append(now)
    _FAILS[who] = hits


def too_many(who: str) -> bool:
    now = time.time()
    hits = [t for t in _FAILS.get(who, []) if now - t < FAIL_WINDOW]
    _FAILS[who] = hits
    return len(hits) >= FAIL_MAX


# ── app ───────────────────────────────────────────────────────────────────
app = FastAPI(title="celestial_bridge", docs_url=None, redoc_url=None)

# The page is opened from a file or from a little http.server, so the origin
# varies. Listing them beats allow-all: with credentials in a header rather
# than a cookie the risk is smaller, but a wide-open CORS policy would let
# any site you visit read this data from your own machine.
ORIGINS = [o.strip() for o in os.environ.get(
    "CELESTIAL_ORIGINS",
    "http://localhost:8080,http://127.0.0.1:8080,http://localhost:8099,http://127.0.0.1:8099",
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def user_from_token(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "no token")
    token = authorization[7:].strip()
    now = time.time()
    with db() as con:
        row = con.execute("SELECT user_id, seen_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(401, "unknown token")
        if now - row["seen_at"] > SESSION_TTL:
            con.execute("DELETE FROM sessions WHERE token=?", (token,))
            raise HTTPException(401, "session expired")
        # only touch the row when it is stale, so every request is not a write
        if now - row["seen_at"] > 3600:
            con.execute("UPDATE sessions SET seen_at=? WHERE token=?", (now, token))
        return int(row["user_id"])


def check_key(k: str):
    if not isinstance(k, str) or not k.startswith(KEY_PREFIX) or len(k) > 64:
        raise HTTPException(400, f"key must start with {KEY_PREFIX!r}")
    if k in KEY_DENY:
        raise HTTPException(
            403,
            f"{k} is not synced on purpose — it holds your API keys, and copying "
            "those to a database and then to every device turns one exposure into several",
        )


@app.get("/health")
def health():
    return {"ok": True, "service": "celestial_bridge", "db": os.path.basename(DB_PATH)}


# ── auth ──────────────────────────────────────────────────────────────────
@app.post("/auth/signup")
def signup(email: str = Body(...), password: str = Body(...)):
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 200:
        raise HTTPException(400, "that does not look like an email address")
    if len(password or "") < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    now = time.time()
    with db() as con:
        try:
            cur = con.execute(
                "INSERT INTO users(email,salt,pw,created_at) VALUES(?,?,?,?)",
                (email, salt, hash_pw(password, salt), now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "that email already has an account — log in instead")
        uid = cur.lastrowid
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions(token,user_id,created_at,seen_at) VALUES(?,?,?,?)",
                    (token, uid, now, now))
    return {"token": token, "email": email}


@app.post("/auth/login")
def login(request: Request, email: str = Body(...), password: str = Body(...)):
    email = (email or "").strip().lower()
    who = f"{request.client.host if request.client else '?'}|{email}"
    if too_many(who):
        raise HTTPException(429, "too many attempts — wait fifteen minutes")
    now = time.time()
    with db() as con:
        row = con.execute("SELECT id,salt,pw FROM users WHERE email=?", (email,)).fetchone()
        # Same message and roughly the same work whether the account exists or
        # the password is wrong, so this cannot be used to find out which
        # emails have accounts.
        if not row:
            hash_pw(password or "", b"x" * 16)
            note_fail(who)
            raise HTTPException(401, "wrong email or password")
        if not check_pw(password or "", row["salt"], row["pw"]):
            note_fail(who)
            raise HTTPException(401, "wrong email or password")
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions(token,user_id,created_at,seen_at) VALUES(?,?,?,?)",
                    (token, row["id"], now, now))
    _FAILS.pop(who, None)
    return {"token": token, "email": email}


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        with db() as con:
            con.execute("DELETE FROM sessions WHERE token=?", (authorization[7:].strip(),))
    return {"ok": True}


@app.get("/auth/me")
def me(uid: int = Depends(user_from_token)):
    with db() as con:
        row = con.execute("SELECT email,created_at FROM users WHERE id=?", (uid,)).fetchone()
        n = con.execute("SELECT COUNT(*) c FROM store WHERE user_id=?", (uid,)).fetchone()["c"]
    return {"email": row["email"], "since": row["created_at"], "keys": n}


# ── the data ──────────────────────────────────────────────────────────────
@app.get("/data")
def get_all(uid: int = Depends(user_from_token)):
    with db() as con:
        rows = con.execute("SELECT k,v,updated_at FROM store WHERE user_id=?", (uid,)).fetchall()
    return {"items": {r["k"]: {"v": r["v"], "updated_at": r["updated_at"]} for r in rows}}


@app.put("/data")
def put_many(items: list[dict] = Body(...), uid: int = Depends(user_from_token)):
    """
    LAST WRITE WINS, AND THE CLOCK IS THE CLIENT'S.

    Two devices editing the same journal will disagree, and something has to
    decide. This keeps whichever write claims the later updated_at, and hands
    back what it kept so the page can correct itself rather than assume its
    own copy won. It is not a merge — a genuine merge needs per-row history,
    which the page does not keep — but it is predictable, and it says so.
    """
    if not isinstance(items, list):
        raise HTTPException(400, "expected a list")
    now = time.time()
    out = {}
    with db() as con:
        have = con.execute("SELECT COUNT(*) c FROM store WHERE user_id=?", (uid,)).fetchone()["c"]
        for it in items:
            k = (it or {}).get("k")
            v = (it or {}).get("v")
            check_key(k)
            if v is None:
                con.execute("DELETE FROM store WHERE user_id=? AND k=?", (uid, k))
                out[k] = {"deleted": True}
                continue
            if not isinstance(v, str):
                v = json.dumps(v, separators=(",", ":"))
            if len(v) > MAX_VALUE:
                raise HTTPException(413, f"{k} is {len(v)} bytes, the limit is {MAX_VALUE}")
            ts = float(it.get("updated_at") or now)
            cur = con.execute("SELECT v,updated_at FROM store WHERE user_id=? AND k=?", (uid, k)).fetchone()
            if cur is None and have >= MAX_KEYS:
                raise HTTPException(409, f"this account already holds {MAX_KEYS} keys")
            if cur is not None and cur["updated_at"] > ts:
                out[k] = {"v": cur["v"], "updated_at": cur["updated_at"], "kept": "server"}
                continue
            con.execute(
                "INSERT INTO store(user_id,k,v,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id,k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                (uid, k, v, ts),
            )
            if cur is None:
                have += 1
            out[k] = {"updated_at": ts, "kept": "client"}
    return {"items": out}


@app.delete("/data/{k}")
def delete_one(k: str, uid: int = Depends(user_from_token)):
    check_key(k)
    with db() as con:
        con.execute("DELETE FROM store WHERE user_id=? AND k=?", (uid, k))
    return {"ok": True}


@app.get("/export")
def export_all(uid: int = Depends(user_from_token)):
    """Everything, in one file, in a shape the page can read back. A backend
    you cannot get your data out of is a worse place to keep it than a
    browser was."""
    with db() as con:
        rows = con.execute("SELECT k,v,updated_at FROM store WHERE user_id=?", (uid,)).fetchall()
        email = con.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()["email"]
    body = {"celestial_export": 1, "email": email, "at": time.time(),
            "items": {r["k"]: {"v": r["v"], "updated_at": r["updated_at"]} for r in rows}}
    return JSONResponse(body, headers={"Content-Disposition": 'attachment; filename="celestial-backup.json"'})


if __name__ == "__main__":
    import uvicorn

    init_db()
    print(f"celestial_bridge → http://{HOST}:{PORT}   db: {DB_PATH}")
    print(f"origins allowed: {', '.join(ORIGINS)}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
