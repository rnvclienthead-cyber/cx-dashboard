import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const store = readFileSync(new URL('../src/stores/permissions.js', import.meta.url), 'utf8')
const login = readFileSync(new URL('../src/views/LoginView.vue', import.meta.url), 'utf8')
const view = readFileSync(new URL('../src/views/TShoppingView.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')

test('login accepts a username, not only email', () => {
  assert.match(login, /type="text"/)
  assert.match(login, /placeholder="логин или почта"/)
  assert.doesNotMatch(login, /type="email"/)
})

test('sidebar has T-Shopping section', () => {
  assert.match(app, /to="\/tshopping"/)
  assert.match(app, /Запросы «Т-Шопинг»/)
  assert.match(app, /permissionsStore\.can\('tshopping'\)/)
})

test('route and module are registered', () => {
  assert.match(router, /path: '\/tshopping'/)
  assert.match(router, /module: 'tshopping'/)
  assert.match(router, /TShoppingView/)
})

test('two-sided workspace: partner creates, only they close, we request close', () => {
  assert.match(view, /\/api\/v1\/tshopping/)
  assert.match(view, /message-with-files/)
  assert.match(view, /Новая заявка/)
  assert.match(view, /Закрыть заявку/)
  assert.match(view, /Попросить закрыть/)
  assert.match(view, /request-close/)
  assert.match(view, /isTshoppingPartner/)
  assert.match(view, /Заказ /)
})

test('messenger list: full-width search, no waiting-us filters', () => {
  assert.match(view, /Поиск по заказу или тексту/)
  assert.match(view, /w-full text-sm border/)
  assert.doesNotMatch(view, /Ждёт нас/)
  assert.doesNotMatch(view, /Ждут их/)
  assert.doesNotMatch(view, /Ждёт Видовито/)
  assert.doesNotMatch(view, /chatFilter/)
  assert.match(view, /ждёт ответа/)
  assert.match(view, /waitingReply/)
  assert.match(view, /liveTick/)
  assert.match(view, /newSubscription\('tshopping'\)/)
})

test('staff replies go to help@vidovito.team and can close email tickets', () => {
  assert.match(view, /help@vidovito.team/)
  assert.match(view, /isEmailChat/)
  assert.match(view, /canCloseChat/)
})

test('email thread is letters grouped by order, not chat bubbles', () => {
  assert.match(view, /isEmailThread/)
  assert.match(view, /letterSubject/)
  assert.match(view, /letterFrom/)
  assert.match(view, /isNearBottom/)
  assert.match(view, /keepScroll/)
})

test('telegram messages are labelled separately from mail', () => {
  assert.match(view, /channelSuffix/)
  assert.match(view, /Telegram/)
})

test('return templates and chat themes are available to staff', () => {
  assert.match(view, /returnTemplates/)
  assert.match(view, /Шаблоны/)
  assert.match(view, /chat-templates\?mp=ozon/)
  assert.match(view, /suggestedTemplate/)
})

test('order card shows Ozon sum and status', () => {
  assert.match(view, /ozonCard/)
  assert.match(view, /Сумма:/)
  assert.match(view, /formatMoney/)
})

test('partner shell hides company sidebar and marketplace switcher until rights load', () => {
  assert.match(app, /hideChrome/)
  assert.match(app, /isTshoppingPartner/)
  assert.match(app, /permissionsStore\.loaded/)
  assert.match(store, /tshopping_partner/)
  assert.match(store, /if \(!loaded\.value\) return \[\]/)
})
