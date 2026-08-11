import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ReshipmentFormView.vue', import.meta.url), 'utf8')

test('reshipment form resolves a typed address through the backend', () => {
  assert.match(source, /new URLSearchParams\(\{ city: query, address: query \}\)/)
  assert.match(source, /\/api\/v1\/reshipment\/nearest-pickup/)
  assert.match(source, /addressDebounce = setTimeout\(\(\) => resolveTypedAddress\(requestId\), 500\)/)
})

test('production form does not depend on a browser-side DaData key', () => {
  assert.doesNotMatch(source, /VITE_DADATA_KEY/)
  assert.doesNotMatch(source, /suggestions\.dadata\.ru/)
})
