import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/RatingsView.vue', import.meta.url), 'utf8')

test('Ozon ratings page loads API data instead of hard empty stub', () => {
  assert.match(source, /\/api\/ratings\?platform=\$\{platformStore\.platform\}/)
  assert.doesNotMatch(source, /Витринный рейтинг Ozon не собирается/)
  assert.doesNotMatch(source, /if \(platformStore\.platform === 'ozon'\) \{\s*ratingsData\.value = \[\]/)
  assert.match(source, /Пока нет снимка рейтингов Ozon/)
})
