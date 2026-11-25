# Changelog

## 1.1.0

- Read-along pane: the reply being read is shown beside the chat with each word
  lit as it is said. A header names the reply ("Reply 3 of 8", its title, when
  it was read); Previous and Next step through the chat's replies; Read, Stop,
  Latest, and a play button on every block.
- Word timings with every engine, not only Kokoro: speech is made sentence by
  sentence and played in turn, and `now.json` records when each started.

## 1.0.0 (2026-11-25)

First release as a Claude Code plugin.

- Replies read aloud per chat or in every chat; a new prompt stops the speech.
- System voices: macOS `say`, `espeak-ng` on Linux.
- Kokoro neural voice, installed with `/voice-on:voice setup kokoro`.
- Tables, drawings, Mermaid diagrams, links, paths and code read in a way that
  makes sense out loud.
- `repeat`, `stop`, `status`, `test` and `voices` commands.
