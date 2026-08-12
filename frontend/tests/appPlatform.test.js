import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

test('default platform selection uses first accessible marketplace outside reshipment', () => {
  assert.match(source, /const specificPlatforms = computed\(\(\) =>/)
  assert.match(source, /const firstAvailablePlatform = computed\(\(\) =>/)
  assert.match(source, /const prefersAllPlatform = computed\(\(\) => route\.path === '\/reshipment'\)/)
  assert.match(source, /if \(prefersAllPlatform\.value\)/)
  assert.match(source, /if \(current === 'all' \|\| !specificPlatforms\.value\.includes\(current\)\)/)
  assert.match(source, /platformStore\.setPlatform\(firstAvailablePlatform\.value\)/)
})
