import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { onAppClick, sameQuery } from '../src/utils/nav.js'

const shell = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const reship = readFileSync(new URL('../src/views/ReshipmentView.vue', import.meta.url), 'utf8')
const chats = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
const returns = readFileSync(new URL('../src/views/ReturnsView.vue', import.meta.url), 'utf8')
const reviews = readFileSync(new URL('../src/views/ReviewsView.vue', import.meta.url), 'utf8')
const reviewCard = readFileSync(new URL('../src/components/ReviewCard.vue', import.meta.url), 'utf8')

test('modified click leaves native new-tab behaviour', () => {
  let ran = false
  const fn = () => { ran = true }
  const prevent = []
  const click = (extra) => ({
    defaultPrevented: false,
    button: 0,
    preventDefault() { prevent.push(true) },
    ...extra,
  })
  onAppClick(click({}), fn)
  assert.equal(ran, true)
  assert.equal(prevent.length, 1)
  ran = false
  onAppClick(click({ metaKey: true }), fn)
  onAppClick(click({ ctrlKey: true }), fn)
  onAppClick(click({ button: 1 }), fn)
  assert.equal(ran, false)
})

test('sameQuery compares listed keys only', () => {
  assert.equal(sameQuery({ id: '1', extra: 'a' }, { id: 1 }, ['id']), true)
  assert.equal(sameQuery({ id: '1' }, { id: '2' }, ['id']), false)
})

test('dashboard keeps pages alive so Back restores the open card', () => {
  assert.match(shell, /KeepAlive/)
  assert.match(shell, /<component :is="Component"/)
})

test('reshipment scanner and rows are real links', () => {
  assert.match(reship, /<RouterLink[^>]*to="\/return\/scan"/)
  assert.match(reship, /requestHref\(req\)/)
  assert.match(reship, /syncListQuery/)
  assert.doesNotMatch(reship, /@click="router\.push\('\/return\/scan'\)"/)
})

test('chats open return and reshipment as links', () => {
  assert.match(chats, /returnFromChatTo/)
  assert.match(chats, /reshipmentFromChatTo/)
  assert.match(chats, /chatItemHref/)
  assert.doesNotMatch(chats, /router\.push\(\{ path: '\/returns'/)
  assert.doesNotMatch(chats, /router\.push\(\{ path: '\/reshipment'/)
})

test('returns scanner and reshipment jump are links, selected claim stays in URL', () => {
  assert.match(returns, /<RouterLink[^>]*to="\/return\/scan"/)
  assert.match(returns, /returnItemTo\(/)
  assert.match(returns, /reshipmentTo\(/)
  assert.doesNotMatch(returns, /@click="router\.push\('\/return\/scan'\)"/)
})

test('reviews keep the opened card in the URL', () => {
  assert.doesNotMatch(reviews, /router\.replace\(\{ query: \{\} \}\)/)
  assert.match(reviews, /closeReviewCard/)
  assert.match(reviewCard, /<RouterLink/)
})

test('admin panel is on hub, not in CX sidebar', () => {
  const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
  assert.doesNotMatch(shell, /to="\/admin"/)
  assert.match(router, /hub\.cxvo\.ru/)
})
