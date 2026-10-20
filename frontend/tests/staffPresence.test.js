import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const admin = readFileSync(new URL('../src/views/AdminView.vue', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const control = readFileSync(new URL('../src/views/CxControlView.vue', import.meta.url), 'utf8')

test('admin panel shows live presence and polls every 2 seconds', () => {
  assert.match(admin, /Сейчас на сайте/)
  assert.match(admin, /На сайте сейчас никого нет/)
  assert.match(admin, /loadPresence/)
  assert.match(admin, /setInterval\(loadPresence, 2000\)/)
  assert.match(admin, /presenceLabel/)
})

test('activity ping is sent only after real input, not every minute', () => {
  assert.match(app, /markActivity/)
  assert.match(app, /pointerdown/)
  assert.match(app, /flushActivity/)
  assert.match(app, /setInterval\(flushActivity, 2000\)/)
  assert.doesNotMatch(app, /setInterval\(pingActivity, 60000\)/)
})

test('cx control has replies dashboard tab', () => {
  assert.match(control, /tab='replies'/)
  assert.match(control, /\/api\/v1\/cx-control\/replies/)
  assert.match(control, /Автоответы в чатах/)
})
