#!/bin/bash
# ── CAELUS · give the assistant a brain, on your own Mac ──────────────────
#
# Double-click this file. It does the four things the assistant needs and
# nothing else: unblock the binary, pull a model once, start the server with
# this page allowed to talk to it, and stay open.
#
# WHY A SCRIPT AND NOT FOUR LINES IN A MESSAGE. Three of the four go wrong
# silently on a Mac. A binary downloaded through a browser is quarantined by
# Gatekeeper and dies with "cannot be opened" that names no fix. The page is
# opened as a FILE, so its origin is the literal string "null", which is not
# on Ollama's default allowlist — every call is refused before it is sent and
# nothing on screen says why; OLLAMA_ORIGINS below is the whole fix. And the
# server answers on 11434 whether or not a model has been pulled, so a first
# question fails on an empty model list rather than on anything it names.
#
# Nothing here needs a key, an account or the internet after the first pull.

set -u
cd "$(dirname "$0")" || exit 1

BIN="./ollama-aarch64-apple-darwin"
MODEL="${1:-llama3.2}"

echo "── CAELUS · local assistant ──────────────────────────────"

if [ ! -f "$BIN" ]; then
  echo "Can't find $BIN next to this script."
  echo "Put the unzipped ollama-aarch64-apple-darwin file in the SAME folder"
  echo "as this .command file, then double-click this again."
  read -r -p "Press return to close."
  exit 1
fi

chmod +x "$BIN" 2>/dev/null
# Gatekeeper's quarantine flag, set by every browser download. Without this
# the binary refuses to run and the error blames the file, not the flag.
xattr -d com.apple.quarantine "$BIN" 2>/dev/null

echo "Ollama: $("$BIN" --version 2>&1 | head -1)"

# start the server first — pull needs it up, and it is what we are here for.
# OLLAMA_ORIGINS='*' is what lets a page opened as a file read the answers.
echo "Starting the server on 127.0.0.1:11434 …"
OLLAMA_ORIGINS='*' OLLAMA_HOST='127.0.0.1:11434' "$BIN" serve &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 0.5
done

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "The server did not come up. Scroll up for its own error."
  read -r -p "Press return to close."
  exit 1
fi

# a server with no model answers 200 with an empty list, which is "running"
# and "cannot answer you" at the same time — so the model is pulled here
# rather than left to fail on the first question
if "$BIN" list 2>/dev/null | tail -n +2 | grep -q .; then
  echo "Models already installed:"
  "$BIN" list
else
  echo "No model yet — pulling $MODEL (about 2 GB, once). This takes a few minutes."
  "$BIN" pull "$MODEL" || { echo "Pull failed — check the connection and run this again."; read -r -p "Press return to close."; exit 1; }
fi

echo
echo "── Ready ─────────────────────────────────────────────────"
echo "Open celestial_alpha_b196.html and click Celestial."
echo "The panel should turn green: \"Running on your machine\"."
echo
echo "Leave THIS WINDOW OPEN while you use it. Closing it stops the model."
echo "──────────────────────────────────────────────────────────"
wait $SERVE_PID
