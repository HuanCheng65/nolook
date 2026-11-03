/** One line of nolook's actions.jsonl, as `nolook screen --stream` passes it on. */
export type Action = {
  /** Epoch seconds. */
  at: number
  /** tap, long-press, type, key, scroll down, open, launch, shot, motion, flow, wait, … */
  verb: string
  /** What the command was given: a query, a key, a URI, a flow's name. */
  query?: string | null
  target?: { role: string; label: string; id: string; bounds: number[] }
  settled?: number | null
  /** "screen" when the screen changed wholesale, else how many elements came or went. */
  change?: 'screen' | number
  guard?: string[] | null
  error?: string
  picture?: string | null
  steps?: number
  took?: number
}

/** What the stream last said about the device. */
export type Status = {
  device?: string
  app?: string
  /** The measurement holding the mirror off the device. */
  busy?: string | null
  error?: string | null
  /** Epoch seconds of the frame on show. */
  frameAt?: number
  live: boolean
}

/** The last shot or motion sheet, as a PNG file the terminal draws. */
export type Picture = { file: string; name: string; width: number; height: number }

declare module 'claude-code' {
  interface PluginState {
    'nolook': {
      actions: Shaped<Action[]>
      status: Shaped<Status>
      /** What the body shows: the device, or the last picture at full size. */
      view: 'screen' | 'picture'
      picture: Shaped<Picture | null>
      /** Bumped when the frame's size changes and the Image must be drawn again; same-size frames are blitted. */
      gen: number
    }
  }
}
