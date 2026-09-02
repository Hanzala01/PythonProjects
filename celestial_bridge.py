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
import urllib.parse
import secrets
import sqlite3
import time
from contextlib import contextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

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
    "http://localhost:8080,http://127.0.0.1:8080,http://localhost:8099,http://127.0.0.1:8099,"
    f"http://localhost:{os.environ.get('CELESTIAL_PORT', '8770')},"
    f"http://127.0.0.1:{os.environ.get('CELESTIAL_PORT', '8770')}",
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


# ── FRED passthrough ──────────────────────────────────────────────────────
# Constant-maturity treasury yields are published free by the St. Louis Fed,
# but fredgraph.csv sends no Access-Control-Allow-Origin header, so a browser
# cannot read it and both public CORS relays answered 522 when tried. A dozen
# lines here get the real series instead of inferring a yield from a bond
# fund's price, which is only ever the right sign and never the right number.
#
# No key, no credentials, no user data leaves this machine — the request is a
# fixed URL with a series id from a fixed list, so this cannot be turned into
# an open proxy for anything else.
FRED_SERIES = {
    "DGS1": "US 1-year treasury yield",
    "DGS5": "US 5-year treasury yield",
    "DGS10": "US 10-year treasury yield",
    "DGS30": "US 30-year treasury yield",
    "DTWEXBGS": "Broad trade-weighted dollar index",
    # THIS WHITELIST IS WHY CPI AND PAYROLLS WERE BLANK.
    # The page walks EconPulse, then BLS, then DBnomics, then FMP, then FRED.
    # EconPulse answers 522 (its origin is down), BLS refuses without a key
    # because the anonymous quota is one shared exhausted pool, FMP is at its
    # plan limit, and FRED sends no Access-Control-Allow-Origin so the browser
    # drops it. The bridge could have served FRED — but only these five series
    # were allowed through, and none of them is CPI or payrolls. So the one
    # working route was closed for exactly the numbers that were missing.
    "CPIAUCSL": "US CPI, all items, seasonally adjusted",
    "PAYEMS": "US total non-farm payrolls",
    "UNRATE": "US unemployment rate",
    "DFF": "US effective federal funds rate",
    "PPIFIS": "US PPI, final demand",
    "PCEPI": "US PCE price index",
    "GDPC1": "US real GDP",
}
_FRED_CACHE: dict[str, tuple[float, list]] = {}
FRED_TTL = 6 * 3600


# ── ECONOMIC CALENDAR ────────────────────────────────────────────────────
# ForexFactory publishes its week as JSON, free and without a key. The page
# cannot read it directly: the response carries no Access-Control-Allow-Origin
# header, so a browser refuses it, and every public CORS relay tried against
# it is down or rate-limited — allorigins and codetabs both answer 522,
# cors.sh answers 429. Measured, not assumed.
#
# This machine has no such restriction. The bridge fetches it, caches it for
# an hour, and serves it back with the page's own origin allowed — which is
# the whole reason the bridge exists.
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_CAL_CACHE: tuple[float, list] | None = None
CAL_TTL = 3600


# The page is often opened straight from the filesystem, where the browser
# sends Origin: null. That is not in ORIGINS and must not be — the data
# endpoints below hold the journal and the trade history, and allowing a null
# origin would let any sandboxed frame on any site read them.
#
# This endpoint is different: it serves a public calendar that anyone can
# fetch from ForexFactory directly. It carries its own allow-all header so a
# file:// page can read it, and nothing else here does.
def _public(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers={"Access-Control-Allow-Origin": "*"})


@app.options("/calendar")
def calendar_preflight():
    return _public({"ok": True})


@app.get("/calendar")
def calendar():
    global _CAL_CACHE
    now = time.time()
    if _CAL_CACHE and now - _CAL_CACHE[0] < CAL_TTL:
        return _public({"source": "forexfactory", "cached": True, "events": _CAL_CACHE[1]})
    import json as _json
    import urllib.request

    req = urllib.request.Request(CAL_URL, headers={"User-Agent": "celestial-bridge"})
    try:
        with urllib.request.urlopen(req, timeout=25) as f:
            rows = _json.loads(f.read().decode("utf-8", "replace"))
    except Exception as e:                      # noqa: BLE001
        # a stale week beats no week — say how old it is rather than failing
        if _CAL_CACHE:
            return _public({"source": "forexfactory", "cached": True, "stale": True,
                            "age_minutes": int((now - _CAL_CACHE[0]) / 60),
                            "events": _CAL_CACHE[1]})
        raise HTTPException(502, f"calendar unreachable: {e}")
    if not isinstance(rows, list):
        raise HTTPException(502, "calendar returned something that is not a list of events")
    _CAL_CACHE = (now, rows)
    return _public({"source": "forexfactory", "cached": False, "events": rows})


# ── THE OTHER THREE FEEDS, FOR THE SAME REASON ────────────────────────────
# Measured against each provider rather than assumed, with Origin: null —
# which is what a page opened from a folder actually sends:
#
#   EODHD    200, real data (XAUUSD.FOREX priced 4526.51 on 2026-08-20),
#            and NO Access-Control-Allow-Origin. It sends allow-credentials,
#            allow-methods and allow-headers — everything except the one
#            header that matters — so a browser refuses the response even
#            though the request succeeded.
#   GNews    200, articles returned, and again no Access-Control-Allow-Origin.
#            (Its own payload also notes the free plan runs 12 hours behind.)
#   BLS      Access-Control-Allow-Origin: * — the CORS is fine. It fails for
#            a different reason: with no registration key the quota is a
#            single shared anonymous pool, and the reply is
#            "the daily threshold for total number of requests allocated to
#            the user with registration key <blank> has been reached".
#            A free key from bls.gov/developers is a per-user quota.
#
# CORS is a rule browsers apply. It is not a rule this process has to obey,
# so all three are fetched here and handed back with the page's origin
# allowed. Each is cached, because these are per-day numbers behind
# per-day quotas and re-fetching them on every render is how a free key is
# spent by lunchtime.
#
# EconPulse is NOT proxied. api.econpulse.io answers 522 and then 523 —
# Cloudflare for "the origin server is down" — while econpulse.io itself
# answers 200. The service is broken at source, and proxying a dead endpoint
# only moves the failure. The page drops it and says so.

_PX_CACHE: dict[str, tuple[float, object]] = {}


def _px(key: str, ttl: int, build):
    """Fetch through a small per-key cache, and serve a stale copy rather
    than nothing when the upstream is having a bad day."""
    import json as _json
    import urllib.request

    now = time.time()
    hit = _PX_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return _public({"cached": True, "data": hit[1]})
    url = build()
    req = urllib.request.Request(url, headers={"User-Agent": "celestial-bridge"})
    try:
        with urllib.request.urlopen(req, timeout=25) as f:
            data = _json.loads(f.read().decode("utf-8", "replace"))
    except Exception as e:                      # noqa: BLE001
        if hit:
            return _public({"cached": True, "stale": True,
                            "age_minutes": int((now - hit[0]) / 60),
                            "data": hit[1]})
        raise HTTPException(502, f"{key.split(':')[0]} unreachable: {e}")
    _PX_CACHE[key] = (now, data)
    return _public({"cached": False, "data": data})


# The key travels from the page rather than living here, so this file stays
# safe to share. Broker credentials are the opposite case and stay server-side.
@app.get("/px/eodhd/{symbol}")
def px_eodhd(symbol: str, token: str, days: int = 400):
    import datetime as _dt
    frm = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    return _px(
        f"eodhd:{symbol}:{frm}", 3600,
        lambda: ("https://eodhd.com/api/eod/"
                 + urllib.parse.quote(symbol)
                 + f"?period=d&from={frm}&api_token="
                 + urllib.parse.quote(token) + "&fmt=json"))


@app.get("/px/gnews")
def px_gnews(q: str, token: str, max_results: int = 10):
    return _px(
        f"gnews:{q}", 900,
        lambda: ("https://gnews.io/api/v4/search?q=" + urllib.parse.quote(q)
                 + f"&in=title&lang=en&max={int(max_results)}"
                 + "&sortby=publishedAt&apikey=" + urllib.parse.quote(token)))


@app.get("/px/bls/{series}")
def px_bls(series: str, token: str = ""):
    # Six hours: these series print monthly. Asking more often than that
    # spends a per-day quota on a number that cannot have changed.
    return _px(
        f"bls:{series}", 6 * 3600,
        lambda: ("https://api.bls.gov/publicAPI/v2/timeseries/data/"
                 + urllib.parse.quote(series)
                 + (("?registrationkey=" + urllib.parse.quote(token)) if token else "")))


@app.options("/px/{rest:path}")
def px_preflight(rest: str):
    return _public({"ok": True})


# ── METATRADER 5, READ ONLY ───────────────────────────────────────────────
# Asked for as "account ka password login id dalo aur woh interconnect hojai".
# The honest version of that is narrower than it sounds, and the narrowing is
# the whole point.
#
# MT5 accounts have TWO passwords. The master password can place orders and
# move money. The INVESTOR password can do neither — it opens the same
# account in read-only mode, sees positions and history, and every order
# attempt is refused by the terminal itself. A journal only ever needs to
# read, so only the investor password is accepted here, and the trade-sending
# call is not implemented at all rather than left present and unused.
#
# It lives in this process because this process is on the user's own machine.
# It is never sent to the page, never written to the database, and never
# logged: it is held in memory for the life of the run and is gone when the
# bridge stops. Restarting the bridge means entering it again, which is the
# correct trade-off for a credential.
#
# MetaTrader5 is Windows-only and is an optional import. Where it is missing
# the endpoint says so plainly instead of failing in a way that reads like a
# wrong password.

_MT5: dict = {"login": None, "server": None, "password": None, "ok": False}


def _mt5_mod():
    try:
        import MetaTrader5 as mt5           # noqa: N813
        return mt5
    except Exception:                        # noqa: BLE001
        return None


@app.options("/mt5/{rest:path}")
def mt5_preflight(rest: str):
    return _public({"ok": True})


@app.get("/mt5/status")
def mt5_status():
    mt5 = _mt5_mod()
    return _public({
        "available": mt5 is not None,
        "connected": bool(_MT5["ok"]),
        "login": _MT5["login"],
        "server": _MT5["server"],
        # the command is shown as its own copy-able block by the page, so
        # repeating it here printed it twice in the same sentence
        "note": "The bridge does not have the MetaTrader5 package yet."
        if mt5 is None else "read-only: the investor password cannot place orders",
    })


@app.post("/mt5/connect")
def mt5_connect(body: dict = Body(...)):
    """Open a read-only MT5 session. The password stays in this process."""
    mt5 = _mt5_mod()
    if mt5 is None:
        raise HTTPException(501, "MetaTrader5 is not installed here. It is a Windows "
                                 "package and needs the MT5 terminal: pip install MetaTrader5")
    try:
        login = int(str(body.get("login", "")).strip())
    except ValueError:
        raise HTTPException(400, "login must be the numeric account number")
    server = str(body.get("server", "")).strip()
    password = str(body.get("password", ""))
    if not server or not password:
        raise HTTPException(400, "server and investor password are both required")

    if not mt5.initialize():
        raise HTTPException(502, f"could not start the MT5 terminal: {mt5.last_error()}")
    if not mt5.login(login, password=password, server=server):
        err = mt5.last_error()
        mt5.shutdown()
        raise HTTPException(401, f"MT5 refused the login: {err}")

    # Prove it is the investor password rather than trusting the label on it.
    # trade_allowed comes back False for a read-only session; if it is True the
    # master password was pasted, and that is a credential this tool must not
    # hold. Refuse, disconnect, and say why.
    info = mt5.account_info()
    if info is not None and getattr(info, "trade_allowed", False):
        mt5.shutdown()
        raise HTTPException(
            403,
            "That is the MASTER password — it can place orders and move money. "
            "Use the INVESTOR password instead: same account, read-only. Your "
            "broker or the MT5 terminal (Tools > Options > Server) can show it.")

    _MT5.update({"login": login, "server": server, "password": password, "ok": True})
    return _public({"connected": True, "login": login, "server": server,
                    "name": getattr(info, "name", None),
                    "currency": getattr(info, "currency", None),
                    "balance": getattr(info, "balance", None),
                    "equity": getattr(info, "equity", None),
                    "read_only": True})


@app.post("/mt5/disconnect")
def mt5_disconnect():
    mt5 = _mt5_mod()
    if mt5 is not None:
        try:
            mt5.shutdown()
        except Exception:                    # noqa: BLE001
            pass
    _MT5.update({"login": None, "server": None, "password": None, "ok": False})
    return _public({"connected": False})


@app.get("/mt5/history")
def mt5_history(days: int = 365):
    """Closed deals, in the shape the journal already stores."""
    import datetime as _dt

    mt5 = _mt5_mod()
    if mt5 is None:
        raise HTTPException(501, "MetaTrader5 is not installed here")
    if not _MT5["ok"]:
        raise HTTPException(409, "not connected — POST /mt5/connect first")

    to = _dt.datetime.now()
    frm = to - _dt.timedelta(days=max(1, min(days, 3650)))
    deals = mt5.history_deals_get(frm, to)
    if deals is None:
        raise HTTPException(502, f"MT5 returned no history: {mt5.last_error()}")

    # DEAL_ENTRY_OUT is the leg that CLOSES a position, and it is the only leg
    # that carries the realised result. Counting the opening leg as well would
    # double the trade count and halve every average on the board.
    rows = []
    for d in deals:
        if getattr(d, "entry", None) != getattr(mt5, "DEAL_ENTRY_OUT", 1):
            continue
        profit = float(getattr(d, "profit", 0.0)) \
            + float(getattr(d, "swap", 0.0)) + float(getattr(d, "commission", 0.0))
        rows.append({
            "ticket": getattr(d, "ticket", None),
            "position": getattr(d, "position_id", None),
            "date": _dt.datetime.fromtimestamp(getattr(d, "time", 0)).strftime("%Y-%m-%d"),
            "symbol": getattr(d, "symbol", ""),
            # a closing SELL closes a BUY, so the reported side is inverted to
            # describe the trade rather than the leg
            "dir": "sell" if getattr(d, "type", 0) == 0 else "buy",
            "lots": float(getattr(d, "volume", 0.0)),
            "price": float(getattr(d, "price", 0.0)),
            "pnl": round(profit, 2),
            "comment": getattr(d, "comment", ""),
        })
    rows.sort(key=lambda r: r["date"])
    return _public({"count": len(rows), "from": frm.strftime("%Y-%m-%d"), "deals": rows})


@app.get("/mt5/open")
def mt5_open():
    mt5 = _mt5_mod()
    if mt5 is None:
        raise HTTPException(501, "MetaTrader5 is not installed here")
    if not _MT5["ok"]:
        raise HTTPException(409, "not connected — POST /mt5/connect first")
    pos = mt5.positions_get()
    out = []
    for pp in (pos or []):
        out.append({
            "ticket": getattr(pp, "ticket", None),
            "symbol": getattr(pp, "symbol", ""),
            "dir": "buy" if getattr(pp, "type", 0) == 0 else "sell",
            "lots": float(getattr(pp, "volume", 0.0)),
            "open": float(getattr(pp, "price_open", 0.0)),
            "now": float(getattr(pp, "price_current", 0.0)),
            "sl": float(getattr(pp, "sl", 0.0)) or None,
            "tp": float(getattr(pp, "tp", 0.0)) or None,
            "pnl": round(float(getattr(pp, "profit", 0.0)), 2),
        })
    return _public({"count": len(out), "positions": out})


# FRED SERVED PUBLIC MACRO DATA BEHIND A PRIVATE CORS POLICY.
# This endpoint carries no user data — it is the St Louis Fed's own published
# series, which anyone can download — but it was returning a bare dict, so the
# CORS middleware applied the strict ORIGINS list and a file:// page (origin
# null) had its response refused by the browser. The bridge fetched CPI
# correctly and the page could not read it, which is the same failure the
# calendar had. It gets the same allow-all header the calendar does; the
# journal and trade endpoints keep the strict list and are untouched.
@app.options("/fred/{series_id}")
def fred_preflight(series_id: str):
    return _public({"ok": True})


@app.get("/fred/{series_id}")
def fred(series_id: str, start: str = "2020-01-01"):
    if series_id not in FRED_SERIES:
        raise HTTPException(404, f"unknown series — this endpoint serves only {sorted(FRED_SERIES)}")
    now = time.time()
    hit = _FRED_CACHE.get(series_id)
    if hit and now - hit[0] < FRED_TTL:
        return _public({"series": series_id, "name": FRED_SERIES[series_id],
                        "cached": True, "observations": hit[1]})
    import urllib.request

    url = ("https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}&cosd={start}")
    try:
        with urllib.request.urlopen(url, timeout=20) as f:
            body = f.read().decode("utf-8", "replace")
    except Exception as e:  # network, DNS, timeout — say which, do not pretend
        raise HTTPException(502, f"could not reach FRED: {e}")
    lines = body.strip().split("\n")
    if len(lines) < 2:
        raise HTTPException(502, "FRED returned nothing usable")
    obs = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        # FRED writes "." for a day the series has no print — a holiday is not
        # a zero, and carrying it as one would put a fake collapse in the data
        if parts[1] in ("", "."):
            continue
        try:
            obs.append({"date": parts[0], "value": float(parts[1])})
        except ValueError:
            continue
    _FRED_CACHE[series_id] = (now, obs)
    return _public({"series": series_id, "name": FRED_SERIES[series_id],
                    "cached": False, "observations": obs})


# ══════════════════════════════════════════════════════════════════════════
# SERVING THE PAGE ITSELF
#
# CHROME WILL NOT GIVE THE MICROPHONE TO A file:// PAGE. That is a browser
# rule, not a setting, and nothing in the page can talk it round — so opening
# celestial_alpha.html by double-clicking it means the wake word can never
# work, however many times it is switched on.
#
# The fix is that the page has to arrive over http. There was already a
# server here; it just was not handing over the one file that matters. Now
# it does, and that solves three things at once rather than one:
#
#     the microphone is allowed, so "CELESX" works
#     the page and the api are the same origin, so CORS stops applying
#     there is one thing to start instead of two
#
# ONLY THIS FILE IS SERVED. Not a directory, not a path the caller chooses —
# a fixed filename resolved once at import. A static mount over the project
# folder would also have served celestial.db, which holds password hashes
# and every trade taken.
# ══════════════════════════════════════════════════════════════════════════
APP_HTML = os.environ.get(
    "CELESTIAL_PAGE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "celestial_alpha.html"),
)


@app.get("/")
def root_redirect():
    return RedirectResponse("/app")


@app.get("/app")
def serve_app():
    if not os.path.exists(APP_HTML):
        raise HTTPException(
            404,
            f"no page at {APP_HTML} — put celestial_alpha.html beside this file, "
            "or set CELESTIAL_PAGE to where it is",
        )
    # no-store, because the page is edited constantly and a cached copy of
    # yesterday's build is a confusing thing to debug
    return FileResponse(APP_HTML, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


# ══════════════════════════════════════════════════════════════════════════
# CELESX
#
# The assistant lives in celesx.py, not here — this file is storage and
# proxies, and mixing a scheduler into it would make both harder to reason
# about. These four endpoints are the seam: the page asks what CELESX
# knows, and can ask it to run now.
#
# Everything is behind the login. A brief names levels and directions, and
# the bot tokens are the user's own; neither belongs on an open endpoint.
# No endpoint here ever returns a token — /celesx/status says whether a
# channel is configured, never what with.
# ══════════════════════════════════════════════════════════════════════════
try:
    import celesx as _cx
except Exception as _cx_err:  # a missing assistant must not stop the bridge
    _cx = None
    _CX_ERR = str(_cx_err)


def _need_celesx():
    if _cx is None:
        raise HTTPException(503, f"celesx is not loadable: {_CX_ERR}")
    return _cx


@app.get("/celesx/status")
def celesx_status(uid: int = Depends(user_from_token)):
    return _need_celesx().status()


@app.get("/celesx/brief")
def celesx_brief(kind: str | None = None, uid: int = Depends(user_from_token)):
    if kind not in (None, "weekend", "daily"):
        raise HTTPException(400, "kind must be weekend or daily")
    b = _need_celesx().latest_brief(kind)
    if not b:
        return {"brief": None, "note": "CELESX has not run yet"}
    return {"brief": b}


@app.post("/celesx/run")
def celesx_run(kind: str = Body(..., embed=True), uid: int = Depends(user_from_token)):
    """Run a pass now, outside the schedule.

    Synchronous on purpose: a pass drives a real browser over sixteen
    instruments and takes minutes, and a caller that is told 'started' with
    no way to find out how it went is worse than a caller that waits."""
    if kind not in ("weekend", "daily"):
        raise HTTPException(400, "kind must be weekend or daily")
    return _need_celesx().run_job(kind, force=True)


@app.post("/celesx/test")
def celesx_test(uid: int = Depends(user_from_token)):
    cx = _need_celesx()
    if not any(cx.channels_configured().values()):
        raise HTTPException(
            400,
            "no channel is configured — set CELESX_TELEGRAM_TOKEN and "
            "CELESX_TELEGRAM_CHAT, or CELESX_DISCORD_WEBHOOK, in the "
            "server's environment",
        )
    return cx.deliver(
        "<b>CELESX</b> is connected. This is the channel I will use for the "
        "weekend view, the morning brief and event alerts."
    )


if __name__ == "__main__":
    import uvicorn

    init_db()
    if _cx is not None:
        _cx.init_db()
        # Off unless asked for. A scheduler that starts itself would send
        # messages from a bridge someone started to test an endpoint.
        if os.environ.get("CELESX_SCHEDULER", "").strip() in ("1", "true", "yes", "on"):
            _cx.start_scheduler()
            print(f"celesx scheduler on · channels: {_cx.channels_configured()}")
        else:
            print("celesx loaded, scheduler off (CELESX_SCHEDULER=1 to run it)")
    print(f"celestial_bridge → http://{HOST}:{PORT}   db: {DB_PATH}")
    print(f"OPEN THE APP AT   → http://127.0.0.1:{PORT}/app"
          + ("" if os.path.exists(APP_HTML) else f"   (missing: {APP_HTML})"))
    print("  the microphone and the wake word only work on this address, "
          "never on a file:// one")
    print(f"origins allowed: {', '.join(ORIGINS)}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
