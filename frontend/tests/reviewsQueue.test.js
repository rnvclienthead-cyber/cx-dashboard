import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/views/ReviewsView.vue', import.meta.url), 'utf8')

test('queue search stays visible when the table is empty', () => {
  const filterIdx = src.indexOf('placeholder="Текст отзыва, артикул…"')
  const emptyIdx = src.indexOf('Очередь пуста или ничего не найдено по фильтру')
  assert.notEqual(filterIdx, -1)
  assert.notEqual(emptyIdx, -1)
  assert.ok(filterIdx < emptyIdx, 'search field must be rendered before the empty-state branch')
  assert.doesNotMatch(
    src.slice(emptyIdx - 400, emptyIdx),
    /v-if="filteredItems.length === 0"[\s\S]*placeholder="Текст отзыва/,
  )
})

test('queue filters use rounded-lg like the rest of the site', () => {
  const block = src.slice(
    src.indexOf('Фильтры — всегда на виду'),
    src.indexOf('Загружаем отзывы'),
  )
  assert.match(block, /rounded-lg/)
  assert.doesNotMatch(block, /border-gray-200 rounded px-/)
})

test('reviews have per-marketplace auto-reply toggles', () => {
  assert.match(src, /review-auto-settings/)
  assert.match(src, /toggleAutoMp/)
  assert.match(src, /toggleAutoScenario/)
  assert.match(src, /toggleAllAutoScenarios/)
  assert.match(src, /key: 'settings'/)
  for (const mp of ['wb', 'ym', 'ozon']) {
    assert.match(src, new RegExp(`mp: '${mp}'`))
  }
  for (const sc of ['thanks_5', 'thanks_4', 'sorry']) {
    assert.match(src, new RegExp(`key: '${sc}'`))
  }
})

test('auto-reply toggles are admin-only, template edits also for the CX manager', () => {
  assert.match(src, /permissionsStore.isAdmin \|\| permissionsStore.role === 'cs_manager'/)
  assert.match(src, /activeSection === 'settings' && permissionsStore.isAdmin/)
  assert.match(src, /:read-only="templatesReadOnly"/)
  assert.match(src, /key: 'templates', label: 'Шаблоны'/)
  assert.match(src, /function openReviewScenarioEditor/)
})

test('stats request reviews only', () => {
  assert.match(src, /stats\?days=\$\{statsDays\.value\}&item_type=review/)
})

test('archive has the same search and rating filters as the queue', () => {
  const block = src.slice(
    src.indexOf('Фильтры архива'),
    src.indexOf('Архив пуст'),
  )
  assert.match(block, /v-model="aText"/)
  assert.match(block, /placeholder="Текст отзыва, артикул…"/)
  assert.match(block, /toggleArchiveRating/)
  assert.match(block, /aRatings.includes\(n\)/)
  assert.match(block, /v-for="n in \[5,4,3,2,1\]"/)
  assert.match(block, /v-model="aSku"/)
  assert.match(block, /v-model="aHasText"/)
  assert.match(block, /v-model="aStatus"/)
})

test('archive search is sent to the server as q and ratings', () => {
  assert.match(src, /params\.set\('q', aText\.value\.trim\(\)\)/)
  assert.match(src, /params\.set\('ratings', aRatings\.value\.join\(','\)\)/)
  assert.match(src, /aSku\.value = ''; aText\.value = ''; aRatings\.value = \[\]/)
})

test('archive distinguishes WB closed-without-reply from real answers', () => {
  assert.match(src, /На WB без ответа/)
  assert.match(src, /archiveStatusLabel/)
  assert.match(src, /function archiveStatusKind/)
})

test('WB review photos come from nm_id card, not Ozon SKU', () => {
  assert.match(src, /reviewProductPhoto\(item\)/)
  assert.doesNotMatch(src, /<img v-if="item\.product_photo"/)
})

test('queue rating filter allows several stars at once', () => {
  assert.match(src, /toggleQueueRating/)
  assert.match(src, /fRatings\.value\.length/)
  assert.match(src, /const fRatings\s+= ref\(\[\]\)/)
})

test('queue can filter updated vs new reviews', () => {
  const block = src.slice(
    src.indexOf('Фильтры — всегда на виду'),
    src.indexOf('Загружаем отзывы'),
  )
  assert.match(block, /v-model="fKind"/)
  assert.match(block, /option value="updated">Обновлённые/)
  assert.match(block, /option value="new">Новые/)
  assert.match(src, /fKind\.value === 'updated'/)
  assert.match(src, /item\.update_label/)
  assert.match(src, /Было:/)
  assert.match(src, /Доотправка #/)
  assert.match(src, /updatedCount/)
})

test('queue status filter includes check-before-send', () => {
  const block = src.slice(
    src.indexOf('Фильтры — всегда на виду'),
    src.indexOf('Загружаем отзывы'),
  )
  assert.match(block, /option value="check">Проверить/)
  assert.match(src, /fStatus\.value === 'check'/)
  assert.match(src, /i\._manual_check/)
})

test('row AI button generates only that review, bulk asks confirmation', () => {
  assert.match(src, /@click\.stop="generateAI\(item\)"/)
  assert.match(src, /ИИ только для этого отзыва/)
  assert.match(src, /ИИ для выбранных/)
  assert.match(src, /selectedIds\.value\.includes\(rowKey\(i\)\)/)
  assert.match(src, /pagedItems\.value\.map\(rowKey\)/)
  assert.match(src, /Сгенерировать \$\{label\} для \$\{items\.length\} выбранных отзывов/)
  assert.match(src, /clearSelectedDrafts/)
  assert.match(src, /function clearDraft\(item\)/)
})

test('1-3 star reviews with text still show templates and constructor', () => {
  assert.match(src, /reply_mode === 'ai_only'/)
  assert.match(src, /ИИ, шаблон или свой текст/)
  assert.doesNotMatch(src, /v-if="!isAiOnly\(item\)"/)
  assert.match(src, /use_overlay: Boolean\(item\._topic\)/)
  assert.match(src, /topic: item\._topic \|\| ''/)
  assert.match(src, /function selectTopic/)
  assert.match(src, /function topicsFor/)
  assert.match(src, /reviewTopics/)
  assert.match(src, /method: 'POST'/)
  assert.match(src, /ИИ пишет, обычно 10–20 секунд/)
  assert.match(src, /закончился бесплатный лимит/)
  assert.match(src, /withReviewSignature/)
})

test('auto-reply settings describe thanks and chat, not old template checkboxes', () => {
  const block = src.slice(src.indexOf("activeSection === 'settings'"), src.indexOf("Карточка отзыва"))
  assert.match(block, /без текста и фото/)
  assert.match(block, /Все сценарии сразу/)
  assert.match(src, /приглашение в чат/)
  assert.doesNotMatch(block, /шаблонам с галкой/)
})

test('ozon bulk send keeps a failed card and says when the bot will deliver', () => {
  assert.match(src, /cabinet_bot/)
  assert.match(src, /Бот отправит клиентам/)
  assert.match(src, /!done.has\(rowKey\(i\)\)/)
  assert.doesNotMatch(src, /Отправлено \$\{ok\}, ошибок/)
})

test('review card shows previous version and reshipment link', () => {
  const card = readFileSync(new URL('../src/components/ReviewCard.vue', import.meta.url), 'utf8')
  assert.match(card, /r\.update_label/)
  assert.match(card, /Было/)
  assert.match(card, /Стало/)
  assert.match(card, /linkedReshipment/)
  assert.match(card, /Доотправка #/)
})
