# Jarvis — Setup

A local, voice-activated personal assistant: say "Hey Jarvis" to wake it, talk to it in the browser
tab that pops up (or gets refocused if already open), and it can search the web, browse pages, look
at your screen, launch apps, and check the weather/time — powered by Gemini's free API tier (no
usage-based billing).

## 1. Prerequisites

- Python 3.10+
- Google Chrome (used for the UI's speech recognition and browser automation)
- A free [Gemini API key](https://aistudio.google.com/apikey) (sign in with a Google account, no
  card required — the free tier is permanent, not a trial: ~15 requests/min, 1,500/day)

## 2. Install

```powershell
cd Jarvis
pip install -r requirements.txt
playwright install chromium
```

## 3. Configure

```powershell
copy config.example.json config.json
```

Edit `config.json`:

| Field | What it does |
|---|---|
| `gemini_api_key` | Required. Your free Gemini API key. (Or set `GEMINI_API_KEY` in a `.env` file instead — this is what's already set up.) |
| `gemini_model` | Defaults to `gemini-3-flash-preview`. If Google renames/retires this preview model, check https://ai.google.dev/gemini-api/docs/models for the current free-tier flash model and update this. |
| `elevenlabs_api_key` / `elevenlabs_voice_id` | Optional. Enables a real, expressive JARVIS-style voice. Without these, Jarvis speaks using your browser/Windows built-in TTS instead (still works, just more robotic). |
| `user_name` | What Jarvis calls you (default `"sir"`). |
| `default_location` | City used for weather when you don't specify one. |
| `apps` | Map of app keys → launch commands/paths, used by the `launch_app` tool. Add your own (e.g. `"discord": "C:\\Users\\you\\AppData\\Local\\Discord\\Update.exe --processStart Discord.exe"`). |
| `mic_device` | Substring of the exact microphone name to listen on (e.g. `"AMD Audio Dev"`), used by `wake_word_trigger.py`/`clap_trigger.py`. Windows often defaults to the wrong device (virtual/streaming mics are common culprits) — if wake detection isn't working, check Windows Settings → Sound → Input for which device actually shows activity when you talk, and put its name here. |
| `wake_word_threshold` | Confidence (0–1) the "Hey Jarvis" detector needs to trigger. Default `0.5`. Lower if it's not triggering, raise if it's too trigger-happy. |
| `clap_threshold` | Only used by the legacy `clap_trigger.py`. Mic RMS level (0–1) that counts as a clap. |

### Getting a JARVIS-style voice (optional but recommended)

We can't clone the actual movie voice (that's a real actor's protected performance), but ElevenLabs'
voice library has plenty of "posh British butler" style voices that land the same vibe:

1. Sign up at [elevenlabs.io](https://elevenlabs.io) (free tier: ~10k characters/month).
2. In **Voice Library**, search "British" or "butler" and pick one you like — add it to your voices.
3. Copy your API key and the voice's ID into `config.json`.

Without ElevenLabs, you can still get a decent British voice for free: Windows Settings → Time &
Language → Speech → Add a voice → **English (United Kingdom)**. Jarvis will prefer it automatically.

## 4. Run it

```powershell
python server.py
```

Open **http://127.0.0.1:8420** in Chrome and allow microphone access. There's no manual "talk" button
by design — click the reactor (the glowing triangle) to manually start/stop listening, or set up wake
detection below for the real hands-free experience.

## 5. Turn on "Hey Jarvis" wake detection

In a second terminal (server must already be running):

```powershell
python wake_word_trigger.py
```

This runs fully offline (via [openWakeWord](https://github.com/dscripka/openWakeWord)'s pretrained
"hey jarvis" model — no cloud, no API key). Say **"Hey Jarvis"** near your mic — it'll find and
refocus an already-open Jarvis tab, or open a new one from scratch if none is open, then greet you
and start listening. If it's not triggering (or triggers on nothing), see the `mic_device` and
`wake_word_threshold` notes above — getting the right microphone selected is the most common issue.

A legacy double-clap trigger (`clap_trigger.py`) also still exists if you'd rather use that instead —
same wake behavior, just clap-activated instead of voice-activated.

## 6. (Optional) Auto-start at login

Use `scripts/launch-session.ps1` — it starts both `server.py` and `wake_word_trigger.py`. Register it
in Windows Task Scheduler as an "At log on" trigger, action: `powershell.exe -File
"<full path to>\scripts\launch-session.ps1"`.

## Notes

- **Costs**: $0. Gemini's free API tier has no billing attached — it's rate-limited (15 req/min,
  1,500/day), not metered. Normal personal use won't come close. ElevenLabs (if you add it) has its
  own separate free tier for TTS.
- **Privacy**: Everything routes through your own machine except the Gemini API call itself and
  (optionally) ElevenLabs for TTS. Screen captures and browser automation never leave your machine
  except as part of what you explicitly ask Jarvis to look at.
- **Extending it**: `tools.py` is the single place tool schemas + dispatch live — add a function
  there (and to whichever module makes sense) to give Jarvis a new capability. A personal knowledge
  base (RAG over your notes/Obsidian vault) is a natural next addition once the core loop feels good.
