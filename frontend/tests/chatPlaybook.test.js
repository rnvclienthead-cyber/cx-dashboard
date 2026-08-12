import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')

test('empty-template hint is honest about manager and fallback behavior', () => {
  assert.match(source, /function emptyVariantHint\(theme\)/)
  assert.match(source, /тема сразу уходит менеджеру/)
  assert.match(source, /Менеджеру придётся писать ответ вручную или опираться на ИИ-черновик/)
  assert.match(source, /бот попробует запасной ответ \(ИИ\/FAQ, если он есть\)/)
})

test('wb nonreturnable theme shows active category list in playbook', () => {
  assert.match(source, /t\.theme_key === 'nevozvratny' && pbMp === 'wb'/)
  assert.match(source, /Автоправило WB по невозвратности/)
  assert.match(source, /nonreturnableCatsLabel/)
  assert.match(source, /\/api\/v1\/nonreturnable-cats/)
})
