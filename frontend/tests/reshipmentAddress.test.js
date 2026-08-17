import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ReshipmentFormView.vue', import.meta.url), 'utf8')

test('reshipment form loads DaData suggestions through the backend', () => {
  assert.match(source, /\/api\/v1\/reshipment\/suggest-address/)
  assert.match(source, /fetchAddressSuggestions/)
  assert.match(source, /pickSuggestion/)
})

test('reshipment form confirms selected address via nearest-pickup', () => {
  assert.match(source, /\/api\/v1\/reshipment\/nearest-pickup/)
  assert.match(source, /resolveTypedAddress/)
})

test('production form does not depend on a browser-side DaData key', () => {
  assert.doesNotMatch(source, /VITE_DADATA_KEY/)
  assert.doesNotMatch(source, /suggestions\.dadata\.ru/)
})
