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

test('ym playbook has marketplace-specific theme UI like ozon', () => {
  assert.match(source, /const YM_THEME_UI = \{/)
  assert.match(source, /pbMp\.value === 'ym' && YM_THEME_UI/)
  assert.match(source, /сначала предлагаем купить недостающую деталь/)
  assert.match(source, /Маркет не умеет выдавать новый промокод/)
  assert.match(source, /ym_parts_promo_code/)
  assert.match(source, /покупатель использует один раз/)
})

test('wb nonreturnable logic is embedded into return theme in playbook', () => {
  assert.match(source, /return list\.filter\(t => t\.theme_key !== 'nevozvratny' && t\.theme_key !== 'promokody'\)/)
  assert.match(source, /OZON_HIDDEN_THEME_KEYS/)
  assert.match(source, /'promokody'/)
  assert.match(source, /t\.theme_key === 'vozvrat'/)
  assert.match(source, /система автоматически подставляет текст о невозвратности WB/)
  assert.match(source, /branch_label: 'WB · невозвратный товар'/)
  assert.match(source, /nonreturnableCatsLabel/)
  assert.match(source, /\/api\/v1\/nonreturnable-cats/)
})

test('wb return_expired note has no ozon wording and nasadki toggle exists', () => {
  assert.match(source, /const WB_THEME_UI = \{/)
  assert.match(source, /return_expired:\s*\{\s*note: 'Вести в поддержку Wildberries/)
  assert.match(source, /nasadki_in_stock/)
  assert.match(source, /Сменные насадки в наличии/)
  assert.doesNotMatch(source, /WB_THEME_UI[\s\S]*Не давить «Заказы»/)
})

test('ozon pickup guide from the site can be inserted into a chat', () => {
  assert.match(source, /Как забрать и проверить/)
  assert.match(source, /function insertOzonPickupGuide/)
  assert.match(source, /\/api\/v1\/chat-bot\/ozon-pickup-guide/)
  assert.match(source, /theme_key === 'ozon_pickup'/)
  assert.match(source, /delivery_method === 'ozon'/)
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
  assert.match(source, /function productFastenersLine\(card\)/)
  assert.match(source, /Винты:/)
  assert.doesNotMatch(source, /Карточка товара из Яндекс Маркета/)
})

test('form-link template is not inserted after switching chats', () => {
  const fill = source.slice(
    source.indexOf('async function fillFormLinkTemplate'),
    source.indexOf('function isMarketplaceNudge'),
  )
  assert.match(fill, /const requestId = chatSelectionRequest/)
  assert.match(fill, /isCurrentChatSelection\(requestId, chatId\)/)
})

test('picking a chat template does not train the bot until send', () => {
  const apply = source.slice(source.indexOf('function applyChatTemplate'), source.indexOf('async function fillFormLinkTemplate'))
  assert.match(apply, /lastAppliedChatTemplate\.value = template/)
  assert.doesNotMatch(apply, /recordTemplateCorrection/)
  assert.match(source, /function rememberSentTemplate\(/)
  assert.match(source, /rememberSentTemplate\(item\)/)
})

test('chat quick templates are grouped by theme folders', () => {
  assert.match(source, /groupTemplatesByTheme/)
  assert.match(source, /chatTemplateGroups/)
  assert.match(source, /onChatThemeChip/)
  assert.match(source, /Темы · \$\{chatTemplateGroups\.length\}/)
})
