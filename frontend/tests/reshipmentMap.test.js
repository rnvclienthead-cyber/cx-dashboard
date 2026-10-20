import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ReshipmentView.vue', import.meta.url), 'utf8')

test('staff map loads Yandex key from backend, not from a missing Vite env', () => {
  assert.match(source, /\/api\/v1\/reshipment\/maps-config/)
  assert.doesNotMatch(source, /Нет VITE_YANDEX_MAPS_KEY/)
})
