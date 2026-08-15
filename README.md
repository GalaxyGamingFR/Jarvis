# Jarvis

A local, voice-activated personal assistant. Say **"Hey Jarvis"**, talk to it, and it can search the
web, browse pages, look at your screen, launch apps, check the weather/time, and manage a to-do list —
all through a JARVIS-style HUD in your browser.

Runs entirely on your own machine except for the LLM call itself (Google Gemini's free API tier — no
usage-based billing) and, optionally, ElevenLabs for a richer voice.

## Features

- **"Hey Jarvis" wake word** — fully offline detection via [openWakeWord](https://github.com/dscripka/openWakeWord),
  no cloud, no account. Finds and refocuses an already-open Jarvis tab, or opens a new one from
  scratch if none is open.
- **Hands-free conversation** — wakes, greets you with the time/weather/pending tasks, listens for
  what you say, responds, and keeps listening for follow-ups until you go quiet or click to mute.
- **Real tool use** — web search & page reading, screen vision, app launching, weather, a to-do list,
  media/volume control, locking/sleeping the PC, timers & reminders, local file search, email &
  calendar, smart home control (Home Assistant), clipboard read/write, Obsidian note search, and
  multi-step macros — not just a chatbot.
- **Persistent memory** — remembers durable facts about you across restarts (preferences, people,
  projects) and works them into conversation naturally, without you needing to remind it.
- **Proactive alerts** — a timer or reminder firing speaks up on its own, even if you're not mid-
  conversation.
- **JARVIS-style HUD** — an animated reactor core with live time/weather/status readouts, no
  cluttered chat UI.
- **$0 to run** — Gemini's free API tier is rate-limited, not metered.

## Quick start

See [SETUP.md](SETUP.md) for full setup instructions (API keys, config, running it, auto-start at
login). The short version:

```powershell
pip install -r requirements.txt
playwright install chromium
copy config.example.json config.json
# add your Gemini API key to config.json or a .env file
python server.py          # in one terminal
python wake_word_trigger.py   # in another
```

Then say "Hey Jarvis" near your mic.

## Project layout

| File | What it does |
|---|---|
| `server.py` | FastAPI backend — WebSocket chat, Gemini tool-calling loop, ElevenLabs TTS |
| `wake_word_trigger.py` | Background listener for "Hey Jarvis" (openWakeWord) |
| `clap_trigger.py` | Legacy double-clap wake trigger, kept as an alternative |
| `window_focus.py` | Finds/refocuses an already-open Jarvis tab on Windows |
| `tools.py` | Tool schemas + dispatch — the place to add new capabilities |
| `browser_tools.py` | Web search & page reading (Playwright) |
| `screen_capture.py` | Screenshot → Claude/Gemini vision |
| `app_launcher.py` | Launches configured apps (Spotify, VS Code, etc.) |
| `tasks.py` | Local to-do list |
| `memory.py` | Persistent facts about the user, injected into the system prompt every turn |
| `system_control.py` | Media playback, volume, now-playing, lock/sleep (Windows) |
| `timers.py` | Timers & reminders; `get_due_items()` is polled by `server.py` to fire alerts |
| `proactive.py` | Queue Jarvis speaks unprompted — the hand-off point timers/reminders use |
| `file_search.py` | Local filesystem search & read |
| `email_calendar.py` | Email (IMAP/SMTP) and calendar (ICS feed) |
| `smart_home.py` | Home Assistant device control |
| `clipboard_tools.py` | Windows clipboard read/write |
| `obsidian_notes.py` | Search/read notes directly from an Obsidian vault on disk |
| `macros.py` | Config-driven multi-step workflows (e.g. "start my work session") |
| `frontend/` | The HUD web UI (no framework — plain HTML/CSS/JS) |
| `config.example.json` | Copy to `config.json` and fill in |

## Not cloning the actual movie voice

ElevenLabs' voice library has plenty of British-butler-style voices that land a similar vibe, but
this project doesn't attempt to clone the real JARVIS voice from the films — that's a real actor's
protected performance. See [SETUP.md](SETUP.md) for picking a voice.

## License

Personal project, no license file — ask before reusing.
