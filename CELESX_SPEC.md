# CELESX — spec and decisions

Not built yet. This file is the memory: what CELESX is meant to be, what has
been decided, and what is still open. Nothing here has been added to
`celestial_alpha.html`.

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

## Still open

- Which assets does the weekend scan cover — all sixteen, or only the ones
  the user watches?
- Does "two or three agree" mean any two of {price, time, macro}, or is
  price required in every selection?
- Does CELESX speak out loud, or is the voice-assistant manner about how it
  writes?
- What does it send when a weekend finds nothing worth trading — silence,
  or a message saying so?
