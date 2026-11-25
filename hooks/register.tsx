// Read-along pane for voice-on: the reply being read aloud, each word lit as it is said.
//
// scripts/speak.py keeps now.json up to date while it reads a reply: the chat it
// belongs to, the reply's markdown and its blocks (what is shown and what is said),
// and when each chunk of speech started playing and how long it lasts. This mod
// polls that file and, for a reading of this chat, opens a pane beside the chat.
// Chunk timings are exact; the word inside a chunk is estimated from word lengths.
//
// The pane draws the reply again, block by block: headings, prose and lists as
// words that dim once read and light while said; tables, code and diagrams as
// themselves, framed while the voice is on them. Previous and Next step through this
// chat's earlier replies, read from the session; Read plays the one shown. The speed
// buttons set the voice's speed (config.json "speed") and pick up where it was.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Reading, Segment, Shown } from '../types'

const PANE = 'read-along'
const TITLE = 'Read-along'
/** A light yellow behind the word being said, with the word in black on it. */
const HIGHLIGHT = '#FFE680'
const TICK_MS = 150
/** A chunk that should have ended this long ago means the speaker died unseen. */
const STALE_MS = 4000

const reading = atom({ plugin: 'voice-on', key: 'reading' } as const, null, { shape: 'reading-1' })
const shown = atom({ plugin: 'voice-on', key: 'shown' } as const, null, { shape: 'shown-1' })
const speed = atom({ plugin: 'voice-on', key: 'speed' } as const, 1, { shape: 'speed-1' })

/** The speeds the pane offers: 1 is the voice's normal pace. */
export const SPEEDS = [0.75, 1, 1.25, 1.5, 2] as const

type Chunk = { w0: number; text: string; t0: number | null; dur: number | null }

/** now.json, as speak.py writes it. */
export type NowFile = {
  session: string
  id: number
  state: Reading['state']
  markdown: string
  segments: Segment[]
  part: number | null
  /** Words of the reply before the block read alone (0 for the whole reply). */
  base: number
  chunks: Chunk[]
}

/** The messages a session holds, as far as this mod reads them. */
type Message = { role: string; text: string; toolUses: readonly unknown[]; toolResults?: readonly unknown[] }

// Module variables: a reload starts them over, and the next tick refills them.
let sessionId = ''
let nowPath = ''
let configPath = ''
let speakPath = ''
let configMtime = -1
let file: NowFile | undefined
let seenMtime = -1
let seenId = 0
let openedFor = 0

/** A word as shown: German comes marked [de]...[/de] for the voice, not for the eye. */
const bare = (word: string) => word.replace(/\[\/?de\]/g, '')

/** The block the word `word` is in, or -1. */
export function blockAt(segments: readonly Segment[], word: number): number {
  return segments.findIndex(s => word >= s.w0 && word < s.w0 + s.words)
}

/** The word of `text` being said `elapsed` seconds into a chunk lasting `dur`. */
export function wordAt(text: string, elapsed: number, dur: number): number {
  const words = text.split(/\s+/).filter(Boolean)
  if (words.length === 0) return -1
  // Longer words take longer; a full stop or comma adds a pause after the word.
  const weights = words.map(w => w.length + 1 + (/[.!?:;]$/.test(w) ? 4 : /,$/.test(w) ? 2 : 0))
  const total = weights.reduce((a, b) => a + b, 0)
  const target = Math.min(1, Math.max(0, elapsed / (dur || 1))) * total
  let sum = 0
  for (let i = 0; i < words.length; i++) {
    sum += weights[i] ?? 0
    if (sum > target) return i
  }
  return words.length - 1
}

/** Where the voice is in `f` at time `now` (ms). */
export function place(f: NowFile, now: number): Reading {
  let state = f.state
  let current: Chunk | undefined
  for (const c of f.chunks) if (c.t0 !== null) current = c
  if (state === 'playing' && current) {
    const ends = (current.t0 ?? 0) + (current.dur ?? 0) * 1000
    if (now > ends + STALE_MS) state = 'stopped'
  }
  let word = -1
  if (state === 'playing' && current) {
    const elapsed = (now - (current.t0 ?? now)) / 1000
    word = f.base + current.w0 + Math.max(0, wordAt(current.text, elapsed, current.dur ?? 1))
  }
  return { id: f.id, state, markdown: f.markdown, word, part: f.part }
}

/** This chat's replies, oldest first: each turn's text after its last tool call. */
export function replies(messages: readonly Message[]): string[] {
  const out: string[] = []
  let tail: string[] = []
  const close = () => {
    const text = tail.join('\n\n').trim()
    if (text) out.push(text)
    tail = []
  }
  for (const m of messages) {
    if (m.role === 'user') {
      if (!m.toolResults?.length) close() // a new prompt: the turn before it is over
    } else if (m.toolUses.length > 0) {
      tail = [] // text before a tool call is narration, not the reply
    } else if (m.text.trim()) {
      tail.push(m.text)
    }
  }
  close()
  return out
}

const squash = (s: string) => s.replace(/\s+/g, ' ').trim()

/** Which of `list` is `markdown`: the newest that matches, or -1. */
export function indexOf(list: readonly string[], markdown: string): number {
  const want = squash(markdown)
  for (let i = list.length - 1; i >= 0; i--) {
    const have = squash(list[i] ?? '')
    if (have === want || (want.length > 40 && (have.endsWith(want) || want.endsWith(have)))) return i
  }
  return -1
}

/** A short title for a reply: its first heading, or its first line of prose. */
export function titleOf(segments: readonly Segment[]): string {
  const first =
    segments.find(s => s.kind === 'heading') ?? segments.find(s => s.spoken.trim() !== '')
  const text = bare((first?.spoken ?? '').split('\n')[0] ?? '').replace(/[.:]$/, '')
  return text.length > 70 ? `${text.slice(0, 67).trimEnd()}…` : text
}

export function ago(ms: number): string {
  const min = Math.floor(ms / 60000)
  return min < 1 ? 'just now' : min < 60 ? `${min} min ago` : `${Math.floor(min / 60)} h ago`
}

const python = ($: EngineInterface, args: string[], stdin = '') =>
  $.process
    .run(['python3', speakPath, ...args, '--session', sessionId], { stdin, timeoutMs: 15000 })
    .catch(() => undefined)

/** The blocks of a reply, from speak.py; one plain block if that fails. */
async function segmentsOf($: EngineInterface, markdown: string): Promise<Segment[]> {
  const ran = await python($, ['--segments'], markdown)
  try {
    return (JSON.parse(ran?.stdout ?? '') as { segments: Segment[] }).segments
  } catch {
    const words = markdown.split(/\s+/).filter(Boolean).length
    return [{ kind: 'text', source: markdown, spoken: markdown, w0: 0, words }]
  }
}

/** Show reply `to` of this chat (`latest` for the newest), and stop following the voice. */
async function go($: EngineInterface, to: number | 'latest') {
  const list = replies(await $.session.messages())
  if (list.length === 0) return
  const at = await read($, shown)
  const from = at && at.index >= 0 ? at.index : list.length - 1
  const index = to === 'latest' ? list.length - 1 : Math.min(list.length - 1, Math.max(0, from + to))
  const markdown = list[index] ?? ''
  const segments = await segmentsOf($, markdown)
  await update($, shown, () => ({ index, total: list.length, markdown, segments, follow: to === 'latest' }))
}

/** A new reading of this chat: show its reply, and find where it sits among the replies. */
async function follow($: EngineInterface, f: NowFile) {
  const at = await read($, shown)
  if (at && !at.follow && squash(at.markdown) === squash(f.markdown)) return // already shown
  await update($, shown, () => ({ index: -1, total: 0, markdown: f.markdown, segments: f.segments, follow: true }))
  const list = replies(await $.session.messages())
  const index = indexOf(list, f.markdown)
  await update($, shown, s => (s && s.markdown === f.markdown ? { ...s, index, total: list.length } : s))
}

/** The speed config.json holds, read again whenever the file changes. */
async function readSpeed($: EngineInterface) {
  const stat = await $.fs.stat(configPath).catch(() => undefined)
  if (!stat || stat.mtimeMs === configMtime) return
  configMtime = stat.mtimeMs
  try {
    const value = Number((JSON.parse(await $.fs.read(configPath)) as { speed?: unknown }).speed ?? 1)
    if (Number.isFinite(value) && value > 0 && value !== (await read($, speed))) await update($, speed, () => value)
  } catch {
    // caught mid-write: the next tick reads it
  }
}

/** Set the voice's speed; if this chat's reply is being read, carry on from its block at the new speed. */
async function setSpeed($: EngineInterface, value: number) {
  await python($, ['--speed', String(value)])
  await update($, speed, () => value)
  const r = await read($, reading)
  if (!file || !r || (r.state !== 'playing' && r.state !== 'making')) return
  const at = blockAt(file.segments, r.word)
  const args = file.part !== null ? ['--part', String(file.part)] : at > 0 ? ['--from', String(at)] : []
  await python($, ['--speak', ...args], file.markdown)
}

async function tick($: EngineInterface) {
  await readSpeed($)
  const stat = await $.fs.stat(nowPath).catch(() => undefined)
  if (!stat) return
  if (stat.mtimeMs !== seenMtime) {
    seenMtime = stat.mtimeMs
    try {
      file = JSON.parse(await $.fs.read(nowPath)) as NowFile
    } catch {
      return // caught mid-write: the next tick reads it
    }
  }
  if (!file || !sessionId || file.session !== sessionId) return
  const now = place(file, await $.clock.now())
  if (file.id !== seenId) {
    seenId = file.id
    await follow($, file)
  }
  if ((now.state === 'making' || now.state === 'playing') && openedFor !== now.id) {
    openedFor = now.id
    void $.ui.open({ id: PANE, title: TITLE })
  }
  const held = await read($, reading)
  const same =
    held !== null && held.id === now.id && held.state === now.state && held.word === now.word
  if (!same) await update($, reading, () => now)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    sessionId = await $.session.id()
    const home = (await $.env.get('VOICE_ON_HOME')) || `${(await $.env.get('HOME')) ?? ''}/.claude/voice-on`
    const dir = home.replace(/^~(?=\/)/, (await $.env.get('HOME')) ?? '~')
    nowPath = `${dir}/now.json`
    configPath = `${dir}/config.json`
    speakPath = `${$.plugin.root}/scripts/speak.py`
    await $.command.register({
      name: 'read-along',
      description: 'Open the read-along pane: replies read aloud, each word lit as it is said',
    })
    $.clock.every(TICK_MS, () => void tick($).catch(() => undefined))
    return started
  })

  on('command.run', { command: 'read-along' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })
    if (!(await read($, shown))) await go($, 'latest').catch(() => undefined)
    return { text: 'Read-along pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Code, Markdown } = $.ui.resolve(e)
    const r = await read($, reading)
    const s = await read($, shown)
    const pace = await read($, speed)
    const now = await $.clock.now()

    if (!s) {
      return (
        <Box flexDirection="column" gap={1}>
          <Text bold>🔊 Read-along</Text>
          <Text dimColor>
            When voice is on, the reply being read shows here, each word lit as it is said. Press
            Latest to see this chat's last reply.
          </Text>
          <Box flexDirection="row">
            <Button key="latest" variant="primary" hotkey="l" onPress={() => void go($, 'latest').catch(() => undefined)}>
              Latest
            </Button>
          </Box>
        </Box>
      )
    }

    // The voice is on the reply shown: light its words. Otherwise nothing is lit.
    const live = r !== null && squash(r.markdown) === squash(s.markdown)
    const isReading = live && (r.state === 'playing' || r.state === 'making')
    const word = live && r.state === 'playing' ? r.word : -1
    const status = !live
      ? ''
      : r.state === 'making'
        ? 'Getting ready…'
        : r.state === 'playing'
          ? 'Reading'
          : r.state === 'stopped'
            ? `Stopped · ${ago(now - r.id)}`
            : `Read ${ago(now - r.id)}`
    const where = s.index >= 0 ? `Reply ${s.index + 1} of ${s.total}` : 'Reply'
    const atFirst = s.index <= 0
    const atLast = s.index < 0 || s.index >= s.total - 1

    // Every line is drawn the same way on every frame: plain words, one space
    // between them. Only the colours move, so no width or line break changes.
    const drawWords = (spoken: string, w0: number, look: Record<string, unknown> = {}) =>
      spoken
        .split('\n')
        .filter(line => line.trim() !== '')
        .map(line => {
          const words = line.split(/\s+/).filter(Boolean).map(bare)
          const first = w0
          w0 += words.length
          const said = Math.max(0, Math.min(words.length, word - first))
          const lit = word >= first && word < first + words.length
          const children: (string | JSX.Element)[] = []
          const done = words.slice(0, said).join(' ')
          const rest = words.slice(lit ? said + 1 : said).join(' ')
          if (done) children.push(<Text dimColor>{done + (lit || rest ? ' ' : '')}</Text>)
          if (lit) {
            children.push(
              <Text backgroundColor={HIGHLIGHT} color="black">
                {words[said] ?? ''}
              </Text>,
            )
            if (rest) children.push(' ')
          }
          if (rest) children.push(rest)
          return <Text {...look}>{children}</Text>
        })

    const play = (i: number) =>
      void python($, ['--speak', '--part', String(i)], s.markdown)

    const blocks = s.segments.map((seg, i) => {
      if (seg.kind === 'break') return null
      const active = word >= seg.w0 && word < seg.w0 + seg.words
      const playButton = seg.words > 0 && (
        <Button key={`part-${i}`} plain dimColor onPress={() => play(i)}>
          ▶
        </Button>
      )
      if (seg.kind === 'table' || seg.kind === 'code' || seg.kind === 'diagram' || seg.kind === 'drawing') {
        const label =
          seg.kind === 'table'
            ? 'Table'
            : seg.kind === 'diagram'
              ? 'Diagram'
              : seg.kind === 'drawing'
                ? 'Drawing'
                : seg.lang
                  ? `Code · ${seg.lang}`
                  : 'Code'
        return (
          <Box flexDirection="column" gap={0}>
            <Box flexDirection="row" gap={1}>
              {playButton}
              <Text dimColor size="caption">
                {label}
              </Text>
            </Box>
            <Box
              flexDirection="column"
              borderStyle="round"
              borderColor={active ? HIGHLIGHT : undefined}
              borderDimColor={!active}
              paddingX={1}
            >
              {seg.kind === 'table' ? (
                <Markdown text={seg.source} />
              ) : (
                <Code
                  source={seg.source}
                  language={seg.kind === 'diagram' ? 'mermaid' : seg.kind === 'drawing' ? 'text' : seg.lang || undefined}
                />
              )}
            </Box>
            {active && <Box flexDirection="column">{drawWords(seg.spoken, seg.w0, { dimColor: false, italic: true })}</Box>}
          </Box>
        )
      }
      const look =
        seg.kind === 'heading'
          ? { bold: true, size: (seg.level ?? 2) <= 2 ? 'heading' : 'body' }
          : seg.kind === 'quote'
            ? { italic: true }
            : {}
      return (
        <Box flexDirection="row" gap={1}>
          {playButton}
          {seg.kind === 'item' && seg.bullet && <Text>•</Text>}
          {seg.kind === 'quote' && <Text dimColor>│</Text>}
          <Box flexDirection="column" flexShrink={1}>
            {drawWords(seg.spoken, seg.w0, look)}
          </Box>
        </Box>
      )
    })

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="column" borderStyle="round" borderDimColor paddingX={1}>
          <Box flexDirection="row" justifyContent="space-between">
            <Text bold>🔊 {where}</Text>
            <Text dimColor>{status}</Text>
          </Box>
          <Text dimColor wrap="truncate-end">
            {titleOf(s.segments)}
          </Text>
        </Box>
        <Box flexDirection="row" gap={1}>
          <Button key="previous" hotkey="p" dimColor={atFirst} onPress={() => void (atFirst || go($, -1).catch(() => undefined))}>
            ◀ Previous
          </Button>
          {isReading ? (
            <Button key="stop" variant="primary" hotkey="s" onPress={() => void python($, ['--halt'])}>
              ■ Stop
            </Button>
          ) : (
            <Button key="read" variant="primary" hotkey="r" onPress={() => void python($, ['--speak'], s.markdown)}>
              ▶ Read
            </Button>
          )}
          <Button key="next" hotkey="n" dimColor={atLast} onPress={() => void (atLast || go($, 1).catch(() => undefined))}>
            Next ▶
          </Button>
          {(!s.follow || !atLast) && (
            <Button key="latest" hotkey="l" onPress={() => void go($, 'latest').catch(() => undefined)}>
              Latest
            </Button>
          )}
        </Box>
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text dimColor>Speed</Text>
          {SPEEDS.map(x => (
            <Button
              key={`speed-${x}`}
              plain={x === pace ? undefined : true}
              variant={x === pace ? 'primary' : undefined}
              dimColor={x !== pace}
              onPress={() => void setSpeed($, x).catch(() => undefined)}
            >
              {`${x}×`}
            </Button>
          ))}
        </Box>
        <Box flexDirection="column" gap={1}>
          {blocks}
        </Box>
      </Box>
    )
  })
}
