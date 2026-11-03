import { expect, test } from 'claude-code/testing'

import type { Action } from '../types'
import { layout } from './register'

const pane = (bodyColumns: number) =>
  ({
    plugin: 'nolook',
    component: 'Pane',
    requestId: 'nolook',
    props: {
      title: 'nolook',
      isFocused: false,
      bodyColumns,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: 40 },
      view: {},
    },
  }) as const

const TAP: Action = {
  at: 1793736272,
  verb: 'tap',
  query: '设置',
  target: { role: 'btn', label: '设置', id: '', bounds: [0, 0, 100, 100] },
  settled: 0.4,
  change: 'screen',
}
const REFUSED: Action = { at: 1793736280, verb: 'tap', query: '赞', error: 'btn "赞" looks like a write action (\'赞\'); not tapped.' }

// The values the pane reads, as the engine holds them: under their shape tags.
function seed(on: any) {
  const held: Record<string, unknown> = {
    status: { shape: 'status-1', value: { live: true, device: 'emulator-5556', app: 'oolong.client' } },
    actions: { shape: 'actions-1', value: [TAP, REFUSED] },
  }
  on('state.get', (_: unknown, e: { key: string }) => (e.key in held ? { value: { value: held[e.key], version: 1 } } : { value: {} }))
}

test('the pane names the device and says what each action did', async ($, on) => {
  seed(on)
  for (const cols of [70, 30]) {
    const ui = await $.ui.mount({ ...pane(cols), surface: 'terminal' })
    expect((await ui.find({ type: 'Text', text: /emulator-5556/ }))?.text).toContain('oolong.client')
    expect(await ui.find({ type: 'Text', text: /→ new screen · 0\.4s/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /✗ .*looks like a write action/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /connecting…/ })).toBeDefined()
    await ui.unmount()
  }
})

test('without pictures the pane is the actions alone', async ($, on) => {
  seed(on)
  for (const surface of ['desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...pane(50), surface })
    expect(await ui.find({ type: 'Text', text: /tap/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /connecting…/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('nothing done yet says so', async $ => {
  const ui = await $.ui.mount({ ...pane(60), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'Nothing done yet.' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /no device/ })).toBeDefined()
  await ui.unmount()
})

test('from 46 columns the actions sit beside the mirror', async () => {
  for (const cols of [50, 60, 70]) {
    const l = layout(cols, 40, 360, 800, true)
    expect(l.stacked).toBe(false)
    expect(l.size.columns + 2 + 2 + 26).toBeLessThanOrEqual(cols)
    expect(l.size.rows).toBeLessThanOrEqual(38)
  }
  expect(layout(40, 40, 360, 800, true).stacked).toBe(true)
})
