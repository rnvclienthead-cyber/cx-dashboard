import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/views/AITaggingView.vue', import.meta.url), 'utf8')

test('defect tagging stops on error even if some rows already tagged', () => {
  assert.match(src, /if \(pollTicks >= 2 && taggingStoppedWithError\(\)\)/)
  assert.doesNotMatch(src, /processed === 0 && pollTicks >= 2 && taggingStoppedWithError/)
})

test('defect tagging gives up if the log is stale for about 3 minutes', () => {
  assert.match(src, /TAGGING_STALE_TICKS = 36/)
  assert.match(src, /staleTicks >= TAGGING_STALE_TICKS/)
  assert.match(src, /Разметка остановилась\. Нажми запуск ещё раз/)
})

test('run button shows in-progress labelling, not a stuck send', () => {
  assert.match(src, /Идёт разметка\.\.\./)
  assert.doesNotMatch(src, /isProcessing \? 'Отправка\.\.\.'/)
})
