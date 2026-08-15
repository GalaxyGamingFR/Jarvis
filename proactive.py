"""Thin hand-off point for Jarvis to speak up unprompted (reminders, timers, calendar alerts, etc.)
without any of those features needing to know about WebSockets, active_connections, or asyncio.

WHAT THIS MODULE IS: a module-level queue. Any other module (timers.py, a future calendar poller,
weather-alert checker, ...) calls queue_message(text) when it decides Jarvis should say something
unprompted. This module does nothing else — no scheduling, no delivery, no TTS. Delivery is
server.py's job, via a polling loop that does not exist yet. This docstring is the spec for that loop
so it can be built directly from these notes without re-deriving the design.

INTEGRATION — what server.py needs to add:

1. A periodic task, started once at process startup (e.g. in an `@app.on_event("startup")` handler,
   or right after `active_connections` is defined), that loops forever:

       import proactive

       _proactive_task: asyncio.Task | None = None  # module-level, keeps a strong reference

       async def proactive_poll_loop():
           while True:
               await asyncio.sleep(5)  # poll interval; 5s is a reasonable default
               for text in proactive.drain_queue():
                   for connection in list(active_connections):
                       if is_connection_active(connection):
                           continue  # mid-conversation — don't talk over the user, just drop it
                       audio_b64 = synthesize_speech(text)
                       await connection["websocket"].send_json(
                           {"type": "proactive_message", "text": text, "audio_b64": audio_b64}
                       )

       @app.on_event("startup")
       async def _start_proactive_loop():
           global _proactive_task
           _proactive_task = asyncio.create_task(proactive_poll_loop())

   The module-level `_proactive_task` reference is required for the same reason `/trigger-wake`
   keeps its one-off wake tasks in `_background_tasks`: asyncio.create_task() only holds a WEAK
   reference to the task internally — with nothing else pointing at it, the task object can be
   garbage-collected mid-run and the loop silently dies. `/trigger-wake` solves this with a
   `_background_tasks: set[asyncio.Task]` plus `add_done_callback(_background_tasks.discard)` because
   its tasks are one-shot and short-lived; this loop is a single long-lived task instead, so one
   module-level variable holding it is enough — nothing ever removes it.

2. A new WebSocket message type, `proactive_message`, distinct from `wake_push`:

       {"type": "proactive_message", "text": "...", "audio_b64": "..." | null}

   It is deliberately NOT `wake_push` + `handle_turn(...)`. `wake_push` means "the user woke Jarvis,
   expect them to talk next" — the frontend calls beginSession() and opens the mic for a follow-up.
   A reminder firing while nobody asked for anything should just be spoken and then go quiet again;
   forcing the mic open afterward would make Jarvis start listening (and potentially re-trigger
   wake-word/clap logic) after every unprompted announcement, which is the wrong default. Frontend
   handling (in the `ws.addEventListener("message", ...)` switch in main.js) should look like:

       } else if (msg.type === "proactive_message") {
         addBubble("assistant", msg.text);
         speak(msg.text, msg.audio_b64);
         // no beginSession() — don't open the mic just because Jarvis spoke unprompted
       }

   If a given proactive message DOES want a follow-up conversation (e.g. "you have a meeting in 5
   minutes, want me to do anything?"), that's a job for a different, explicit call path (reuse
   external_wake()/wake_push), not something this generic queue should special-case.

3. Interrupt-vs-skip: reuses is_connection_active(connection) exactly as /trigger-wake does — per
   connection, if the tab is already mid-conversation (awake and listening/speaking, and that flag
   hasn't self-expired past SESSION_ACTIVE_MAX_AGE_S), skip delivering to THAT connection this poll
   cycle rather than talking over the user. Note this module's queue is drained once per poll
   regardless of whether every connection was busy — a message that arrives while every tab is mid-
   conversation is silently dropped rather than retried. That's an intentional simplification (this
   queue is meant to be dead simple, not a message broker with delivery guarantees); a caller that
   truly cannot afford to lose a notification should re-queue it itself, or a future version of the
   poll loop could requeue on an all-skipped cycle.

USAGE from other modules (e.g. timers.py):

    import proactive
    proactive.queue_message("Your 10-minute timer is up.")

That's the entire contract other feature modules need to know.
"""

from threading import Lock

_queue: list[str] = []
_lock = Lock()


def queue_message(text: str) -> None:
    """Queue a piece of text for Jarvis to speak next time server.py's proactive poll loop runs.
    Safe to call from any thread (e.g. a timer callback running off the main event loop)."""
    with _lock:
        _queue.append(text)


def drain_queue() -> list[str]:
    """Return everything queued since the last drain, and clear the queue. Called by server.py's
    (not-yet-written) periodic poll loop — see module docstring for the exact integration."""
    with _lock:
        drained = _queue[:]
        _queue.clear()
        return drained
