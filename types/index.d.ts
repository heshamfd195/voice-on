/** One block of a reply: what the pane shows and what the voice says (speak.py segments()). */
export type Segment = {
  kind: 'heading' | 'text' | 'item' | 'quote' | 'table' | 'code' | 'diagram' | 'drawing' | 'break'
  /** Its markdown: a line of prose, or a whole table, code block or diagram. */
  source: string
  /** What is said for it; empty for code already named once. */
  spoken: string
  lang?: string
  level?: number
  bullet?: boolean
  /** Where its spoken words start in the whole reply's spoken text, and how many there are. */
  w0: number
  words: number
}

/** The reply the pane shows. */
export type Shown = {
  /** Its place among this chat's replies (from 0) and how many there are; -1 when unknown. */
  index: number
  total: number
  markdown: string
  segments: Segment[]
  /** True while the pane follows what is read aloud; false once Previous or Next is pressed. */
  follow: boolean
}

/** Where the voice is: the reading in now.json, when it belongs to this chat. */
export type Reading = {
  /** When it started (ms); a new reading has a new id. */
  id: number
  /** `making` while the first audio is made, then `playing`, then `done` or `stopped`. */
  state: 'making' | 'playing' | 'done' | 'stopped'
  /** The reply being read. */
  markdown: string
  /** The word being said, counted over the whole reply's spoken text; -1 when none. */
  word: number
  /** The block read alone, or null for the whole reply. */
  part: number | null
}

declare module 'claude-code' {
  interface PluginState {
    'voice-on': {
      reading: Shaped<Reading | null>
      shown: Shaped<Shown | null>
      /** The voice's speed, from config.json: 1 is normal. */
      speed: Shaped<number>
    }
  }
}
