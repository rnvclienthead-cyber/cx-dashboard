import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseStartParam } from '../src/telegramWebApp.js'

const app = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const chats = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
const login = readFileSync(new URL('../src/views/LoginView.vue', import.meta.url), 'utf8')
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

test('startapp ozon uuid', () => {
  const id = 'fdbb1be2-d564-4beb-9dad-78b0618a7341'
  assert.deepEqual(parseStartParam(`o_${id}`), { mp: 'ozon', chat: id })
})

test('startapp wb colon id', () => {
  assert.deepEqual(parseStartParam('w_1__abc-def'), { mp: 'wb', chat: '1:abc-def' })
})

test('mini app hides site chrome', () => {
  const webapp = readFileSync(new URL('../src/telegramWebApp.js', import.meta.url), 'utf8')
  assert.match(app, /isTelegramMiniApp/)
  assert.match(app, /hideChrome/)
  assert.doesNotMatch(html, /<script[^>]+telegram-web-app\.js/)
  assert.match(webapp, /telegram-web-app\.js/)
  assert.match(webapp, /isProbablyTelegramShell/)
  assert.match(html, /100dvh/)
  assert.match(app, /min-h-full/)
  assert.doesNotMatch(app, /tg-viewport-stable-height/)
  assert.match(chats, /isMiniApp/)
  assert.match(chats, /v-if="!isMiniApp"/)
  assert.match(chats, /--tg-safe-area-inset-bottom/)
  assert.match(login, /takeAfterLogin/)
  assert.match(login, /tgAuthError/)
  assert.match(chats, /centrifugoUrl/)
  const auth = readFileSync(new URL('../src/authStorage.js', import.meta.url), 'utf8')
  const api = readFileSync(new URL('../src/api.js', import.meta.url), 'utf8')
  const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
  assert.match(auth, /memory/)
  const main = readFileSync(new URL('../src/main.js', import.meta.url), 'utf8')
  assert.match(main, /takeAfterLogin/)
  assert.match(main, /isProbablyTelegramShell/)
  assert.match(main, /app.mount\('#app'\)/)
  assert.match(webapp, /setAuthSession/)
  assert.match(webapp, /openExternalLink/)
  assert.match(webapp, /tg.openLink/)
  assert.match(api, /getAuthToken/)
  assert.match(router, /getAuthToken/)
})

test('live socket uses current host, not hardcoded cxvo.ru', () => {
  const realtime = readFileSync(new URL('../src/realtime.js', import.meta.url), 'utf8')
  const tshopping = readFileSync(new URL('../src/views/TShoppingView.vue', import.meta.url), 'utf8')
  assert.match(realtime, /location\.host/)
  assert.match(chats, /centrifugoUrl\(\)/)
  assert.match(tshopping, /centrifugoUrl\(\)/)
})
