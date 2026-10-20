import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')

test('review filter is available to everyone with chats access', () => {
  assert.match(source, /@click="setChatFilter\('review'\)"/)
  assert.doesNotMatch(source, /v-if="isAdmin" @click="setChatFilter\('review'\)"/)
  assert.match(source, /loadReviewQueue\(true\)/)
  assert.doesNotMatch(
    source,
    /if \(!isAdmin\.value && \(tab\.value === 'triage' \|\| chatFilter\.value === 'review'\)\)/,
  )
})

test('pending queue ignores chats where last message is ours', () => {
  assert.match(source, /function chatNeedsReply/)
  assert.match(source, /senderKind\(item\) !== 'seller'/)
  assert.match(source, /list = list.filter\(i => chatNeedsReply\(i\)\)/)
  assert.match(source, /v-if="chatNeedsReply\(item\)"/)
})

test('new badge is shared via seen_ts, not localStorage', () => {
  assert.match(source, /const lastView = Number\(item\.seen_ts \|\| 0\)/)
  assert.match(source, /\$\{BASE\}\/chats\/seen/)
  assert.match(source, /chats\/mark-all-seen/)
  assert.match(source, /Number\(item\.unread_count \|\| 0\) > 0/)
  assert.doesNotMatch(source, /cx_chat_viewed_ts/)
  assert.doesNotMatch(source, /localStorage\.setItem\(VIEWED_KEY/)
})

test('fulfillment badge sits next to chat age, once', () => {
  assert.match(source, /chatFulfillmentCls/)
  assert.match(source, /applyChatFulfillment/)
  assert.match(source, /payload\.fulfillment/)
  const vIf = source.match(/v-if="chatFulfillmentCode\(chatItem\)"/g) || []
  assert.equal(vIf.length, 1)
  assert.ok(source.indexOf('fmtAge(chatAgeSec(chatItem))') < source.indexOf('chatFulfillmentCls(chatItem)'))
})

test('seller messages show auto or manager label', () => {
  assert.match(source, /msg\.repliedByName/)
  assert.match(source, /nekomplekt_branch/)
})

test('quick template chips pin three frequent themes', () => {
  assert.match(source, /compactThemeGroups/)
  assert.match(source, /onCompactChatThemeChip/)
  assert.match(source, /pickThemeTemplate/)
  assert.doesNotMatch(source, /compactChatTemplateGroups = computed\(\(\) => chatTemplateGroups\.value\.slice\(0, 3\)\)/)
})

test('chat composer has template insert and AI generate like reviews', () => {
  assert.match(source, /insertChatPlaybookTemplate/)
  assert.match(source, /generateSuggestAi/)
  assert.match(source, /title="Подставить шаблон"/)
  assert.match(source, /title="Сгенерировать ответ ИИ"/)
  assert.doesNotMatch(source, /Сгенерировать черновик/)
})

test('search is by name and keywords on all marketplaces', () => {
  assert.match(source, /placeholder="Имя, текст, артикул или заказ…"/)
  assert.match(source, /function looksLikeChatRef/)
  assert.match(source, /chats-search\?q=/)
  assert.doesNotMatch(source, /placeholder="chatId или ссылка WB/)
  assert.doesNotMatch(source, /Вставьте chatId или ссылку из ЛК WB/)
})

test('YM cabinet link keeps uuid slashes and falls back to search', () => {
  assert.match(source, /chatId=\$\{cab\}/)
  assert.match(source, /q.set\('search'/)
  assert.doesNotMatch(source, /if \(cab.includes\('\/'\)\) q.set\('chatId', cab\)/)
})

test('greeting repeats 12 hours after our hello, even if client said hello', () => {
  assert.match(source, /function lastSellerGreetingAt/)
  assert.match(source, /12 \* 60 \* 60 \* 1000/)
  assert.doesNotMatch(source, /Клиент уже начал с приветствия/)
  assert.doesNotMatch(source, /function moscowDateKey/)
})

test('live chat refresh keeps scroll if the thread was scrolled up', () => {
  assert.match(source, /function isChatNearBottom/)
  assert.match(source, /function keepChatScroll/)
  assert.match(source, /function scheduleOpenChatReload/)
  assert.match(source, /setInterval\(\(\) => fetchChatMsgs\(false/)
  assert.match(source, /msg\.messageId \|\| msg\.id \|\| idx/)
  assert.doesNotMatch(source, /setInterval\(\(\) => fetchChatMsgs\(true/)
  assert.doesNotMatch(
    source,
    /chatItem\.value && String\(chatItem\.value\.id\) === chatId && chatMp\(chatItem\.value\) === mp\) \{\s*fetchChatMsgs\(true\)/,
  )
})

test('chat header shows order status from local card', () => {
  assert.match(source, /function chatOrder\(/)
  assert.match(source, /chatOrder\(chatItem\)/)
  assert.match(source, /payload\.order/)
  assert.match(source, /specific\?\.order/)
  assert.match(source, /chatOrderToneCls/)
  assert.match(source, /Заказ/)
})
