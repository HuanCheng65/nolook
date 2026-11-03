export type Entry = {
  /** The nolook command as run, shortened. */
  cmd: string
  /** Its first line of output, or its error. */
  head: string
  kind: 'ok' | 'error' | 'refused' | 'guard'
  at: string
}

declare module 'claude-code' {
  interface PluginState {
    'nolook': {
      log: Shaped<Entry[]>
      /** Bumped whenever the mirror or the picture changes; the bytes live in the module. */
      gen: number
      live: boolean
      picLabel: string
    }
  }
}
