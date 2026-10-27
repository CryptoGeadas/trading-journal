# AGENTS.md — install and start the Trading Journal

These steps are for a coding agent (Claude Code, Codex, Cursor or similar) that
installs the journal for a person who may not be technical. Follow them in
order. Explain each step to the person in plain, short words. After each step,
check the expected result before you go on.

## Hard rules (always)

1. **Never ask for API keys, secrets or passphrases in the chat, and never
   accept them if the person pastes them.** If they paste one, tell them to
   delete that key on the exchange and make a new one. Keys go only into the
   journal's own **Exchanges** page, in their browser.
2. **Keep the journal on this computer only.** Do not set `HOST` to anything
   other than `127.0.0.1` unless the person clearly asks to open the journal
   from other devices. If they do ask, tell them to also set `AUTH_USER` and
   `AUTH_PASS`.
3. **Never weaken the permission check** that refuses Binance keys which can
   withdraw or transfer, and never remove the warning that Gate.io and Kraken
   keys can't be checked.
4. Don't change the journal's code to make a step pass. If a step fails and
   the "If it fails" note doesn't help, stop and tell the person what you saw.
5. Never read, copy, print or upload the person's data folder (see "Where the
   data lives"). It holds their trades and their encrypted keys.

## Step 1 — Check Python

The journal needs **Python 3.11 or newer**.

- Windows: run `py -3 --version` (or `python --version`).
- macOS / Linux: run `python3 --version`.

Expected: `Python 3.11.x`, `3.12.x`, `3.13.x` or newer.

If it fails, or the version is older than 3.11: tell the person in plain words
that their computer needs a newer Python first, and how to get it:

- Windows: install "Python 3.12" from the Microsoft Store, or from
  python.org/downloads (tick **"Add python.exe to PATH"** in the installer).
- macOS: install from python.org/downloads, or `brew install python@3.12` if
  they use Homebrew.
- Linux: use the system package manager (e.g. `sudo apt install python3.12 python3.12-venv`).

Then run the check again. Use the command that worked (`py -3`, `python` or
`python3`) everywhere below where it says `PYTHON`.

## Step 2 — Get the code

If `git --version` works:

```
git clone https://github.com/CryptoGeadas/trading-journal.git
cd trading-journal
```

If git is missing, download https://github.com/CryptoGeadas/trading-journal/archive/refs/heads/main.zip,
unzip it, and go into the unzipped `trading-journal-main` folder.

Expected: the folder has `AGENTS.md`, `pyproject.toml` and an `app` folder.

## Step 3 — Make a virtual environment

```
PYTHON -m venv .venv
```

Expected: a new `.venv` folder, no error.

If it fails on Linux with "ensurepip is not available": install the venv
package (e.g. `sudo apt install python3.12-venv`) and run it again.

## Step 4 — Install

- Windows: `.venv\Scripts\python -m pip install .`
- macOS / Linux: `.venv/bin/python -m pip install .`

Expected: a line starting with `Successfully installed` (it can take a
minute). A notice about a newer pip version may come after it; that is fine.

## Step 5 — Start the journal

Run this from the `trading-journal` folder (it must be this folder: the page
files live here):

- Windows: `.venv\Scripts\python -m app`
- macOS / Linux: `.venv/bin/python -m app`

Expected output includes:

```
[db] Database ready at ... (v14)        <- the number may be higher
Trading Journal is running at http://127.0.0.1:8000
```

and the journal opens in the browser. This command keeps running; that is
normal. Start it in the background (or a second terminal) so you can go on.

Check it from another terminal: opening http://127.0.0.1:8000/health returns
`{"status": "ok", ...}`. (With `--no-browser` the "is running at" line is not
printed; use this check instead.)

If it fails:
- "port 8000 is already in use": the journal may already be running; open
  http://127.0.0.1:8000. Otherwise start it on another port with `PORT=8001`
  (Windows PowerShell: `$env:PORT="8001"` before the command).
- No browser opened: open http://127.0.0.1:8000 yourself and tell the person
  the address.

## Step 6 — Offer demo data

Ask the person: "Do you want to look around with made-up demo trades first?
You can remove them with one click later."

If yes: tell them to click **Load demo data** on the Overview page (or in
Settings). You may also load it for them with a POST to
http://127.0.0.1:8000/api/demo. A yellow "Demo data" banner then shows on every
page, with a **Remove demo data** button.

## Step 7 — Connecting an exchange (explain, don't do)

Explain how to make a **read-only** API key, then tell the person to paste it
only into the journal's **Exchanges → + Add Exchange** form. Never in the chat.

- **Binance:** API Management → Create API → enable **Enable Reading**; enable
  **Enable Futures** only if they trade futures (needed to read futures
  history; the journal never places orders). Leave withdrawals, internal
  transfer and universal transfer **off**: the journal refuses keys that have
  them.
- **Gate.io:** API Keys → create an API v4 key → **Spot: Read only**, nothing
  else. The journal can't check Gate.io permissions, so make sure withdrawals
  are off.
- **Kraken:** Settings → API → create a key with **Query Funds** and **Query
  Open Orders & Trades** only. The journal can't check Kraken permissions, so
  make sure **Withdraw Funds** is off.

## Starting it again later

From the `trading-journal` folder:

- Windows: `.venv\Scripts\python -m app`
- macOS / Linux: `.venv/bin/python -m app`

Tell the person this one line, and that closing the terminal stops the journal.

## Where the data lives

Trades, encrypted keys, screenshots and backups live in one folder in the
person's profile: `~/.trading-journal` (Windows:
`C:\Users\<name>\.trading-journal`), never inside the code folder. Settings →
"Data folder" shows the exact place. `TRADING_JOURNAL_DATA_DIR` moves it.

## Updating

Stop the journal, then in the folder: `git pull` (or download the ZIP again)
and repeat Step 4 and Step 5. The data folder is kept and upgraded on start.
