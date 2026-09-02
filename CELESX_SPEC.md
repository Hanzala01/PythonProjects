# CELESX — spec and decisions

This file is the memory: what CELESX is meant to be, and what has been
decided. The first slice is built — `celesx.py` — and the four questions
that were open are answered. Nothing has been added to
`celestial_alpha.html`; CELESX drives that page rather than living inside
it.

---

## What it is

Celestial Alpha's assistant, in the JARVIS sense: it does not wait to be
asked. It works on its own schedule, forms a view, and comes to the user
with it. It has a name — **CELESX** — and behaves like a voice assistant.

The rule that overrides everything else: **never astrology alone.** Every
call is price × time × macro combined. Astrology is one input among several,
never the whole case.

**Quality over quantity.** Few good setups, not a list.

---

## Access

**The owner is the user.** The Reference column/tab is owner-only — nobody
else sees it. (Recorded 2026-09-01. Not implemented.)

---

## The weekend job — Saturday and Sunday

Runs on its own, without the user opening anything.

1. Analyse the market with the methods already in the app.
2. Produce a view per asset: *"next week XAU is in selling / accumulation."*
3. Read market sentiment — macro events.
4. Read the Time Engine for the better windows to trade.
5. **Where two or three of those agree, select it.** Agreement is the filter.
6. Tell the user which week and which days suit which asset.
7. If the user only trades one instrument, guide inside that: *"no swing
   this week, but intraday and scalping are possible."*

Delivered by message, not left on a screen to be found.

## The daily job — Monday onward

The user should not have to hunt for trades. CELESX has already done it.

- When the user says "good morning" — or anything — CELESX submits its work.
- *"Today these are the times a trade could form."*
- Each day: the best time, the best level, and where the two meet.
- Where they line up, it alerts.

## Event days

Any big event — astrology, news, a Trump tweet, geopolitics — an alert on
the day plus a reminder.

## Risk

**12% is the ceiling.** Once the target is reached, CELESX says so and
recommends stopping: *"target achieved, no more trades is better today."*
The cap holds until the user changes it.

---

## Decided

| Question | Answer |
|---|---|
| Where does it run? | A server, 24/7. The bridge, not the browser. |
| How does it reach the user? | Telegram and Discord first — both free. WhatsApp later. |
| Name and manner | CELESX, working like a voice assistant. |
| Risk ceiling | 12%, until the user changes it. |
| Reference column | Owner only. |

**WhatsApp is deliberately last.** The official route (Meta Business API /
Twilio) charges per conversation and needs business verification; the
unofficial libraries are free but get the number banned. Telegram and
Discord are free bot tokens and cost nothing.

## Answered 2026-09-01

| Question | Answer |
|---|---|
| Which assets does the weekend scan cover? | **All sixteen.** |
| Must price be one of the agreeing votes? | **No — any two of the three.** |
| Does CELESX speak out loud? | **Yes, as well as writing.** |
| A weekend that finds nothing? | **Say so, briefly.** |

Two of those loosen the filter rather than tighten it. Sixteen assets with
price not required means a weekend can select a dozen things, which pulls
against *quality over quantity*. The build honours the answer and handles
the volume by ranking instead: every qualifying asset is recorded and
reaches the message, the strongest three lead, the rest sit below a fold.
`LEAD_COUNT` in `celesx.py` is the one number to change if that reads wrong.

Because price is optional, a selection can be made on time and macro with
no level behind it. When that happens the brief says so in the line itself —
*"no price level behind this one"* — rather than letting it pass as a
complete case.

Speaking aloud only works while the page is open, so it cannot be the
delivery mechanism; it is an addition to the written brief, not a
replacement. It also needs the ElevenLabs key for a real voice.

---

## What is built

`celesx.py` — the spine.

- **Delivery.** Telegram and Discord, tokens from the environment only.
  They are never in the page, never in the database, never in a response.
- **Schedule.** A minute tick that reads the run log, not the process, to
  decide whether today's work is done — so a reboot at 06:59 still sends
  the brief.
- **Reading.** CELESX does not re-implement a single engine. It opens the
  real `celestial_alpha.html` in a headless browser, loads each asset
  through the page's own `SCAN_QUIET` path, and calls the page's own
  `majorPair`, `tmgEngine`, `qamAhead` and `dayBias`. One implementation,
  no drift, and an engine fix reaches CELESX the moment the page is saved.
- **The selector.** Three votes — price, time, macro — counted, never
  averaged. Averaging would let one strong layer carry a selection alone,
  which is the thing the rule exists to prevent.
- **Ceiling.** 12%, as one constant.

- **Being talked to.** The half that makes it an assistant rather than a
  mailing list. A Telegram bot long-polls `getUpdates`, so there is no
  webhook, no public address and no certificate for the inbound side. Say
  anything and it hands over the day's work; name an instrument and it
  reads that one live; `week`, `status`, `quiet`, `wake`, `help` do what
  they say. Intents are keyword matches, not a language model — the same
  words always do the same thing, so a wrong answer is a bug rather than a
  sampling temperature. **Only the configured chat is answered.** A bot
  username is discoverable and the replies name levels and sizes; every
  other chat is dropped and never acknowledged, because a reply of any kind
  confirms the bot is live.

Four endpoints on the bridge, all behind the login:
`/celesx/status`, `/celesx/brief`, `/celesx/run`, `/celesx/test`.

The scheduler does not start itself. `CELESX_SCHEDULER=1` turns it on, so a
bridge started to test an endpoint does not send anybody a message.

## Not built yet

- Event-day alerts as their own path. Heavy aspects already reach the daily
  brief; a number or a headline breaking mid-session does not yet.
- Discord replies. Discord needs a gateway connection and a library for
  inbound, so it stays a delivery channel; Telegram carries the
  conversation.
- Reading the journal to know whether the 12% is already met. The ceiling
  is stated in every brief but not yet enforced against real trades.
- Speaking aloud.
- The owner-only Reference column.


---

## VOICE — PARKED, AND WHAT WAS ALREADY DONE

Parked by the owner. Not abandoned: when the word **voice** comes up again,
this is the state to resume from, and none of it needs redoing.

**Built and verified**

- `CELESX` is its own wake word and its own branch — not a synonym for
  "Celestial". "Celestial, gold" describes the chart; "CELESX, gold" gives
  the three votes and whether two agree.
- The name is matched in the forms a recogniser actually returns for an
  invented word: `celesx, celex, salex, selex, seles, sell ex, cell ex,
  sales x, celeste x, silex`.
- Six spoken intents, answered live from `cqFacts`/`cqVotes` — the same
  functions the cross-question uses: today's read, the range, the window,
  should-I-buy, the ceiling, the sky.
- Both wake paths work: one breath ("celesx should i buy") and two
  ("celesx" → "Go ahead." → the question). `JV_ARMED` carries `'cx'` so the
  follow-up reaches the right assistant.
- **Typing the name works everywhere** — `jvLocal` routes any message that
  starts with a CELESX name to the same answers. This is the path that
  works today, with no server.

**The blocker, measured rather than assumed**

Chrome will not attach a microphone permission to a `file://` origin —
there is no origin to attach one to. `getUserMedia` returns
`NotSupportedError`, and the permission cannot be granted even through the
devtools protocol. So `jvWakeSupported()`'s `!jvIsFile()` gate is correct,
not over-cautious. **Voice requires an address: `http://localhost` or
`https://`.** Nothing in the page can work around it.

**Three doors, and which are open**

| Route | Status |
|---|---|
| Public https host | **Closed by the owner** — the page carries 11 provider keys and they must not be exposed. |
| Local server | Open but fought with: the `.bat` did not run, and whether Python is installed on the machine was never established. |
| Own server (https, keys server-side) | **The chosen direction.** CELESX needs a server anyway, so it is one job rather than two. |

**Half-done toward that**

`celestial_bridge.py` can now take provider keys from its own environment
(`CELESTIAL_<NAME>_KEY`), so the page need not carry them. EODHD and GNews
are wired; nine remain. The page's own key still wins when it sends one, so
nothing that works today breaks — which is what makes removing the keys
later a quiet change rather than a flag day.

**Also delivered and superseded:** `START_CELESTIAL.bat`. Its own bugs are
fixed (server before browser, a real Python check rather than `where`, a
pause on failure), but it never ran on the owner's machine and is not the
path being pursued.
