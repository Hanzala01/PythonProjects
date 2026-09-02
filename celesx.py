#!/usr/bin/env python3
"""
celesx — the assistant half of Celestial Alpha.

The board waits to be opened. CELESX does not. It runs on a server, works to
its own schedule, forms a view, and comes to you with it.

    Saturday and Sunday   read all sixteen instruments, form a view of the
                          week, and say which days suit which asset
    Monday to Friday      before the session, say today's times, today's
                          levels, and where the two meet
    Any day               a heavy aspect, a number, a headline — alert, then
                          a reminder on the day

THE RULE THAT OVERRIDES EVERYTHING ELSE

    Never astrology alone. Every selection is price, time and macro read
    together. Astrology is one of three votes and can never be the whole
    case, which is why the selector below counts votes instead of scoring
    a single layer.

WHY THE ANALYSIS IS NOT WRITTEN IN THIS FILE

    Every engine CELESX needs already exists in celestial_alpha.html, and
    those engines are forward-tested — the timing weights came out of a
    null test, not out of a guess. Rewriting them in Python would create a
    second implementation that drifts from the first, and the day the two
    disagree there would be no way to know which one was right.

    So CELESX drives the page instead. It opens the real file in a headless
    browser, loads each asset through the page's own SCAN_QUIET path, and
    calls the page's own functions. One implementation, one source of truth,
    and an engine fix reaches CELESX the moment the page is saved.

CREDENTIALS

    Bot tokens are read from the environment and never from the page. They
    are not stored in the database, not returned by any endpoint, and not
    written to the log — /celesx/status reports whether a channel is
    configured, never what it is configured with.

        CELESX_TELEGRAM_TOKEN     from @BotFather
        CELESX_TELEGRAM_CHAT      your chat id
        CELESX_DISCORD_WEBHOOK    a channel webhook url
        CELESX_PAGE               path to celestial_alpha.html
        CELESX_TZ_OFFSET          hours from UTC, default 0

    WhatsApp is deliberately absent. The official API bills per conversation
    and needs business verification; the unofficial libraries are free and
    get the number banned. Telegram and Discord cost nothing and are not a
    trap, so they come first.

RISK

    Twelve percent is the ceiling, and it holds until you change it. Past it
    CELESX stops proposing and says so: target achieved, no more trades is
    better today.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

# ── configuration ─────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CELESTIAL_DB", os.path.join(HERE, "celestial.db"))
PAGE = os.environ.get("CELESX_PAGE", os.path.join(HERE, "celestial_alpha.html"))

TELEGRAM_TOKEN = os.environ.get("CELESX_TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("CELESX_TELEGRAM_CHAT", "").strip()
DISCORD_WEBHOOK = os.environ.get("CELESX_DISCORD_WEBHOOK", "").strip()

# The user's clock, as hours from UTC. The scheduler thinks in UTC and only
# converts when it needs to answer "is it Saturday where they are".
TZ_OFFSET = float(os.environ.get("CELESX_TZ_OFFSET", "0") or 0)

# THE CEILING. Recorded here as one number so that changing it is one edit
# and so that nothing else in the file invents its own limit.
RISK_CEILING_PCT = 12.0

# All sixteen, because that is what was asked for. The names are the page's
# own — they are passed straight to loadHistory and tmgEngine.
ASSETS = [
    "Gold", "Silver", "Bitcoin", "Ethereum", "Cardano", "Solana", "Ripple",
    "Litecoin", "Dogecoin", "Apple", "Tesla", "Microsoft", "Nvidia", "Oil",
    "EURUSD", "GBPUSD",
]

# A weekend that selects everything has selected nothing. Every qualifying
# asset is still recorded and still reaches the message; this is only where
# the fold goes, so the top of the message is readable on a phone.
LEAD_COUNT = 3


def now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


# ── storage ───────────────────────────────────────────────────────────────
# CELESX writes to the same SQLite file the bridge already keeps, in its own
# tables. Two reasons: one file to back up, and the run log sits next to the
# journal it will eventually be read against.

SCHEMA = """
CREATE TABLE IF NOT EXISTS celesx_runs(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind      TEXT NOT NULL,          -- weekend | daily | event
  ran_at    REAL NOT NULL,
  day_key   TEXT NOT NULL,          -- local YYYY-MM-DD, so a run happens once
  ok        INTEGER NOT NULL,
  detail    TEXT NOT NULL DEFAULT '',
  UNIQUE(kind, day_key)
);
CREATE TABLE IF NOT EXISTS celesx_briefs(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind      TEXT NOT NULL,
  made_at   REAL NOT NULL,
  day_key   TEXT NOT NULL,
  body      TEXT NOT NULL,          -- json
  text      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS celesx_brief_day ON celesx_briefs(day_key);
CREATE TABLE IF NOT EXISTS celesx_state(
  k         TEXT PRIMARY KEY,
  v         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS celesx_sent(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  sent_at   REAL NOT NULL,
  channel   TEXT NOT NULL,
  ok        INTEGER NOT NULL,
  note      TEXT NOT NULL DEFAULT ''
);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript(SCHEMA)
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def already_ran(kind: str, day_key: str) -> bool:
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM celesx_runs WHERE kind=? AND day_key=? AND ok=1",
            (kind, day_key),
        ).fetchone()
    return bool(row)


def record_run(kind: str, day_key: str, ok: bool, detail: str = ""):
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO celesx_runs(kind,ran_at,day_key,ok,detail) "
            "VALUES(?,?,?,?,?)",
            (kind, time.time(), day_key, 1 if ok else 0, detail[:2000]),
        )


def store_brief(kind: str, day_key: str, body: dict, text: str) -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO celesx_briefs(kind,made_at,day_key,body,text) VALUES(?,?,?,?,?)",
            (kind, time.time(), day_key, json.dumps(body), text),
        )
        return int(cur.lastrowid)


def latest_brief(kind: str | None = None) -> dict | None:
    with db() as con:
        if kind:
            row = con.execute(
                "SELECT * FROM celesx_briefs WHERE kind=? ORDER BY made_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM celesx_briefs ORDER BY made_at DESC LIMIT 1"
            ).fetchone()
    if not row:
        return None
    return {
        "kind": row["kind"], "made_at": row["made_at"], "day_key": row["day_key"],
        "text": row["text"], "body": json.loads(row["body"] or "{}"),
    }


def state_get(k: str, dflt: str = "") -> str:
    with db() as con:
        row = con.execute("SELECT v FROM celesx_state WHERE k=?", (k,)).fetchone()
    return row["v"] if row else dflt


def state_set(k: str, v: str):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO celesx_state(k,v) VALUES(?,?)", (k, str(v)))


# ── delivery ──────────────────────────────────────────────────────────────

def _post(url: str, payload: dict, timeout: int = 15) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 300), f"{r.status}"
    except urllib.error.HTTPError as e:
        # The body carries the actual reason — wrong chat id, revoked webhook.
        # It is worth keeping; it is not a secret.
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        return False, f"{e.code} {body}"
    except Exception as e:  # network, DNS, timeout
        return False, str(e)[:300]


def send_telegram(text: str) -> tuple[bool, str]:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        return False, "not configured"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    return _post(url, {
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def send_discord(text: str) -> tuple[bool, str]:
    if not DISCORD_WEBHOOK:
        return False, "not configured"
    # Discord has no HTML; the briefs are written in a subset that reads the
    # same either way, so only the tags are stripped.
    plain = text.replace("<b>", "**").replace("</b>", "**")
    plain = plain.replace("<i>", "_").replace("</i>", "_")
    return _post(DISCORD_WEBHOOK, {"content": plain[:1900]})


def deliver(text: str) -> dict:
    """Send to every configured channel. A channel that is not set up is not
    a failure — it is simply not one of the ways you have asked to be
    reached."""
    out = {}
    for name, fn in (("telegram", send_telegram), ("discord", send_discord)):
        configured = (name == "telegram" and TELEGRAM_TOKEN and TELEGRAM_CHAT) or \
                     (name == "discord" and DISCORD_WEBHOOK)
        if not configured:
            out[name] = "off"
            continue
        ok, note = fn(text)
        out[name] = "sent" if ok else f"failed: {note}"
        with db() as con:
            con.execute(
                "INSERT INTO celesx_sent(sent_at,channel,ok,note) VALUES(?,?,?,?)",
                (time.time(), name, 1 if ok else 0, note[:300]),
            )
    return out


def channels_configured() -> dict:
    return {
        "telegram": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT),
        "discord": bool(DISCORD_WEBHOOK),
        "whatsapp": False,
    }


# ── reading the market, through the page's own engines ────────────────────

# This runs inside the page. It is the page's functions doing the work —
# nothing here re-implements an engine, it only collects what they return.
READER_JS = r"""
async (assets) => {
  const out = {asOf: new Date().toISOString(), assets: [], errors: []};

  /* the page built this flag for its own multi-asset scan: history lands,
     nothing is painted. A full pass is seconds instead of minutes. */
  window.SCAN_QUIET = true;

  const load = (name) => new Promise((res) => {
    let done = false;
    const finish = () => { if (!done) { done = true; res(); } };
    setTimeout(finish, 20000);          // a feed that never answers must not hang the pass
    try { loadHistory(name, finish); } catch (e) { finish(); }
  });

  /* the sky is the same for every asset, so it is read once */
  let moon = null, ahead = [], day = null;
  try { moon = qamMoon(new Date()); } catch (e) { out.errors.push('moon: ' + e.message); }
  try { ahead = qamAhead(7) || []; }  catch (e) { out.errors.push('aspects: ' + e.message); }
  try { day = (typeof qamDayCardData === 'function') ? qamDayCardData() : null; } catch (e) {}

  for (const name of assets) {
    const rec = {asset: name};
    try {
      await load(name);
      const rows = (window.HIST_ROWS && window.HIST_ROWS.length) ? window.HIST_ROWS : null;
      rec.bars = rows ? rows.length : 0;
      if (!rows || rows.length < 120) { rec.skip = 'no history'; out.assets.push(rec); continue; }

      const px = +rows[rows.length - 1].close;
      rec.price = px;

      /* PRICE — where the range is, and whether price is at an edge of it */
      try {
        const mp = majorPair(rows, px);
        /* MH and ML are objects — the level plus how it was found. Only the
           price crosses the boundary; the rest is not used here. */
        const lv = (x) => (x && typeof x === 'object') ? +x.px : +x;
        if (mp) rec.major = {
          MH: lv(mp.MH), ML: lv(mp.ML), inPlay: mp.inPlay, stable: mp.stable
        };
      } catch (e) { rec.majorErr = e.message; }

      /* TIME — the five-book score, with its own forward-test grade */
      try {
        const t = tmgEngine(name);
        if (t) rec.timing = {
          score: t.score, status: t.status, grade: t.grade, usable: t.usable,
          mayAlert: t.mayAlert, dir: t.dir, combine: t.combine
        };
      } catch (e) { rec.timingErr = e.message; }

      /* the day's own lean for this asset, one reading per calendar day.
         dayBias takes a live signal, not a price; with none to give it, it
         returns the reading already stored for today, which is what is
         wanted here — CELESX reads the day's bias, it does not set it. */
      try {
        const b = dayBias(name, null);
        if (b) rec.bias = {label: b.label, dir: b.dir, bull: b.bull, held: !!b.held};
      } catch (e) {}
    } catch (e) {
      rec.error = e.message;
    }
    out.assets.push(rec);
  }

  out.moon = moon ? {
    sign: moon.sign, deg: moon.deg, zone: moon.zone && moon.zone.label,
    speed: moon.speed, nak: moon.nak && moon.nak.name
  } : null;
  /* A Date crossing the browser boundary arrives in Python as a datetime,
     which json.dumps refuses — and the brief is stored as json. Every date
     leaves here as an ISO string so nothing downstream has to know. */
  const iso = (d) => { try { return new Date(d).toISOString(); } catch (e) { return null; } };

  out.aspects = ahead.slice(0, 20).map(x => ({
    p1: x.p1, p2: x.p2, aspect: x.aspect, at: iso(x.at), heavy: !!x.heavy,
    rank: x.profile && x.profile.rank, bias: x.profile && x.profile.bias,
    reason: x.profile && x.profile.reason
  }));
  out.day = day;
  /* last guard: anything that is still a Date after the mapping above */
  JSON.parse(JSON.stringify(out));
  window.SCAN_QUIET = false;
  return out;
}
"""


class ReaderUnavailable(RuntimeError):
    pass


async def _read_async(assets: list[str], page_path: str, headful: bool = False) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise ReaderUnavailable(
            "playwright is not installed — pip install playwright && playwright install chromium"
        ) from e
    if not os.path.exists(page_path):
        raise ReaderUnavailable(f"page not found at {page_path}")

    # A pinned Chromium, when the machine has one. Playwright expects the
    # exact build that matches its own version and refuses anything else by
    # default; on a server where the browser is provided by the image rather
    # than by pip, that mismatch is normal and this is the way past it.
    launch: dict = {"headless": not headful}
    exe = os.environ.get("CELESX_CHROMIUM", "").strip()
    if not exe:
        for cand in ("/opt/pw-browsers/chromium/chrome-linux/chrome",
                     "/opt/pw-browsers/chromium",
                     "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     "/usr/bin/google-chrome"):
            if os.path.isfile(cand):
                exe = cand
                break
    if exe:
        launch["executable_path"] = exe

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch)
        try:
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            await page.goto("file://" + os.path.abspath(page_path))
            # the page boots its engines and its ephemeris before anything is
            # callable; this is the settle, not a guess at network latency
            await page.wait_for_function(
                "typeof tmgEngine==='function' && typeof loadHistory==='function'",
                timeout=60000,
            )
            data = await page.evaluate(READER_JS, assets)
            if errs:
                data.setdefault("errors", []).extend(errs[:10])
            return data
        finally:
            await browser.close()


def read_market(assets: list[str] | None = None, page_path: str | None = None) -> dict:
    """Drive the real page and come back with what its engines said.

    Raises ReaderUnavailable when the browser or the page is missing, which
    is deliberately not the same as an empty reading: 'I could not look' and
    'I looked and there is nothing' are different messages to send."""
    return asyncio.run(_read_async(assets or ASSETS, page_path or PAGE))


# ── the selector ──────────────────────────────────────────────────────────

def votes_for(rec: dict, macro_bias: str | None) -> dict:
    """Three independent votes on one asset: price, time, macro.

    Each returns bull, bear or none. They are counted, never averaged — the
    whole point of the rule is agreement between different kinds of evidence,
    and an average would let one strong layer carry a selection on its own."""
    v = {"price": None, "time": None, "macro": None, "why": {}}

    mj = rec.get("major") or {}
    px = rec.get("price")

    # majorPair returns MH and ML as objects — {px, date, atrAway, …} — not
    # as numbers. `MH > ML` on two dicts raises TypeError in Python, so this
    # would have taken down the whole pass the first time a real feed put a
    # range in front of it. The reader unwraps them now; this stays tolerant
    # of both shapes so an older stored reading still selects.
    def _px(x):
        if isinstance(x, dict):
            x = x.get("px")
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    MH, ML = _px(mj.get("MH")), _px(mj.get("ML"))
    if not (px and MH and ML and MH > ML):
        # A vote that abstains still has to say why. Leaving `why` unset here
        # printed an empty line in the reply, which reads as a bug rather
        # than as "there is nothing to measure".
        v["why"]["price"] = ("no range measured — not enough history"
                             if not (MH and ML) else "no price to place in the range")
    if px and MH and ML and MH > ML:
        span = MH - ML
        pos = (px - ML) / span if span else 0.5
        # An edge is where a range decides something. The middle is where it
        # decides nothing, so the middle abstains rather than guessing.
        if pos <= 0.25:
            v["price"] = "bull"
            v["why"]["price"] = f"at the low edge of {ML:g}–{MH:g}"
        elif pos >= 0.75:
            v["price"] = "bear"
            v["why"]["price"] = f"at the high edge of {ML:g}–{MH:g}"
        else:
            v["why"]["price"] = f"mid-range in {ML:g}–{MH:g} — no edge"

    t = rec.get("timing") or {}
    # A layer that failed its own forward test is not evidence. mayAlert is
    # the page's own gate for exactly that and it is honoured here rather
    # than re-derived.
    if t.get("mayAlert") and (t.get("score") or 0) >= 50:
        d = (t.get("dir") or "").lower()
        v["time"] = "bull" if d in ("up", "bull", "bullish") else \
                    "bear" if d in ("down", "bear", "bearish") else None
        v["why"]["time"] = f"timing {t.get('score')}/100, {t.get('grade')}"
    elif t and not t.get("mayAlert"):
        v["why"]["time"] = "the timing layers failed their own forward test here — no vote"
    elif t:
        v["why"]["time"] = f"timing {t.get('score')}/100 — below the alert line"
    else:
        v["why"]["time"] = "timing could not be read — no daily history loaded"

    if macro_bias in ("bull", "bear"):
        v["macro"] = macro_bias
        v["why"]["macro"] = f"sky leans {macro_bias} this week"
    else:
        v["why"]["macro"] = "no net lean in the sky — no vote"

    return v


def select(rec: dict, macro_bias: str | None) -> dict | None:
    """Any two of the three, agreeing on a direction.

    Price is NOT required — that was decided explicitly. It means a setup can
    be selected on time and macro with no level behind it, so when that
    happens the note says so out loud rather than letting it pass as a
    complete case."""
    v = votes_for(rec, macro_bias)
    cast = [k for k in ("price", "time", "macro") if v[k]]
    if len(cast) < 2:
        return None
    bulls = [k for k in cast if v[k] == "bull"]
    bears = [k for k in cast if v[k] == "bear"]
    side, agree = ("bull", bulls) if len(bulls) >= len(bears) else ("bear", bears)
    if len(agree) < 2:
        return None  # two votes that disagree are not agreement
    return {
        "asset": rec.get("asset"),
        "side": side,
        "agree": agree,
        "count": len(agree),
        "price": rec.get("price"),
        "why": [v["why"].get(k, "") for k in agree],
        "no_level": "price" not in agree,
        "timing": (rec.get("timing") or {}).get("score"),
    }


def macro_lean(data: dict) -> tuple[str | None, str]:
    """One reading of the week's sky, shared by every asset.

    Heavy aspects only — the ones the page already marks as worth an alert.
    Everything else is noise at this altitude."""
    heavy = [a for a in data.get("aspects", []) if a.get("heavy")]
    if not heavy:
        return None, "no heavy aspect in the window"
    bull = sum(1 for a in heavy if (a.get("bias") or "") == "bullish")
    bear = sum(1 for a in heavy if (a.get("bias") or "") == "bearish")
    if bull > bear:
        return "bull", f"{len(heavy)} heavy, leaning bullish"
    if bear > bull:
        return "bear", f"{len(heavy)} heavy, leaning bearish"
    return None, f"{len(heavy)} heavy, no net lean"


# ── the briefs ────────────────────────────────────────────────────────────

def weekend_brief(data: dict) -> tuple[dict, str]:
    lean, lean_note = macro_lean(data)
    picks, skipped = [], []
    for rec in data.get("assets", []):
        s = select(rec, lean)
        (picks if s else skipped).append(s or rec.get("asset"))
    picks.sort(key=lambda p: (-p["count"], -(p["timing"] or 0)))

    body = {
        "kind": "weekend", "asOf": data.get("asOf"), "macro": lean,
        "macroNote": lean_note, "picks": picks, "scanned": len(data.get("assets", [])),
        "moon": data.get("moon"),
    }

    when = now_local().strftime("%d %b")
    n = len(data.get("assets", []))
    head = f"<b>CELESX — the week ahead</b>\n{when} · {n} instrument{'' if n == 1 else 's'} read\n"
    head += f"Sky: {lean_note}.\n"

    if not picks:
        # Silence and a crashed bot look identical, so a quiet week says so.
        text = (head + "\nNothing lines up next week. No asset had two of "
                "price, time and macro agreeing, so there is no swing worth "
                "taking. I will keep watching for intraday and message you "
                "if that changes.")
        return body, text

    lines = [head]
    for i, p in enumerate(picks):
        if i == LEAD_COUNT:
            lines.append(f"\n<i>— and {len(picks) - LEAD_COUNT} weaker, below —</i>")
        side = "BUY side" if p["side"] == "bull" else "SELL side"
        mark = "★" * p["count"]
        lines.append(f"\n{mark} <b>{p['asset']}</b> — {side}")
        for w in p["why"]:
            if w:
                lines.append(f"   · {w}")
        if p["no_level"]:
            lines.append("   · no price level behind this one — time and macro only")

    lines.append(f"\nCeiling stays {RISK_CEILING_PCT:g}%. Quality over quantity — "
                 f"the top {min(LEAD_COUNT, len(picks))} are the ones I would take.")
    return body, "\n".join(lines)


def daily_brief(data: dict) -> tuple[dict, str]:
    lean, lean_note = macro_lean(data)
    picks = [s for s in (select(r, lean) for r in data.get("assets", [])) if s]
    picks.sort(key=lambda p: (-p["count"], -(p["timing"] or 0)))

    today = [a for a in data.get("aspects", []) if a.get("heavy")][:3]
    moon = data.get("moon") or {}

    body = {"kind": "daily", "asOf": data.get("asOf"), "macro": lean,
            "picks": picks, "heavyToday": today, "moon": moon}

    when = now_local().strftime("%A %d %b")
    out = [f"<b>CELESX — {when}</b>"]
    if moon.get("sign"):
        out.append(f"Moon in {moon['sign']} at {moon.get('deg', 0):.1f}° · "
                   f"{moon.get('zone') or '—'} · {moon.get('speed') or '—'}")
    if today:
        for a in today:
            out.append(f"⚠ <b>{a['p1']} {a['aspect']} {a['p2']}</b> — {a.get('reason') or 'heavy'}")

    if not picks:
        out.append("\nNothing has two of price, time and macro agreeing today. "
                   "No setup from me — that is a reading, not a gap.")
        return body, "\n".join(out)

    out.append("\n<b>Where they meet today</b>")
    for p in picks[:LEAD_COUNT]:
        side = "BUY side" if p["side"] == "bull" else "SELL side"
        out.append(f"\n<b>{p['asset']}</b> — {side} ({p['count']} of 3 agree)")
        for w in p["why"]:
            if w:
                out.append(f"   · {w}")
        if p["no_level"]:
            out.append("   · no level behind it — time and macro only")
    if len(picks) > LEAD_COUNT:
        out.append(f"\n<i>{len(picks) - LEAD_COUNT} more, weaker.</i>")
    out.append(f"\nCeiling {RISK_CEILING_PCT:g}%.")
    return body, "\n".join(out)


def run_job(kind: str, force: bool = False) -> dict:
    """One pass: read, decide, store, send. The only function the scheduler
    and the endpoints both call, so there is one path and not two."""
    day_key = now_local().strftime("%Y-%m-%d")
    if not force and already_ran(kind, day_key):
        return {"ok": True, "skipped": "already ran today", "day": day_key}

    try:
        data = read_market()
    except ReaderUnavailable as e:
        record_run(kind, day_key, False, str(e))
        return {"ok": False, "error": str(e), "day": day_key}

    body, text = (weekend_brief if kind == "weekend" else daily_brief)(data)
    brief_id = store_brief(kind, day_key, body, text)
    # "quiet" has to stop the push, not just the reply, or it is a lie. The
    # brief is still made and stored, so asking for it later still works.
    if not force and muted_today():
        record_run(kind, day_key, True, "muted")
        return {"ok": True, "day": day_key, "brief": brief_id, "muted": True,
                "picks": len(body.get("picks", []))}
    sent = deliver(text)
    record_run(kind, day_key, True, json.dumps(sent))
    return {"ok": True, "day": day_key, "brief": brief_id,
            "picks": len(body.get("picks", [])), "sent": sent}



# ── being talked to ───────────────────────────────────────────────────────
#
# THIS IS THE HALF THAT MAKES IT AN ASSISTANT RATHER THAN A MAILING LIST.
#
# Everything above pushes: the scheduler decides when, CELESX writes, you
# read. That is useful and it is not the thing that was asked for. What was
# asked for is that you say "good morning" and it hands over its work —
# which means it has to be listening.
#
# Telegram gives that away free. A bot can long-poll getUpdates, so there is
# no webhook, no public address, no TLS certificate and no hosting bill for
# the inbound half. Discord cannot do the same without a gateway connection
# and a library, so replies are Telegram-only for now and Discord stays a
# delivery channel.
#
# WHO IS ALLOWED TO TALK TO IT
#
#     Only CELESX_TELEGRAM_CHAT. A bot username is discoverable — anyone can
#     find it and start typing — and the answers name levels, sizes and how
#     much of the ceiling is spent. Every message from any other chat is
#     dropped, counted, and never answered. It does not even reply "no",
#     because that confirms the bot is live.
#
# WHAT IT UNDERSTANDS
#
#     Deliberately not a language model. Intents are keyword matches, which
#     means the same words always do the same thing and a wrong answer is a
#     bug I can find rather than a sampling temperature. Anything it does
#     not recognise falls through to the day's work, because that is what
#     was asked for: say anything, and it submits.

INTENTS = [
    ("week",   ("week", "weekend", "hafta", "swing", "next week")),
    ("status", ("status", "alive", "working", "kaam", "health")),
    ("quiet",  ("quiet", "stop", "mute", "band", "chup", "enough")),
    ("wake",   ("wake", "resume", "start", "chalu", "unmute")),
    ("help",   ("help", "what can you", "commands", "kya kar")),
]


def _intent(text: str) -> tuple[str, str | None]:
    """Return (intent, asset). Unrecognised is 'today', on purpose."""
    t = (text or "").strip().lower()

    # an asset name anywhere in the message wins over everything else — if
    # you type "gold" you want gold, not a lecture about the week
    for a in ASSETS:
        if a.lower() in t:
            return "asset", a
    for alias, real in (("xau", "Gold"), ("xag", "Silver"), ("btc", "Bitcoin"),
                        ("eth", "Ethereum"), ("eur", "EURUSD"), ("gbp", "GBPUSD"),
                        ("crude", "Oil"), ("wti", "Oil")):
        if alias in t:
            return "asset", real

    for name, words in INTENTS:
        if any(w in t for w in words):
            return name, None
    return "today", None


def _fresh(kind: str) -> dict | None:
    """Today's brief if it exists, otherwise nothing. A brief from Tuesday
    is not an answer to a question asked on Thursday."""
    b = latest_brief(kind)
    if not b:
        return None
    return b if b.get("day_key") == now_local().strftime("%Y-%m-%d") else None


def answer(text: str) -> str:
    """What CELESX says back. Pure — it reads state and returns a string, so
    it can be tested without a bot token or a network."""
    intent, asset = _intent(text)

    if intent == "quiet":
        state_set("muted_until", now_local().strftime("%Y-%m-%d"))
        return ("Quiet for the rest of today. I will not push anything until "
                "tomorrow — say <b>wake</b> if you want me back sooner.")

    if intent == "wake":
        state_set("muted_until", "")
        return "Back on. I will send the morning brief and alert on anything heavy."

    if intent == "help":
        return ("<b>CELESX</b>\n"
                "Say anything and I hand over today's work.\n\n"
                "· an instrument name — <i>gold, btc, eurusd</i> — that one's read\n"
                "· <b>week</b> — the weekend view\n"
                "· <b>status</b> — what I know and when I last ran\n"
                "· <b>quiet</b> — nothing more today · <b>wake</b> — back on")

    if intent == "status":
        st = status()
        runs = st.get("recent_runs") or []
        last = runs[0] if runs else None
        muted = state_get("muted_until") == now_local().strftime("%Y-%m-%d")
        return ("<b>CELESX</b>\n"
                f"Local time {st['local_now']}\n"
                f"Watching {st['assets']} instruments · ceiling {st['risk_ceiling_pct']:g}%\n"
                f"Rule: {st['rule']}\n"
                f"Last run: {(last['kind'] + ' on ' + last['day_key']) if last else 'none yet'}\n"
                f"Scheduler: {'on' if st['scheduler_alive'] else 'off'}"
                + ("\n<i>Muted for today.</i>" if muted else ""))

    if intent == "week":
        b = latest_brief("weekend")
        if not b:
            return ("I have not run a weekend pass yet. It runs Saturday and "
                    "Sunday morning — ask me then, or say <b>today</b> for "
                    "what I have now.")
        age = "" if b["day_key"] == now_local().strftime("%Y-%m-%d") \
              else f"\n\n<i>This is from {b['day_key']}.</i>"
        return b["text"] + age

    if intent == "asset":
        return _asset_answer(asset)

    # anything else — the day's work, which is the whole point
    b = _fresh("daily")
    if b:
        return b["text"]
    return ("I have not read today yet. Give me a few minutes — reading "
            "sixteen instruments takes a while — and I will send it through.")


def _asset_answer(name: str) -> str:
    """One instrument, read now. A single asset is one page load, so this is
    seconds rather than the minutes a full pass takes, and it is worth doing
    live instead of quoting a brief from this morning."""
    try:
        data = read_market(assets=[name])
    except ReaderUnavailable as e:
        return f"I cannot look right now — {e}"

    rec = (data.get("assets") or [{}])[0]
    lean, lean_note = macro_lean(data)
    v = votes_for(rec, lean)
    sel = select(rec, lean)

    out = [f"<b>{name}</b>"]
    if rec.get("price"):
        out.append(f"Last {rec['price']:g} · {rec.get('bars', 0)} daily bars")
    elif rec.get("skip"):
        out.append(f"<i>No history loaded — {rec['skip']}. Nothing to measure.</i>")

    out.append("")
    for k in ("price", "time", "macro"):
        mark = "●" if v[k] else "○"
        side = f" — {'buy' if v[k] == 'bull' else 'sell'}" if v[k] else ""
        out.append(f"{mark} <b>{k}</b>{side}\n   <i>{v['why'].get(k, '')}</i>")

    out.append("")
    if sel:
        out.append(f"<b>{sel['count']} of 3 agree — {'BUY' if sel['side'] == 'bull' else 'SELL'} side.</b>")
        if sel["no_level"]:
            out.append("No price level behind it though — time and macro only.")
    else:
        cast = sum(1 for k in ("price", "time", "macro") if v[k])
        if cast < 2:
            out.append(f"<b>Not a setup.</b> Only {cast} of the three vote"
                       f"{'s' if cast == 1 else ''} at all, and two have to agree "
                       "before I will call anything. That is a reading, not a gap.")
        else:
            out.append("<b>Not a setup.</b> The votes are split — they point in "
                       "different directions, which is not agreement. That is a "
                       "reading, not a gap.")
    return "\n".join(out)


# ── the listener ──────────────────────────────────────────────────────────

def _get_updates(offset: int, timeout: int = 50):
    url = (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
           f"?timeout={timeout}&offset={offset}&allowed_updates=%5B%22message%22%5D")
    try:
        with urllib.request.urlopen(url, timeout=timeout + 15) as r:
            return json.loads(r.read()).get("result") or []
    except Exception:
        # a dropped long-poll is normal, not an error worth shouting about
        return []


class Listener(threading.Thread):
    """Long-polls Telegram and answers.

    The offset lives in the database rather than in memory: Telegram replays
    every unacknowledged update to a reconnecting bot, so a listener that
    kept the offset in a variable would answer the same "good morning" again
    on every restart."""

    daemon = True

    def __init__(self):
        super().__init__(name="celesx-listener")
        self._stop = threading.Event()
        self.answered = 0
        self.rejected = 0
        self.last_at = 0.0

    def stop(self):
        self._stop.set()

    def run(self):
        init_db()
        if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
            return
        try:
            offset = int(state_get("tg_offset", "0") or 0)
        except ValueError:
            offset = 0

        while not self._stop.is_set():
            ups = _get_updates(offset)
            for u in ups:
                offset = max(offset, int(u.get("update_id", 0)) + 1)
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text") or ""
                if not text:
                    continue
                if chat != str(TELEGRAM_CHAT):
                    # Not yours. Do not answer, do not acknowledge — a reply
                    # of any kind confirms the bot is live and listening.
                    self.rejected += 1
                    continue
                try:
                    send_telegram(answer(text))
                    self.answered += 1
                    self.last_at = time.time()
                except Exception as e:
                    send_telegram(f"Something broke answering that: {str(e)[:200]}")
            if ups:
                state_set("tg_offset", offset)


_LISTEN: "Listener | None" = None


def start_listener() -> "Listener | None":
    global _LISTEN
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        return None
    if _LISTEN is None or not _LISTEN.is_alive():
        _LISTEN = Listener()
        _LISTEN.start()
    return _LISTEN


def muted_today() -> bool:
    return state_get("muted_until") == now_local().strftime("%Y-%m-%d")


# ── the scheduler ─────────────────────────────────────────────────────────

class Scheduler(threading.Thread):
    """A minute tick, not a cron.

    It checks the local clock every sixty seconds and fires a job when the
    hour arrives and that job has not already run today. That makes it
    restart-safe: the run log, not the process, decides whether today's work
    is done, so a server that reboots at 06:59 still sends the brief."""

    daemon = True

    def __init__(self, weekend_hour: int = 10, daily_hour: int = 7):
        super().__init__(name="celesx-scheduler")
        self.weekend_hour = weekend_hour
        self.daily_hour = daily_hour
        self._stop = threading.Event()
        self.last_tick = 0.0
        self.last_result: dict | None = None

    def stop(self):
        self._stop.set()

    def due(self, t: datetime) -> str | None:
        # Saturday is 5, Sunday 6. The weekend job runs on both: Saturday
        # forms the view, Sunday confirms it against a day more of sky.
        if t.weekday() >= 5:
            return "weekend" if t.hour >= self.weekend_hour else None
        return "daily" if t.hour >= self.daily_hour else None

    def run(self):
        init_db()
        while not self._stop.wait(60):
            self.last_tick = time.time()
            try:
                t = now_local()
                kind = self.due(t)
                if not kind:
                    continue
                if already_ran(kind, t.strftime("%Y-%m-%d")):
                    continue
                self.last_result = run_job(kind)
            except Exception as e:  # a scheduler that dies is worse than one that logs
                self.last_result = {"ok": False, "error": str(e)[:300]}


_SCHED: Scheduler | None = None


def start_scheduler() -> Scheduler:
    global _SCHED
    if _SCHED is None or not _SCHED.is_alive():
        _SCHED = Scheduler()
        _SCHED.start()
    return _SCHED


def status() -> dict:
    init_db()
    with db() as con:
        runs = [dict(r) for r in con.execute(
            "SELECT kind,ran_at,day_key,ok FROM celesx_runs ORDER BY ran_at DESC LIMIT 10"
        )]
    return {
        "channels": channels_configured(),
        "page": PAGE,
        "page_found": os.path.exists(PAGE),
        "assets": len(ASSETS),
        "risk_ceiling_pct": RISK_CEILING_PCT,
        "rule": "any two of price, time and macro, agreeing on a direction",
        "tz_offset": TZ_OFFSET,
        "local_now": now_local().isoformat(timespec="seconds"),
        "scheduler_alive": bool(_SCHED and _SCHED.is_alive()),
        "listener_alive": bool(_LISTEN and _LISTEN.is_alive()),
        "answered": _LISTEN.answered if _LISTEN else 0,
        "rejected": _LISTEN.rejected if _LISTEN else 0,
        "muted_today": muted_today(),
        "last_result": _SCHED.last_result if _SCHED else None,
        "recent_runs": runs,
    }


# ── command line ──────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="CELESX — the assistant half of Celestial Alpha")
    ap.add_argument("--test-notify", action="store_true",
                    help="send one message to every configured channel and report")
    ap.add_argument("--run", choices=["weekend", "daily"],
                    help="run a job now, regardless of the schedule")
    ap.add_argument("--dry", action="store_true",
                    help="with --run: print the brief instead of sending it")
    ap.add_argument("--read", action="store_true",
                    help="drive the page and dump the raw reading as json")
    ap.add_argument("--say", metavar="TEXT",
                    help="what would CELESX reply to this? no token needed")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--serve", action="store_true",
                    help="run the scheduler in the foreground")
    a = ap.parse_args()
    init_db()

    if a.say is not None:
        print(answer(a.say)); return

    if a.status:
        print(json.dumps(status(), indent=2)); return

    if a.test_notify:
        cfg = channels_configured()
        if not any(cfg.values()):
            print("No channel is configured. Set CELESX_TELEGRAM_TOKEN and "
                  "CELESX_TELEGRAM_CHAT, or CELESX_DISCORD_WEBHOOK.")
            return
        print(json.dumps(deliver(
            "<b>CELESX</b> is connected. This is the channel I will use for "
            "the weekend view, the morning brief and event alerts."), indent=2))
        return

    if a.read:
        try:
            print(json.dumps(read_market(), indent=2)[:20000])
        except ReaderUnavailable as e:
            print(f"cannot read: {e}")
        return

    if a.run:
        if a.dry:
            try:
                data = read_market()
            except ReaderUnavailable as e:
                print(f"cannot read: {e}"); return
            body, text = (weekend_brief if a.run == "weekend" else daily_brief)(data)
            print(text)
            print("\n--- body ---")
            print(json.dumps(body, indent=2)[:8000])
        else:
            print(json.dumps(run_job(a.run, force=True), indent=2))
        return

    if a.serve:
        s = start_scheduler()
        l = start_listener()
        print(f"CELESX running. Local time {now_local():%Y-%m-%d %H:%M}. "
              f"Channels: {channels_configured()}. "
              + ("Listening on Telegram." if l else
                 "Not listening — set CELESX_TELEGRAM_TOKEN and CELESX_TELEGRAM_CHAT for two-way.")
              + " Ctrl-C to stop.")
        try:
            while s.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            s.stop()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
