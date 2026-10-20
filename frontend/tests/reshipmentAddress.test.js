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

test('reshipment form blocks non-Russia addresses with a clear message', () => {
  assert.match(source, /outside_russia/)
  assert.match(source, /только по России/)
  assert.match(source, /В другие страны сейчас не отправляем/)
})

test('reshipment form blocks city-only addresses without a street', () => {
  assert.match(source, /need_street/)
  assert.match(source, /Укажите улицу и номер дома/)
  assert.match(source, /addressImprecise/)
})

test('reshipment form closes when the return is already approved', () => {
  assert.match(source, /token_return_approved/)
  assert.match(source, /Возврат уже одобрен/)
})
