# Jarvis Personal Manager — Design Spec

**Date**: 2026-08-22
**Status**: Approved, ready for implementation planning

## Overview

A ground-up rebuild of Jarvis as Tariq's personal manager: a local, voice-driven
assistant that helps run his day across dev work, school, content creation, and
business/venture tracking, in addition to the system control the old Jarvis
already did. This replaces the previous Jarvis entirely.

**Migration note**: the prior Jarvis implementation was archived to the
`jarvis-v1-archive` branch on `github.com/GalaxyGamingFR/Jarvis` before this
rebuild started. Working tree wipe and rebuild happens on `master` after this
spec is approved.

## Goals

- Talk to it like a real personal assistant — natural, continuous conversation,
  not one command per wake-word utterance.
- One assistant across five domains: system control, dev, school, content,
  business — reliably, even as the tool surface grows well past the old
  Jarvis's 37 tools.
- Reuse what already worked in the old Jarvis (wake word, FastAPI/WebSocket
  skeleton, HUD frontend, rate-limit handling) rather than re-solving it.
- Keep the LLM backend swappable so upgrading from Gemini to the Claude API
  later (once a business is generating revenue) is a config/client change,
  not a rewrite.

## Out of scope (this spec)

- **Fast clip-generation pipeline.** Tariq wants a new, faster method for
  turning YouTube videos into clips for the `whoaskinyou` account (the
  existing `publikclip` pipeline is too slow). This is a substantial,
  independent engineering effort (video processing, highlight scoring,
  captioning, rendering) and gets its own design spec and build. This spec
  only defines a `generate_clips` tool stub in the content domain that calls
  the *existing* publikclip pipeline; swapping in the new pipeline later is
  a tool-implementation change, not a Jarvis architecture change.
- Automated notification ingestion (email scanning, contact-form leads,
  etc.) for the business domain — notifications are voice-reported by Tariq
  for now.
- Claude API integration — designed for (swappable client interface), not
  built. Gemini only in this build.

## Architecture

Local Python/FastAPI service in `transfer\jarvis`, WebSocket-driven, with a
browser HUD frontend for visual feedback — the same skeleton pattern as the
old Jarvis, since it's proven infrastructure.

**Domain-routed, two-hop request flow**, chosen over the old Jarvis's flat
tool list because the tool surface here is meaningfully larger and spans more
unrelated domains:

1. **Router hop**: a lightweight Gemini call classifies the utterance into
   one domain — `system`, `dev`, `school`, `content`, `business`, or
   `general` (small talk / anything that doesn't need tools).
2. **Domain hop**: a Gemini tool-calling turn runs with only that domain's
   tool subset and system-prompt section loaded, executes any tool calls,
   and produces the spoken response.

This keeps each domain's prompt and tool list focused, and lets domains
evolve independently (e.g. adding tools to the business domain doesn't grow
every other domain's prompt).

## Voice pipeline

- **Wake word**: "Hey Jarvis" via `openWakeWord`, reused from the old
  Jarvis — offline, no cloud cost, proven.
- **Conversation sessions, not per-utterance wake**: once woken, Jarvis
  stays in an open conversation session — Tariq talks back and forth
  naturally without repeating the wake word — until a silence timeout closes
  the session and it returns to wake-word listening.
- **TTS**: ElevenLabs' Jarvis premade voice, free tier. The free tier caps
  around 10k characters/month — the client tracks usage and degrades
  gracefully (falls back to text-only HUD response with a logged warning)
  rather than erroring out when the quota is exhausted.
- **STT**: reuse whatever the old Jarvis used for speech-to-text into the
  FastAPI/WebSocket pipeline (confirm exact mechanism during implementation
  planning by reviewing the archived `jarvis-v1-archive` branch).

## LLM backend

Gemini only, for this build. Client code sits behind a small interface
(e.g. `LLMClient` with `route(utterance) -> domain` and
`respond(domain, messages, tools) -> response`) so swapping in the Claude
API later means writing a new implementation of that interface and changing
a config value — not touching the domain/tool logic. Reuses the old Jarvis's
rotating-API-key rate-limit handling (20 req/min × N rotating keys).

## Domain modules

### System control
Ported from the old Jarvis: volume, lock/sleep, app launching, file search,
screen vision, macros. Keep the existing guardrail — no proactive volume
changes without being asked.

### Dev
Launch and check on Claude Code sessions for Tariq's projects by voice
(e.g. "Jarvis, kick off Claude Code on the SchoolPlan calendar bug"). Scope
is limited to launching/managing sessions — not git-status awareness or
generic task tracking (YAGNI; add later if actually needed).

### School
Reads and writes SchoolPlan's data (`school.tariqkhalif.me`) — assignments,
calendar, deadlines. Exact integration mechanism (direct data-store access
vs. an API SchoolPlan exposes) needs to be determined during implementation
planning by inspecting the SchoolPlan codebase.

### Content
- Conversational script/caption drafting for `@dailytariq`.
- `generate_clips` tool: stub that calls the existing publikclip pipeline
  for `whoaskinyou`/`epicvirals` clip generation (see Out of scope above for
  the future faster-pipeline swap).

### Business
- Structured venture tracking: Tariq can create/update entities by voice —
  name, status, notes, next steps — for projects/companies he's building.
- Client/project notification tracking: Tariq tells Jarvis about
  client emails, project updates, etc. by voice; Jarvis stores and can
  recall/remind. No automated ingestion in this build.

## Memory

Upgrade from the old Jarvis's flat `memory.json` to structured SQLite. This
build's memory needs to hold real entities (business ventures, cached school
deadlines, content queue, conversation/session history), not just freeform
notes, and SQLite also sidesteps the old corrupted-JSON-on-load bug class.

## Error handling

- Dispatch-level try/except safety net around every tool call (carried
  forward from a lesson learned in the old Jarvis's `memory.py`/
  `system_control.py` bugs).
- WebSocket disconnect handling (also carried forward — the old Jarvis hit a
  `RuntimeError` on `websocket.send` after client disconnect).
- ElevenLabs quota tracking with graceful text-only fallback.
- Gemini rate-limit rotation across API keys.

## Testing approach

Personal voice app — plan for manual, voice-driven verification of each
domain as it's built (Tariq's established pattern), rather than a large
automated suite. Unit tests where logic is pure and cheap to test in
isolation: the memory store (SQLite CRUD), router classification, and
business-entity CRUD.

## Open questions for implementation planning

- Exact SchoolPlan data-access mechanism (see School domain above).
- Exact STT mechanism carried forward from the old Jarvis (see Voice
  pipeline above) — confirm by reviewing `jarvis-v1-archive`.
