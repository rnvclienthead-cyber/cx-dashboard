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

test('clarification button follows questions access, not cs_manager role name', () => {
  assert.match(source, /const canRequestClarification = computed\(\(\) =>/)
  assert.match(source, /permissionsStore\.can\('cx_responses'\)/)
  assert.doesNotMatch(
    source,
    /const canRequestClarification = computed\(\(\) =>\s*permissionsStore\.isAdmin \|\| isCsManager\.value/,
  )
})

test('cs manager can filter questions answered by quality', () => {
  assert.match(source, /function hasQualityAnswer\(item\)/)
  assert.match(source, /item\?\.clarification\?\.status === 'answered'/)
  assert.match(source, /showAnsweredOnly/)
  assert.match(source, /Получен ответ/)
  assert.match(source, /if \(showAnsweredOnly\.value\) \{/)
  assert.match(source, /list = list\.filter\(hasQualityAnswer\)/)
})

test('question cards link image and article to marketplace product pages', () => {
  assert.match(source, /function productCardUrl\(item\)/)
  assert.match(source, /https:\/\/www\.wildberries\.ru\/catalog\/\$\{nm\}\/detail\.aspx/)
  assert.match(source, /question_wb_nm_id: i\.question_wb_nm_id \? Number\(i\.question_wb_nm_id\) : null/)
  assert.match(source, /item\.nm_id \|\| item\.question_wb_nm_id/)
  assert.match(source, /return String\(item\.question_product_url \|\| ''\)\.trim\(\)/)
  assert.match(source, /https:\/\/market\.yandex\.ru\/card\/slug\/\$\{encodeURIComponent\(sku\)\}/)
  assert.match(source, /ymProductUrl\(item\.question_market_sku, item\.sku\)/)
  assert.match(source, /market\.yandex\.ru\/search\?text=\$\{encodeURIComponent\(offer\)\}/)
  assert.doesNotMatch(source, /https:\/\/market\.yandex\.ru\/product--\//)
  assert.match(source, /item\.product_photo && productCardUrl\(item\)/)
  assert.match(source, /<a v-if="productCardUrl\(item\)"/)
})

test('questions can open the same internal product card as chats', () => {
  assert.match(source, /function productCardRequestFor\(item\)/)
  assert.match(source, /async function toggleProductCard\(item\)/)
  assert.match(source, /\/api\/v1\/chat-bot\/product-card\?\$\{params\}/)
  assert.match(source, /params\.set\('article', req\.article\)/)
  assert.match(source, /params\.set\('nm_id', String\(req\.nmId\)\)/)
  assert.match(source, /title="Карточка товара"/)
  assert.match(source, /Загружаем карточку товара…/)
  assert.match(source, /function productFastenersLine\(card\)/)
  assert.match(source, /Винты:/)
})

test('queue can search already answered similar questions', () => {
  assert.match(source, /function loadSimilar\(item\)/)
  assert.match(source, /\/questions\/similar/)
  assert.match(source, /Похожие вопросы и ответы/)
  assert.match(source, /item\._reply = similarCurrent\(g\)\.answer/)
  assert.match(source, /Другой ответ/)
  assert.match(source, /function cycleSimilar/)
  assert.match(source, /i\.mp === mp/)
})
