import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/views/FAQView.vue', import.meta.url), 'utf8')

test('FAQ loads live reshipment SKU list from the faq API', () => {
  assert.match(src, /\/api\/v1\/faq\/reshipment-skus/)
  assert.match(src, /Кому отправляем детали/)
  assert.match(src, /Только возврат/)
  assert.match(src, /WB, Ozon и Яндекс Маркет/)
  assert.doesNotMatch(src, /На Ozon детали не шлём/)
  assert.doesNotMatch(src, /\/api\/v1\/reshipment-skus/)
})
