import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Entry } from '../types'

// The device as you see it, beside the chat. Its pictures go to the
// terminal only: the model never reads them, so the pane costs no context.

const PANE = 'nolook'
const LOG_ROWS = 6
// A terminal cell is about twice as tall as it is wide.
const CELL_ASPECT = 2

const log = atom({ plugin: 'nolook', key: 'log' } as const, [], { shape: 'log-1' })
const gen = atom({ plugin: 'nolook', key: 'gen' } as const, 0)
const live = atom({ plugin: 'nolook', key: 'live' } as const, false)
const picLabel = atom({ plugin: 'nolook', key: 'picLabel' } as const, '')

const NOLOOK = /(^|[\s;&|(/])nolook\s+(\S+)/
const PICTURE = /(\/\S+\/(?:shots|motion)\/\S+\.(?:jpg|png))/g

// Pixels stay out of $.state: they are large and only this module draws them.
let mirror: { png: string; width: number; height: number; at: string } | null = null
let picture: { png: string; width: number; height: number } | null = null
let refreshing = false
let opened = false
let ticker: { cancel: () => void } | null = null
const tag = Math.random().toString(36).slice(2, 8)
const mirrorPath = `/tmp/nolook-pane-${tag}.png`
const picturePath = `/tmp/nolook-pane-${tag}-pic.png`

async function clockText($: EngineInterface) {
  return new Date(await $.clock.now()).toTimeString().slice(0, 8)
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

function decodeBase64(text: string): number[] {
  const out: number[] = []
  let bits = 0
  let acc = 0
  for (const ch of text) {
    const v = B64.indexOf(ch)
    if (v < 0) break
    acc = (acc << 6) | v
    bits += 6
    if (bits >= 8) {
      bits -= 8
      out.push((acc >> bits) & 0xff)
    }
  }
  return out
}

async function pngSize($: EngineInterface, path: string) {
  // Width and height sit in the PNG header (IHDR), bytes 16–23.
  const { base64 } = await $.fs.read(path, { as: 'bytes' })
  const head = decodeBase64(base64.slice(0, 32))
  const u32 = (i: number) => (((head[i] ?? 0) << 24) | ((head[i + 1] ?? 0) << 16) | ((head[i + 2] ?? 0) << 8) | (head[i + 3] ?? 0)) >>> 0
  return { png: base64, width: u32(16), height: u32(20) }
}

async function refresh($: EngineInterface) {
  if (refreshing) return
  refreshing = true
  try {
    const ran = await $.process.run(['nolook', 'shot', '--png', '--width', '360', '-o', mirrorPath], { timeoutMs: 15000 })
    if (ran.exitCode === 0) {
      mirror = { ...(await pngSize($, mirrorPath)), at: await clockText($) }
      await update($, gen, n => n + 1)
    }
  } catch {
    // No device, or nolook missing: the pane says so from what it has.
  } finally {
    refreshing = false
  }
}

async function showPicture($: EngineInterface, path: string) {
  const ran = await $.process.run(['nolook', 'topng', path, picturePath, '--width', '900'], { timeoutMs: 15000 })
  if (ran.exitCode !== 0) return
  picture = await pngSize($, picturePath)
  await update($, picLabel, () => path.split('/').pop() ?? '')
  await update($, gen, n => n + 1)
}

async function setLive($: EngineInterface, on_: boolean) {
  await update($, live, () => on_)
  ticker?.cancel()
  ticker = on_ ? $.clock.every(2000, () => void refresh($)) : null
  if (on_) void refresh($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'device',
      description: 'Show the device nolook drives in a pane (`/device live` mirrors it every 2s, `/device still` stops)',
      argumentHint: '[live|still]',
    })
    return next(e)
  })

  on('command.run', { command: 'device' }, async ($, e) => {
    const arg = (e.args ?? '').trim()
    await $.ui.open({ id: PANE, title: 'nolook' })
    opened = true
    if (arg === 'live') await setLive($, true)
    else if (arg === 'still') await setLive($, false)
    else void refresh($)
    return { text: arg === 'live' ? 'nolook pane: live mirror on.' : arg === 'still' ? 'nolook pane: live mirror off.' : 'nolook pane opened.' }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const m = NOLOOK.exec(e.command)
    const ran = await next(e)
    if (!m || ran.deny !== undefined) return ran

    const res = ran.result as { stdout?: string; stderr?: string } | undefined
    const out = res?.stdout ?? ''
    const err = res?.stderr ?? ''
    const text = `${out}\n${err}`
    const kind: Entry['kind'] = /⚠ guard/.test(text)
      ? 'guard'
      : /looks like a write action|is covered/.test(text)
        ? 'refused'
        : /^error:/m.test(err) || ran.isError
          ? 'error'
          : 'ok'
    const head = (kind === 'error' || kind === 'refused' ? err.split('\n').find(l => l.startsWith('error:')) : undefined)
      ?? out.split('\n').find(l => l.trim()) ?? ''
    const cmd = e.command.slice(m.index + (m[1] ?? '').length).split('\n')[0]?.slice(0, 60) ?? ''
    const entry: Entry = { cmd, head: head.slice(0, 140), kind, at: await clockText($) }
    await update($, log, l => [...l, entry].slice(-50))

    if (kind === 'guard') $.ui.toast(`nolook: a guarded write happened — ${head.slice(0, 80)}`, { timeoutMs: 8000 })
    if (kind === 'refused') $.ui.toast(`nolook refused a tap: ${head.replace(/^error: /, '').slice(0, 80)}`)
    $.ui.status(`nolook ${kind === 'ok' ? '·' : kind} ${cmd.slice(0, 30)}`)

    if (!opened) {
      opened = true
      void $.ui.open({ id: PANE, title: 'nolook' })
    }
    const pics = [...text.matchAll(PICTURE)].map(x => x[1] ?? '').filter(x => x)
    const sheet = pics.find(p => p.endsWith('-motion.jpg')) ?? pics[pics.length - 1]
    if (sheet && !sheet.startsWith('/tmp/nolook-pane-')) void showPicture($, sheet)
    // Read-only commands change nothing worth a new frame.
    if (!['flows', 'topng', 'get', 'log', 'motion-compare'].includes(m[2] ?? '')) void refresh($)
    return ran
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Image, Button } = $.ui.resolve(e) as any
    await read($, gen)
    const entries = await read($, log)
    const isLive = await read($, live)
    const label = await read($, picLabel)
    const cols = Math.max(10, e.props.bodyColumns)
    const rows = Math.max(12, e.viewport?.rows ?? 40)

    const fit = (w: number, h: number, maxRows: number) => {
      let c = cols
      let r = Math.round((c * h) / w / CELL_ASPECT)
      if (r > maxRows) {
        r = maxRows
        c = Math.max(4, Math.round((r * w * CELL_ASPECT) / h))
      }
      return { columns: Math.min(255, c), rows: Math.max(1, Math.min(255, r)) }
    }

    const picRows = picture ? Math.min(Math.round(rows * 0.3), fit(picture.width, picture.height, 999).rows) : 0
    const mirrorRows = rows - 3 - LOG_ROWS - (picture ? picRows + 1 : 0)

    return (
      <Box flexDirection="column">
        <Box>
          <Text dimColor>{mirror ? `device · ${mirror.at} ` : 'device · no frame yet '}</Text>
          <Button key="refresh" label="refresh" hotkey="r" onPress={() => void refresh($)} />
          <Text> </Text>
          <Button key="live" label={isLive ? 'live: on' : 'live: off'} hotkey="l" onPress={() => void setLive($, !isLive)} />
        </Box>
        {mirror ? (
          <Image key="mirror" source={{ png: mirror.png }} {...fit(mirror.width, mirror.height, Math.max(4, mirrorRows))} alt="device screen" />
        ) : (
          <Text dimColor>Run a nolook command, or press r.</Text>
        )}
        {picture && <Text dimColor>{`last picture · ${label}`}</Text>}
        {picture && <Image key="picture" source={{ png: picture.png }} {...fit(picture.width, picture.height, picRows)} alt={label || 'picture'} />}
        {entries.slice(-LOG_ROWS).map(x => (
          <Text dimColor={x.kind === 'ok'} color={x.kind === 'ok' ? undefined : x.kind === 'error' ? 'red' : 'yellow'} wrap="truncate-end">
            {`${x.at} ${x.kind === 'ok' ? '✓' : x.kind === 'error' ? '✗' : '!'} ${x.cmd} — ${x.head}`}
          </Text>
        ))}
      </Box>
    )
  })
}
