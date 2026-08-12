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

test('wb nonreturnable logic is embedded into return theme in playbook', () => {
  assert.match(source, /return list\.filter\(t => t\.theme_key !== 'nevozvratny'\)/)
  assert.match(source, /t\.theme_key === 'vozvrat'/)
  assert.match(source, /система автоматически подставляет текст о невозвратности WB/)
  assert.match(source, /branch_label: 'WB · невозвратный товар'/)
  assert.match(source, /nonreturnableCatsLabel/)
  assert.match(source, /\/api\/v1\/nonreturnable-cats/)
})

test('product card is visible in chat and supports wb nm_id fallback', () => {
  assert.match(source, /title="Карточка товара"/)
  assert.match(source, /Загружаем карточку товара…/)
  assert.match(source, /Загружено из локальной базы/)
  assert.match(source, /const productCardRequest = computed\(\(\) =>/)
  assert.match(source, /const chatId = String\(chatItem\.value\?\.id \|\| ''\)\.trim\(\)/)
  assert.match(source, /if \(mp === 'wb' && Number\.isFinite\(nmId\) && nmId > 0\)/)
  assert.match(source, /params\.set\('nm_id', String\(productCardRequest\.value\.nmId\)\)/)
  assert.match(source, /params\.set\('chat_id', productCardRequest\.value\.chatId\)/)
  assert.doesNotMatch(source, /Карточка товара из Яндекс Маркета/)
})
