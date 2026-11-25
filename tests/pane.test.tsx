import { expect, test } from 'claude-code/testing'

import { indexOf, place, replies, titleOf, wordAt } from '../hooks/register'
import type { NowFile } from '../hooks/register'
import type { Segment } from '../types'

const PANE = {
  plugin: 'voice-on',
  component: 'Pane',
  requestId: 'read-along',
  props: {
    title: 'Read-along',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const SEGMENTS: Segment[] = [
  { kind: 'heading', source: '## The plan', spoken: 'The plan.', level: 2, w0: 0, words: 2 },
  { kind: 'break', source: '', spoken: '', w0: 2, words: 0 },
  { kind: 'text', source: 'First we read it.', spoken: 'First we read it.', w0: 2, words: 4 },
  { kind: 'code', source: 'print(1)', spoken: "There's Python code on screen.", lang: 'python', w0: 6, words: 5 },
]
const MARKDOWN = '## The plan\n\nFirst we read it.\n\n```python\nprint(1)\n```'

const ok = (stdout = '') => ({
  value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
})

test('the word estimate walks through the chunk', () => {
  const text = 'One two three four.'
  expect(wordAt(text, 0, 2)).toBe(0)
  expect(wordAt(text, 1, 2)).toBe(2)
  expect(wordAt(text, 2, 2)).toBe(3)
})

test('the word is counted over the whole reply, from where the chunk starts', () => {
  const f: NowFile = {
    session: 's', id: 1, state: 'playing', markdown: MARKDOWN, segments: SEGMENTS, part: null, base: 0,
    chunks: [
      { w0: 0, text: 'The plan.', t0: 0, dur: 1 },
      { w0: 2, text: 'First we read it.', t0: 1000, dur: 2 },
    ],
  }
  const first = { ...f, chunks: [f.chunks[0]!, { ...f.chunks[1]!, t0: null }] }
  expect(place(first, 500).word).toBe(1) // halfway through 'The plan.'
  expect(place(f, 1000).word).toBe(2) // the second chunk has started: its first word
  expect(place(f, 2900).word).toBe(5)
  expect(place(f, 10000).state).toBe('stopped') // its last chunk ended long ago
  // one block read alone: its words start after `base` words of the reply
  const part = { ...f, base: 2, chunks: [{ w0: 0, text: 'First we read it.', t0: 0, dur: 2 }] }
  expect(place(part, 0).word).toBe(2)
})

test('replies are the text after each turn last tool call', () => {
  const list = replies([
    { role: 'user', text: 'hi', toolUses: [] },
    { role: 'assistant', text: 'Let me look.', toolUses: [] },
    { role: 'assistant', text: '', toolUses: [{}] },
    { role: 'user', text: '', toolUses: [], toolResults: [{}] },
    { role: 'assistant', text: 'Found it.', toolUses: [] },
    { role: 'user', text: 'thanks', toolUses: [] },
    { role: 'assistant', text: 'Any time.', toolUses: [] },
  ])
  expect(list).toEqual(['Found it.', 'Any time.'])
  expect(indexOf(list, ' Found   it. ')).toBe(0)
  expect(indexOf(list, 'Something else')).toBe(-1)
  expect(titleOf(SEGMENTS)).toBe('The plan')
})

test('the pane names the reply, lights the word said, and its buttons drive speak.py', async ($, on) => {
  const ran: { argv: string[]; stdin?: string }[] = []
  on('clock.now', () => ({ value: 61000 }))
  on('process.run', (_, e) => {
    ran.push({ argv: [...e.argv], stdin: e.init?.stdin })
    return ok(JSON.stringify({ segments: SEGMENTS }))
  })
  on('session.messages', () => ({
    value: [
      { role: 'user', text: 'one', toolUses: [] },
      { role: 'assistant', text: 'An older reply.', toolUses: [] },
      { role: 'user', text: 'two', toolUses: [] },
      { role: 'assistant', text: MARKDOWN, toolUses: [] },
    ],
  }))
  // The host's state, seeded with a reply shown and its word 3 being said.
  const held = new Map<string, { value: unknown; version: number }>([
    ['shown', { value: { shape: 'shown-1', value: { index: 1, total: 2, markdown: MARKDOWN, segments: SEGMENTS, follow: true } }, version: 1 }],
    ['reading', { value: { shape: 'reading-1', value: { id: 1, state: 'playing', markdown: MARKDOWN, word: 3, part: null } }, version: 1 }],
  ])
  on('state.get', (_, e) => ({ value: held.get(e.key) ?? { value: undefined, version: 0 } }))
  on('state.set', (_, e) => {
    const version = (held.get(e.key)?.version ?? 0) + 1
    held.set(e.key, { value: e.value, version })
    return { value: { isSet: true as const, version } }
  })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /Reply 2 of 2/ }))?.text).toContain('Reply 2 of 2')
    expect((await ui.find({ type: 'Text', text: /^we$/ }))?.text).toBe('we') // word 3: lit
    expect((await ui.find({ type: 'Text', text: /^First $/ }))?.text).toBe('First ') // read: dimmed
    expect(await ui.find({ key: 'stop' })).toBeDefined()
    await ui.unmount()
  }
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'stop' })
  await ui.press({ key: 'part-2' })
  await ui.press({ key: 'previous' })
  await ui.unmount()
  expect(ran[0]?.argv).toContain('--halt')
  expect(ran[1]?.argv.slice(2, 5)).toEqual(['--speak', '--part', '2'])
  expect(ran[1]?.stdin).toBe(MARKDOWN)
  expect(ran[2]?.argv).toContain('--segments') // Previous loads the older reply's blocks
  expect(ran[2]?.stdin).toBe('An older reply.')
  const shown = held.get('shown')?.value as { value: { index: number; follow: boolean } }
  expect(shown.value.index).toBe(0) // Previous: the older reply is shown, and the pane stops following
  expect(shown.value.follow).toBe(false)
})

test('with nothing read yet the pane offers the latest reply', async ($, on) => {
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /each word lit/ }))?.text).toContain('each word lit')
  expect(await ui.find({ key: 'latest' })).toBeDefined()
  await ui.unmount()
})
