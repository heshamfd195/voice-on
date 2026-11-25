<p align="center"><img src="assets/icon.svg" width="96" alt=""></p>

# voice-on

A Claude Code plugin that reads Claude's replies out loud. Turn it on in a chat,
ask your question (typed or dictated), and hear the answer. A new prompt stops the
speech at once.

Everything runs on your computer. It uses your system voice out of the box, and
can install [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), a free neural
voice that sounds far more natural and also runs offline.

## Install

In Claude Code:

```
/plugin marketplace add heshamfd195/voice-on
/plugin install voice-on@voice-on
```

Or from a terminal:

```bash
claude plugin marketplace add heshamfd195/voice-on
```

```bash
claude plugin install voice-on@voice-on
```

You need `python3` (3.9 or newer) on your PATH. On Linux, also install
`espeak-ng` for the system voice (`sudo apt install espeak-ng`).

## Use it

| Command | What it does |
|---|---|
| `/voice-on:voice on` | Read this chat's replies aloud |
| `/voice-on:voice on all` | Read every chat's replies aloud |
| `/voice-on:voice off` | Silence this chat (`off all` silences every chat) |
| `/voice-on:voice stop` | Stop talking now |
| `/voice-on:voice repeat` | Read the last reply again |
| `/voice-on:voice status` | What is on, and which voice |
| `/voice-on:voice test` | Say a test sentence |
| `/voice-on:voice voices` | List the voices you can pick |
| `/voice-on:voice setup kokoro` | Install the Kokoro neural voice |

You can also just ask: "read your replies out loud", "say that again",
"stop talking".

Voice is per chat, so parallel chats don't talk over each other. One reply plays
at a time; a newer reply cuts off an older one. To stop the speech, send any
prompt or run `/voice-on:voice stop`.

## Read-along pane

In the Claude desktop app, a pane opens beside the chat while a reply is read
aloud. It shows the reply again, block by block, and lights each word as it is
said; words already read are dimmed. Tables, code and diagrams are drawn as
themselves and framed while the voice is on them.

- The header says what you are looking at: "Reply 3 of 8", the reply's title,
  and when it was read.
- ◀ Previous and Next ▶ step through this chat's earlier replies. ▶ Read plays
  the one shown, ■ Stop stops it, and Latest jumps back to the newest.
- Each block has its own ▶ to hear just that part.
- With the pane focused: `p` previous, `n` next, `r` read, `s` stop, `l` latest.

Type `/read-along` to open it by hand. Word timing is exact per sentence and
estimated inside a sentence, so the light can run slightly ahead or behind.

## What gets read

Only the final reply, not the "let me check" lines before tool calls. Replies
are cleaned up for listening:

- Markdown symbols are dropped. Links are read as their text, URLs as "a link",
  and file paths as the file name (`src/app/main.ts:42` is "main.ts line 42").
  Short slashed words such as and/or or CI/CD stay as they are.
- Tables are read row by row, each cell named by its column: "Kokoro: Speed
  fast, Cost free". ✅ is read as yes and ❌ as no. After 12 rows it says how
  many more are on screen.
- Drawings (box lines and arrows in a plain code block) are read as their
  labels: "A drawing. Client to API. Then Postgres."
- Mermaid diagrams are described: a flowchart as its links, a sequence diagram
  as its messages.
- Other code is named once per kind: "There's Python code on screen", "There's
  a command on screen".

## Voices

| Engine | Where | Quality | Setup |
|---|---|---|---|
| `say` | macOS | Good with Premium or Enhanced voices | Built in |
| `espeak` | Linux | Robotic but clear | `sudo apt install espeak-ng` |
| `kokoro` | macOS, Linux | Natural, neural | `/voice-on:voice setup kokoro` |

`setup kokoro` makes a Python environment in `~/.claude/voice-on/venv`, installs
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), and downloads the
model (about 120 MB to download, about 300 MB on disk). It needs Python 3.10 to 3.13, or
[uv](https://docs.astral.sh/uv/). Once installed it is used automatically. If it
ever fails, the system voice reads the reply instead.

On macOS, better system voices are free in System Settings → Accessibility →
Spoken Content → System Voice → Manage Voices.

## Settings

`~/.claude/voice-on/config.json` is made the first time you turn voice on.
Changes apply to the next reply.

```json
{
  "engine": "auto",
  "say": {"voice": "", "rate": 190},
  "espeak": {"voice": "en-us", "rate": 175},
  "kokoro": {"voice": "af_heart", "speed": 1.1, "lang": "en-us"}
}
```

- `engine`: `auto` (Kokoro if installed, otherwise the system voice), `say`,
  `espeak` or `kokoro`.
- `say.voice`: empty for the system default, or a name such as Samantha, Daniel
  or Reed (`say -v '?'` lists them). `rate` is words per minute.
- `espeak.voice`: a voice such as `en-us` or `en-gb` (`espeak-ng --voices=en`).
- `kokoro.voice`: `af_heart`, `af_bella`, `af_nicole`, `am_michael`, `am_adam`
  (American), `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis` (British).
  `speed` 1.0 is normal.

Set `VOICE_ON_HOME` to keep the settings somewhere other than `~/.claude/voice-on`.

## How it works

- A `Stop` hook runs `scripts/speak.py` when Claude finishes a reply. It cleans
  the text and starts a speaker in the background, so Claude never waits.
- A `UserPromptSubmit` hook stops the speaker when you send a new prompt.
- Both hooks do nothing unless voice is on somewhere: `~/.claude/voice-on/on`
  lists the chats with voice on, or `*` for all.
- `speak.py` uses only the Python standard library. It splits the reply into
  sentences, has the engine make the audio for each (Kokoro in its own venv,
  `say` or `espeak-ng` otherwise), plays them in turn, and writes `now.json`:
  the reply, its blocks, and when each sentence started and how long it lasts.
  The first words start in about a second.
- `hooks/register.tsx` is the read-along pane, a Claude Code mod: it watches
  `now.json` and draws the reply with the word being said lit.

Files in `~/.claude/voice-on/`:

- `config.json`: your settings.
- `on`: the chats with voice on.
- `last/`: each chat's last reply, for `repeat`. Kept only while voice is on
  somewhere, and deleted after a week.
- `now.json`: the reply being read and its timings, for the pane.
- `voice.log`: one line per spoken reply or error. Look here if nothing is heard.
- `venv/`, `models/`: the Kokoro engine, if installed.

## Privacy

Nothing leaves your computer. Speech is made locally, and the plugin makes no
network calls, except `setup kokoro`, which downloads packages from PyPI and the
model from GitHub.

## Remove it

```
/plugin uninstall voice-on@voice-on
```

Then delete the settings and the Kokoro files:

```bash
rm -rf ~/.claude/voice-on
```

## Not supported yet

- Windows.
- Reading replies in more than one language.
- Push-to-talk input. Use your system's dictation (macOS: press the dictation
  key) to speak your prompts.

## Contributing

Issues and pull requests are welcome. Run the tests with:

```bash
python3 -m unittest discover -s tests
```

```bash
claude plugin test .
```

The second runs the pane's tests (`tests/pane.test.tsx`) against Claude Code.

`python3 scripts/speak.py --clean FILE` prints what would be said for a markdown
file or a transcript (`.jsonl`), which helps when changing how replies are read.

## Credits

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad (Apache 2.0).
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) by thewh1teagle (MIT).

## License

MIT
