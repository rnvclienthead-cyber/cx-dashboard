import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/composables/useCxTemplates.js', import.meta.url), 'utf8')

test('paint-holes chip stays hidden unless the review is about paint holes', () => {
  assert.match(src, /function looksLikePaintHoles/)
  assert.match(src, /Технологические отверстия/)
  assert.match(src, /paint_holes/)
})
