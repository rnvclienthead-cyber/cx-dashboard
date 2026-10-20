import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ExecutiveDashboardView.vue', import.meta.url), 'utf8')

test('ozon carrier tile sits before CDEK in the reshipment funnel', () => {
  const ozon = source.indexOf('Одобрено Ozon')
  const cdek = source.indexOf('Одобрено СДЭК')
  assert.notEqual(ozon, -1)
  assert.notEqual(cdek, -1)
  assert.ok(ozon < cdek)
  assert.match(source, /approved_ozon/)
  assert.match(source, /approved_ozon_cost/)
  assert.match(source, /СДЭК \+ Яндекс \+ Ozon/)
})
