import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const returnsSrc = readFileSync(new URL('../src/views/ReturnsView.vue', import.meta.url), 'utf8')
const miniSrc = readFileSync(new URL('../src/components/ReturnMiniChat.vue', import.meta.url), 'utf8')

test('decision panel sits next to the opened return and chat is under it', () => {
  assert.match(returnsSrc, /ReturnMiniChat/)
  assert.match(returnsSrc, /return-chat-dock/)
  assert.match(returnsSrc, /layout="dock"/)
  assert.match(returnsSrc, /isSelectedItem\(item\)/)
  assert.match(returnsSrc, /data-return-open/)
  assert.doesNotMatch(returnsSrc, /router\.push\(\{ path: '\/chats'/)
})

test('return card shows reshipment next to dates, not inside the decision panel', () => {
  assert.match(returnsSrc, /offerTitle/)
  assert.match(returnsSrc, /reshipmentCardOf/)
  assert.match(returnsSrc, /По этой заявке доотправки нет/)
  assert.match(returnsSrc, /reshipmentTo/)
  assert.match(returnsSrc, /Предложили доотправку/)
  assert.match(returnsSrc, /Вопрос без решения/)
  assert.match(returnsSrc, /Ссылка отправлена/)
  assert.match(returnsSrc, /wbStatusNote/)
  assert.doesNotMatch(returnsSrc, /Расшифровка статуса \(WB\)/)
  assert.match(returnsSrc, /Комментарий WB/)
  const aside = returnsSrc.includes('isSelectedItem(item)')
    ? returnsSrc.split('v-if="isSelectedItem(item)"')[1] || ''
    : ''
  assert.doesNotMatch(aside.slice(0, 2500), />Доотправка</)
})

test('mini chat loads messages and can send on this page', () => {
  assert.match(miniSrc, /chat-messages\/\$\{props\.mp\}/)
  assert.match(miniSrc, /chats\/\$\{encodeURIComponent\(props\.chatId\)\}\/message/)
  assert.match(miniSrc, /Написать здесь/)
})

test('mini chat has templates, AI generate and attachments', () => {
  assert.match(miniSrc, /chat-templates/)
  assert.match(miniSrc, /suggest\/\$\{encodeURIComponent\(props\.chatId\)\}\/generate/)
  assert.match(miniSrc, /message-with-files/)
  assert.match(miniSrc, /Подставить шаблон/)
  assert.match(miniSrc, /Сгенерировать ответ ИИ/)
  assert.match(miniSrc, /compactThemeGroups/)
})

test('docked chat thread is independently scrollable and labels the buyer', () => {
  assert.match(miniSrc, /chat-thread/)
  assert.match(miniSrc, /overflowY: 'scroll'/)
  assert.match(miniSrc, /height: '200px'/)
  assert.match(miniSrc, /Покупатель/)
  assert.match(miniSrc, /fromClaim/)
  assert.match(miniSrc, /claimComment/)
  assert.match(miniSrc, /fmtDayLabel/)
  assert.match(miniSrc, /fmtMsgTime/)
})

test('opened claim that no longer matches filters is dropped', () => {
  assert.match(returnsSrc, /await dropIfOutOfFilter\(item\.claim_id\)/)
  assert.match(returnsSrc, /function wbDaysOpen/)
  assert.match(returnsSrc, /WB может одобрить сам/)
})

test('decision keeps the opened return instead of reloading the list', () => {
  assert.match(returnsSrc, /submitDecision/)
  assert.doesNotMatch(returnsSrc, /await loadData\(page\.value\)/)
  assert.match(returnsSrc, /data-return-open/)
  assert.match(returnsSrc, /dropIfOutOfFilter/)
  assert.match(returnsSrc, /onChatChanged/)
})

test('full chat link opens our chats page for this thread', () => {
  assert.match(returnsSrc, /fullChatTo/)
  assert.match(returnsSrc, /Открыть чат полностью/)
  assert.match(returnsSrc, /path: '\/chats'/)
  assert.doesNotMatch(returnsSrc, /Открыть чат в ЛК/)
  assert.doesNotMatch(returnsSrc, /openCabinetChat/)
  assert.doesNotMatch(returnsSrc, /openExternalLink/)
})

test('approve does not ask for problem-map tags', () => {
  assert.doesNotMatch(returnsSrc, /ClaimStaffTagsModal/)
  assert.doesNotMatch(returnsSrc, /staff_tag_ids/)
})

test('return number is copyable text, not a card overlay link', () => {
  assert.match(returnsSrc, /copyReturnNumber/)
  assert.match(returnsSrc, /select-text/)
  assert.doesNotMatch(returnsSrc, /absolute inset-0 z-\[1\]/)
  assert.doesNotMatch(returnsSrc, /onAppClick\(\$event, \(\) => selectItem/)
})

test('returns list has top pager, date sort and reshipment interaction filter', () => {
  assert.equal((returnsSrc.match(/class="returns-pager/g) || []).length, 2)
  assert.match(returnsSrc, /sortDir/)
  assert.match(returnsSrc, /filterOffer/)
  assert.match(returnsSrc, /params\.set\('sort'/)
  assert.match(returnsSrc, /params\.set\('offer'/)
  assert.match(returnsSrc, /Сначала новые/)
  assert.match(returnsSrc, /Сначала старые/)
  assert.match(returnsSrc, /Любое взаимодействие/)
  assert.match(returnsSrc, /Нет предложения/)
})

test('filtered return drops locally after decision or chat change', () => {
  assert.match(returnsSrc, /itemMatchesReturnFilters/)
  assert.match(returnsSrc, /dropIfOutOfFilter/)
  assert.match(returnsSrc, /@changed="onChatChanged"/)
  assert.match(miniSrc, /emit\('changed'\)/)
})

test('offer filter keeps only cards with that badge', () => {
  assert.match(returnsSrc, /offerKind\(i\) === filterOffer\.value/)
})

test('wb chat starts in the background when a return is opened', () => {
  assert.match(returnsSrc, /startWbChatPrefetch/)
  assert.match(returnsSrc, /prefetchingChat/)
  assert.match(returnsSrc, /wbChatPrefetch/)
  assert.match(returnsSrc, /чат по живым заявкам WB открывается заранее/i)
})

test('WB reject asks for a reason instead of free text', () => {
  assert.match(returnsSrc, /currentMp === 'wb'/)
  assert.match(returnsSrc, /v-if="currentMp !== 'wb'"/)
  assert.match(returnsSrc, /Выберите причину отказа/)
  assert.match(returnsSrc, /WB_REJECT_REASONS/)
  assert.doesNotMatch(
    returnsSrc.slice(
      returnsSrc.indexOf('function submitReject()'),
      returnsSrc.indexOf('submitDecision(action, rejectComment.value, rejectReason.value)'),
    ),
    /currentMp\.value === 'wb' \? 10/,
  )
})
