import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/views/PPMView.vue', import.meta.url), 'utf8')

test('PPM factory rule starts in August 2026 with act tags only', () => {
  assert.match(src, /PPM_FACTORY_FROM = '2026-08-01'/)
  assert.match(src, /PPM_FACTORY_CATS = \[1, 2, 4, 5, 7, 9\]/)
  assert.match(src, /if \(dateStr >= PPM_FACTORY_FROM\) return hasCat\(PPM_FACTORY_CATS\)/)
})

test('YM and Ozon use act tags for all periods', () => {
  assert.match(src, /if \(plat === 'ym' \|\| plat === 'ozon'\) return hasCat\(PPM_FACTORY_CATS\)/)
})

test('PPM keeps old WB tag set before August', () => {
  assert.match(src, /hasCat\(\[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13\]\)/)
})

test('SKU act and chart use only factory groups, no others bucket', () => {
  assert.match(src, /Shortage.*ids: \[1, 2\]/)
  assert.match(src, /Damage.*ids: \[4, 5\]/)
  assert.match(src, /Flimsy.*ids: \[7, 9\]/)
  assert.doesNotMatch(src, /key: ["']others["']/)
  assert.doesNotMatch(src, /categoryPhotosState\.value\['others'\]/)
})
