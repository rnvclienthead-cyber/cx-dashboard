import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/QuestionsView.vue', import.meta.url), 'utf8')

test('Ozon questions use the same public-answer workflow as Yandex Market', () => {
  assert.match(source, /'ozon:question': `\/ozon\/questions\/\$\{id\}\/answer`/)
  assert.match(source, /WB · ЯМ · Ozon — ответ публикуется на карточке товара/)
  assert.match(source, /<option value="ozon">Ozon<\/option>/)
})

test('question queue keeps private reply exclusive to Wildberries', () => {
  assert.match(source, /function isWbQuestion\(item\)/)
  assert.match(source, /v-if="isWbQuestion\(item\)" @click="sendReply\(item, 'private'\)"/)
})

test('quality clarification copy is marketplace-neutral', () => {
  assert.match(source, /На маркетплейс он не отправляется/)
  assert.doesNotMatch(source, /На WB\/ЯМ он не отправляется/)
})
