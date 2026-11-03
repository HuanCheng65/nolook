import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Action, Status } from '../types'

// The device as you see it, beside the chat: a live mirror on the left, what
// nolook did on the right. Its pictures go to the terminal only: the model
// never reads them, so the pane costs no context.
//
// One `nolook screen --stream` child feeds it: frames, the app in front, and
// each line of the action journal, which nolook writes for every action,
// the ones a subagent or a flow takes included.

const PANE = 'nolook'
// A terminal cell is about twice as tall as it is wide.
const CELL_ASPECT = 2
// The actions beside the mirror get at least this many columns; when that
// would leave the mirror narrower than MIRROR_MIN, they go under it instead.
const SIDE_MIN = 26
const MIRROR_MIN = 16
const KEEP = 60

const actions = atom({ plugin: 'nolook', key: 'actions' } as const, [], { shape: 'actions-1' })
const status = atom({ plugin: 'nolook', key: 'status' } as const, { live: true }, { shape: 'status-1' })
const view = atom({ plugin: 'nolook', key: 'view' } as const, 'screen' as 'screen' | 'picture')
const picture = atom({ plugin: 'nolook', key: 'picture' } as const, null, { shape: 'picture-1' })
const gen = atom({ plugin: 'nolook', key: 'gen' } as const, 0)

// The module's own: a reload starts them over, and the stream with them.
let frame: { file: string; v: number; w: number; h: number } | null = null
let control = ''
let refreshes = 0
let quitting = false
let streaming = false
let opened = false
// Where blit is refused (no graphics), a frame redraws the pane: at most this often.
let lastRedraw = 0
const REDRAW_EVERY = 2000

const NOLOOK = /(^|[\s;&|(/])nolook\s+(\S+)/

const clock = (at: number) => new Date(at * 1000).toTimeString().slice(0, 8)

async function setStatus($: EngineInterface, patch: Partial<Status>) {
  const cur = await read($, status)
  if (Object.entries(patch).every(([k, v]) => (cur as Record<string, unknown>)[k] === v)) return
  await update($, status, s => ({ ...s, ...patch }))
}

async function writeControl($: EngineInterface) {
  if (!control) return
  const { live } = await read($, status)
  await $.fs.write(control, JSON.stringify({ live, refresh: refreshes, quit: quitting }))
}

async function startStream($: EngineInterface) {
  if (streaming) {
    // Reopened before the last stream read its quit: keep it.
    if (quitting) {
      quitting = false
      await writeControl($)
    }
    return
  }
  streaming = true
  quitting = false
  const session = await $.session.id()
  void (async () => {
    let buf = ''
    try {
      const child = $.process.spawn({ argv: ['nolook', '--session', session, 'screen', '--stream'] })
      for await (const { stream, text } of child) {
        if (stream !== 'stdout') continue
        buf += text
        for (let i = buf.indexOf('\n'); i >= 0; i = buf.indexOf('\n')) {
          const line = buf.slice(0, i)
          buf = buf.slice(i + 1)
          if (line.trim()) await onLine($, line)
        }
      }
      if (!quitting) await setStatus($, { error: 'the mirror stopped; press r to start it again' })
    } catch {
      await setStatus($, { error: 'cannot start nolook: is it installed and on PATH?' })
    } finally {
      streaming = false
    }
  })()
}

async function stopStream($: EngineInterface) {
  quitting = true
  await writeControl($)
}

async function onLine($: EngineInterface, line: string) {
  let m: Record<string, any>
  try {
    m = JSON.parse(line)
  } catch {
    return
  }
  if ('control' in m) {
    control = m.control
    await writeControl($)
  } else if ('device' in m) await setStatus($, { device: m.device, error: null })
  else if ('app' in m) await setStatus($, { app: m.app ?? undefined })
  else if ('busy' in m) await setStatus($, { busy: m.busy })
  else if ('error' in m) await setStatus($, { error: m.error })
  else if ('frame' in m) await showFrame($, m)
  else if ('action' in m) await onAction($, m.action as Action)
}

async function showFrame($: EngineInterface, m: Record<string, any>) {
  const sameSize = frame !== null && frame.w === m.w && frame.h === m.h
  frame = { file: m.frame, v: m.v, w: m.w, h: m.h }
  const { live, error } = await read($, status)
  // While live, the header shows no time, so a frame redraws nothing but itself.
  if (!live || error) await setStatus($, { frameAt: m.at, error: null })
  if (sameSize) {
    const r = await $.ui.blit({ requestId: PANE, key: 'screen', source: { file: frame.file, format: 'png', generation: frame.v } })
    if (!('deny' in r)) return
    if (Date.now() - lastRedraw < REDRAW_EVERY) return
  }
  lastRedraw = Date.now()
  await update($, gen, n => n + 1)
}

async function onAction($: EngineInterface, a: Action) {
  await update($, actions, l => [...l, a].slice(-KEEP))
  if (a.picture) await showPicture($, a.picture)
  else await update($, view, () => 'screen')
  const what = `${a.verb}${subject(a) ? ' ' + subject(a) : ''}`
  if (a.guard?.length) $.ui.toast(`nolook: a guarded request went out after ${what}: ${(a.guard[0] ?? '').slice(0, 80)}`, { timeoutMs: 8000 })
  else if (a.error && /looks like a write action|is covered/.test(a.error)) $.ui.toast(`nolook refused ${what}: ${a.error.slice(0, 80)}`)
  $.ui.status(`nolook ${a.error ? '✗' : '·'} ${what}`.slice(0, 48))
}

async function showPicture($: EngineInterface, path: string) {
  // The terminal draws PNGs only; nolook's shots are JPEGs.
  const ran = await $.process.run(['nolook', 'topng', path, '--width', '900'], { timeoutMs: 15000 })
  const [file, size] = ran.stdout.trim().split('\n').pop()?.split(/\s+/) ?? []
  const [width, height] = (size ?? '').split('x').map(Number)
  if (ran.exitCode !== 0 || !file || !width || !height) return
  await update($, picture, () => ({ file, name: path.split('/').pop() ?? '', width, height }))
  await update($, view, () => 'picture')
}

async function openPane($: EngineInterface) {
  opened = true
  await $.ui.open({ id: PANE, title: 'nolook' })
  await startStream($)
}

async function setLive($: EngineInterface, isLive: boolean) {
  await setStatus($, { live: isLive })
  await writeControl($)
}

async function refresh($: EngineInterface) {
  if (!streaming) return startStream($)
  refreshes += 1
  await writeControl($)
}

function subject(a: Action): string {
  const t = a.target
  if (t?.label) return `"${t.label}"`
  if (t?.id) return `#${t.id}`
  if (!a.query) return ''
  return ['key', 'open', 'launch', 'flow'].includes(a.verb) ? a.query : `"${a.query}"`
}

/** What came of an action, and in which tone: red for errors, yellow for guard hits. */
function outcome(a: Action): { text: string; tone?: 'red' | 'yellow' | 'green' } {
  if (a.error) return { text: `✗ ${a.error}`, tone: 'red' }
  if (a.guard?.length) return { text: `! guard: ${a.guard[0]}`, tone: 'yellow' }
  if (a.verb === 'flow') return { text: `✓ ${a.steps ?? 0} steps · ${a.took ?? 0}s`, tone: 'green' }
  if (a.picture) return { text: `→ ${a.verb === 'motion' ? 'key frames' : 'picture'}` }
  const change = a.change === 'screen' ? 'new screen' : a.change ? `${a.change} changed` : 'no change'
  const time = a.settled == null ? 'still moving' : `${a.settled}s`
  return { text: `→ ${change} · ${time}` }
}

function fit(w: number, h: number, maxCols: number, maxRows: number) {
  let c = maxCols
  let r = Math.round((c * h) / w / CELL_ASPECT)
  if (r > maxRows) {
    r = maxRows
    c = Math.max(4, Math.round((r * w * CELL_ASPECT) / h))
  }
  return { columns: Math.max(1, Math.min(255, c)), rows: Math.max(1, Math.min(255, r)) }
}

/** Where the mirror and the actions go in a body of `cols` by `body` cells. */
export function layout(cols: number, body: number, fw: number, fh: number, pictures: boolean) {
  if (!pictures) return { stacked: false, size: { columns: 1, rows: 1 }, listRows: body }
  // The actions keep SIDE_MIN columns; the mirror takes the rest, as tall as that allows.
  const beside = cols - SIDE_MIN - 4
  if (beside >= MIRROR_MIN) {
    const size = fit(fw, fh, beside, body - 2)
    return { stacked: false, size, listRows: size.rows + 2 }
  }
  const size = fit(fw, fh, cols - 2, Math.round(body * 0.62) - 2)
  return { stacked: true, size, listRows: body - (size.rows + 2) - 1 }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'device',
      description: 'Show the device nolook drives in a pane (`/device pause` stops the live mirror, `/device live` starts it)',
      argumentHint: '[live|pause]',
    })
    const started = await next(e)
    // A reload ends the stream; a pane that stayed up needs it again.
    if ((await $.ui.panes()).some(p => p.id === PANE)) {
      opened = true
      await startStream($)
    }
    return started
  })

  on('command.run', { command: 'device' }, async ($, e) => {
    const arg = (e.args ?? '').trim()
    if (arg === 'live') await setLive($, true)
    else if (arg === 'pause' || arg === 'still') await setLive($, false)
    await openPane($)
    const { live } = await read($, status)
    return { text: `nolook pane: ${live ? 'live' : 'paused'}.` }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (!opened && NOLOOK.test(e.command) && ran.deny === undefined) void openPane($).catch(() => {})
    return ran
  })

  on('ui.close', async ($, e, next) => {
    if (e.id === PANE) {
      opened = false
      void stopStream($).catch(() => {})
    }
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Image, Button } = $.ui.resolve(e) as any
    // Only a terminal draws pictures: elsewhere the pane is the actions alone.
    const pictures = e.surface === 'terminal'
    await read($, gen)
    const st = await read($, status)
    const list = await read($, actions)
    const shown = await read($, view)
    const pic = await read($, picture)
    const cols = Math.max(20, e.props.bodyColumns)
    const body = Math.max(10, (e.props.scroll?.bodyRows ?? e.viewport?.rows ?? 40) - 2)

    const tone = st.error ? 'red' : st.busy ? 'yellow' : st.live ? 'green' : 'gray'
    const state = st.error
      ? 'offline'
      : st.busy
        ? `held · ${st.busy}`
        : st.live
          ? 'live'
          : `paused${st.frameAt ? ' · ' + clock(st.frameAt) : ''}`

    const header = (
      <Box justifyContent="space-between">
        <Text wrap="truncate-end">
          <Text color={tone}>● </Text>
          <Text bold>{st.device ?? 'no device'}</Text>
          {st.app ? <Text dimColor>{` · ${st.app}`}</Text> : null}
        </Text>
        <Box gap={2} flexShrink={0}>
          <Text color={st.busy ? 'yellow' : undefined} dimColor={!st.busy}>{state}</Text>
          {pic && pictures ? (
            <Button key="view" plain hotkey="p" label={shown === 'picture' ? 'screen' : 'picture'}
              onPress={() => void update($, view, v => (v === 'picture' ? 'screen' : 'picture'))} />
          ) : null}
          <Button key="refresh" plain hotkey="r" label="refresh" onPress={() => void refresh($)} />
          <Button key="live" plain hotkey="l" label={st.live ? 'pause' : 'live'} onPress={() => void setLive($, !st.live)} />
        </Box>
      </Box>
    )

    if (shown === 'picture' && pic && pictures) {
      return (
        <Box flexDirection="column" gap={1}>
          {header}
          <Box flexDirection="column">
            <Text dimColor wrap="truncate-end">{pic.name}</Text>
            <Image key="picture" source={{ file: pic.file, format: 'png' }} {...fit(pic.width, pic.height, cols, body - 1)} alt={pic.name} />
          </Box>
        </Box>
      )
    }

    const { stacked, size, listRows } = layout(cols, body, frame?.w ?? 9, frame?.h ?? 20, pictures)

    const mirror = (
      <Box borderStyle="round" borderColor={st.busy ? 'yellow' : undefined} borderDimColor={!st.busy} flexShrink={0}
        alignSelf={stacked ? 'center' : 'flex-start'}>
        {frame ? (
          <Image key="screen" source={{ file: frame.file, format: 'png', generation: frame.v }} {...size} alt="device screen" />
        ) : (
          <Box width={size.columns} height={size.rows} alignItems="center" justifyContent="center">
            <Text dimColor wrap="wrap">{st.error ?? 'connecting…'}</Text>
          </Box>
        )}
      </Box>
    )

    const picRows = pic ? 2 : 0
    const fits = Math.max(0, Math.floor((listRows - picRows) / 2))
    const side = (
      <Box flexDirection="column" flexGrow={1} height={listRows} justifyContent="flex-end">
        {list.length === 0 ? <Text dimColor>Nothing done yet.</Text> : null}
        {list.slice(-fits).map(a => {
          const o = outcome(a)
          return (
            <Box flexDirection="column">
              <Text wrap="truncate-end">
                <Text dimColor>{`${clock(a.at)} `}</Text>
                <Text bold>{a.verb}</Text>
                {subject(a) ? ` ${subject(a)}` : ''}
              </Text>
              <Text wrap="truncate-end" color={o.tone} dimColor={!o.tone}>{`  ${o.text}`}</Text>
            </Box>
          )
        })}
        {pic ? (
          <Box marginTop={1}>
            <Text dimColor wrap="truncate-end">{`▣ ${pic.name}`}</Text>
          </Box>
        ) : null}
      </Box>
    )

    return (
      <Box flexDirection="column" gap={1}>
        {header}
        <Box flexDirection={stacked ? 'column' : 'row'} gap={stacked ? 1 : 2}>
          {pictures ? mirror : null}
          {side}
        </Box>
      </Box>
    )
  })
}
