# Jarvis — Setup

A local, clap-activated personal assistant: double-clap to wake it, talk to it in the browser tab
that pops up, and it can search the web, browse pages, look at your screen, launch apps, and check
the weather/time — powered by Claude.

## 1. Prerequisites

- Python 3.10+
- Google Chrome (used for the UI's speech recognition and browser automation)
- An [Anthropic API key](https://console.anthropic.com/settings/keys)

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
| `anthropic_api_key` | Required. Your Claude API key. (Or set `ANTHROPIC_API_KEY` in a `.env` file instead.) |
| `anthropic_model` | Defaults to `claude-sonnet-5`. Use `claude-haiku-4-5-20251001` for a cheaper/faster assistant. |
| `elevenlabs_api_key` / `elevenlabs_voice_id` | Optional. Enables a real, expressive JARVIS-style voice. Without these, Jarvis speaks using your browser/Windows built-in TTS instead (still works, just more robotic). |
| `user_name` | What Jarvis calls you (default `"sir"`). |
| `default_location` | City used for weather when you don't specify one. |
| `apps` | Map of app keys → launch commands/paths, used by the `launch_app` tool. Add your own (e.g. `"discord": "C:\\Users\\you\\AppData\\Local\\Discord\\Update.exe --processStart Discord.exe"`). |
| `clap_threshold` | Mic RMS level (0–1) that counts as a clap. Lower = more sensitive. Tune this if wake triggers too easily or not at all. |

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

Open **http://127.0.0.1:8420** in Chrome, allow microphone access, and press **Hold to talk** to test.

## 5. Turn on clap-to-wake

In a second terminal (server must already be running):

```powershell
python clap_trigger.py
```

Double-clap near your mic — it'll open the Jarvis tab and greet you. If it's not triggering or
triggers too easily, adjust `clap_threshold` in `config.json`.

## 6. (Optional) Auto-start at login

Use `scripts/launch-session.ps1` — it starts both `server.py` and `clap_trigger.py`. Register it in
Windows Task Scheduler as an "At log on" trigger, action: `powershell.exe -File
"<full path to>\scripts\launch-session.ps1"`.

## Notes

- **Costs**: Claude API calls are billed per use (a normal reply is a fraction of a cent; tool-heavy
  turns cost more). ElevenLabs' free tier covers light daily use.
- **Privacy**: Everything routes through your own machine except the Claude API call itself and
  (optionally) ElevenLabs for TTS. Screen captures and browser automation never leave your machine
  except as part of what you explicitly ask Jarvis to look at.
- **Extending it**: `tools.py` is the single place tool schemas + dispatch live — add a function
  there (and to whichever module makes sense) to give Jarvis a new capability. A personal knowledge
  base (RAG over your notes/Obsidian vault) is a natural next addition once the core loop feels good.
