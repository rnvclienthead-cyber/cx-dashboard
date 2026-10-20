import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ReshipmentView.vue', import.meta.url), 'utf8')

test('ozon drop-off barcode opens as an image for admin and warehouse manager', () => {
  assert.match(source, /canManageYandexActs/)
  assert.match(source, /\/api\/v1\/reshipment\/ozon-dropoff/)
  assert.match(source, /openOzonDropoff/)
  assert.match(source, /showOzonDropoff/)
  assert.match(source, /code128Svg/)
  assert.match(source, /ozonDropoffSvg/)
  assert.doesNotMatch(source, /printOzonDropoffLabel/)
  assert.doesNotMatch(source, /Печать штрихкода сдачи/)
  assert.doesNotMatch(source, /Сформировать акт/)
  assert.doesNotMatch(source, /Реестр актов Яндекс/)
  assert.doesNotMatch(source, /warehouse\/yandex-acts/)
})

test('ozon parcel label is the official Ozon sticker, not 80x58', () => {
  assert.match(source, /Этикетка Ozon/)
  assert.match(source, /downloadLabels\(\[selected\.id\]\)/)
})

test('warehouse manager sees a label button on the delivery card', () => {
  const warehouseCard = source.split('Для склада: стоимость')[1]?.split('Чаты WB')[0] || ''
  assert.match(warehouseCard, /downloadLabels\(\[selected\.id\]\)/)
  assert.match(warehouseCard, /Этикетка/)
  assert.match(source, /getAuthToken\(\)/)
  assert.doesNotMatch(source, /localStorage\.getItem\('token'\)/)
})

test('carrier picker uses live role, not a stale localStorage snapshot', () => {
  assert.match(source, /usePermissionsStore/)
  assert.match(source, /getAuthRole/)
  assert.match(source, /permissionsStore\.role \|\| getAuthRole/)
  assert.match(source, /candidatesFromReq/)
  assert.doesNotMatch(source, /localStorage\.getItem\('role'\)/)
})

test('selected request is not pinned onto another status tab', () => {
  assert.match(source, /function pickSelected\(id\)/)
  assert.match(source, /filterStatus\.value !== 'all' && row\.status !== filterStatus\.value/)
  assert.doesNotMatch(source, /list = \[selected\.value, \.\.\.list\]/)
})

test('YM chat button from reshipment card opens CX chats, not YM cabinet', () => {
  assert.match(source, /const ymChatRoute = \(req\) => \{/)
  assert.match(source, /path: '\/chats'/)
  assert.match(source, /mp: 'ym'/)
  assert.match(source, /:to="ymChatRoute\(selected\)"/)
  assert.doesNotMatch(source, /partner\.market\.yandex\.ru/)
})

test('ozon list cost prefers billed accruals over checkout quote', () => {
  assert.match(source, /r\.ozon_cost_actual \|\| r\.shipping_cost \|\| r\.ozon_cost/)
})

test('Ozon chat button from reshipment card opens CX chats', () => {
  assert.match(source, /const ozonChatRoute = \(req\) => \{/)
  assert.match(source, /mp: 'ozon'/)
  assert.match(source, /:to="ozonChatRoute\(selected\)"/)
  assert.match(source, /selected\.ozon_chat_id/)
})
