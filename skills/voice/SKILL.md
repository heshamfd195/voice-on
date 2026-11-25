---
name: voice
description: Turn voice mode on or off - Claude's replies are read aloud, and a new prompt stops the speech. Use when the user types /voice-on:voice (on, off, status, repeat, stop, test, voices, setup kokoro), or asks to hear replies read aloud, to hear the last reply again, to stop talking, or to change the voice.
argument-hint: on | off | status | repeat | stop | test | voices | setup kokoro [all]
---

# voice: hear Claude's replies

Run the command that matches the argument with the Bash tool, then answer in one
short plain sentence, since it will be read aloud. Copy the command as written:
the session id and plugin path are filled in for you.

- `on` → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --on --session "${CLAUDE_SESSION_ID}"` (this chat only)
- `on all` → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --on --all --session "${CLAUDE_SESSION_ID}"` (every chat)
- `off` → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --off --session "${CLAUDE_SESSION_ID}"` (add `--all` to silence every chat)
- `status`, or no argument → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --status --session "${CLAUDE_SESSION_ID}"`, then say what it printed
- `stop` (or "stop talking") → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --halt`
- `test` → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --say "Voice mode is working."`
- `voices` → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --voices`, then name a few good ones
- `repeat` (or "say that again", "read your last message again") →
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --repeat --session "${CLAUDE_SESSION_ID}"`.
  It reads your previous reply again. Then reply with one short line only, such as
  "Reading it again." That line is not spoken, so the repeat is not cut off.
- `setup kokoro` → first tell the user it downloads about 120 MB and takes a few
  minutes, then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --setup kokoro`
  with a 10-minute timeout. Kokoro is a free neural voice that runs offline; once
  installed it is used automatically.

To change the voice, speed or engine, edit `~/.claude/voice-on/config.json`
(`--status` prints its path; the plugin README lists every setting). Changes apply
to the next reply.

While voice is on, keep replies short and easy to listen to: lead with the answer,
avoid long lists and tables.
