import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const view = readFileSync(new URL('../src/views/ShopOrdersView.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')

test('sidebar has shop orders next to reshipment', () => {
  assert.match(app, /to="\/shop-orders"/)
  assert.match(app, /Заказы сайта/)
  assert.match(app, /permissionsStore\.can\('shop_orders'\)/)
})

test('route and module are registered', () => {
  assert.match(router, /path: '\/shop-orders'/)
  assert.match(router, /module: 'shop_orders'/)
  assert.match(router, /ShopOrdersView/)
})

test('shop orders search is not shared with reshipment URL', () => {
  assert.match(view, /name="shop-orders-search"/)
  assert.match(view, /autocomplete="off"/)
  assert.match(view, /listQueryFromState/)
  assert.match(view, /!route\.query\.q/)
  assert.doesNotMatch(view, /searchQuery\.value = route\.query\.q/)
  assert.doesNotMatch(view, /typeof route\.query\.q/)
  assert.doesNotMatch(view, /\.\.\.route\.query/)
})

test('warehouse card has parcels, labels and packed status back to the shop', () => {
  assert.match(view, /\/api\/v1\/shop\/orders/)
  assert.match(view, /create-shipment/)
  assert.match(view, /\/labels/)
  assert.match(view, /Посылки/)
  assert.match(view, /Собран/)
  assert.match(view, /Оплачен/)
  assert.match(view, /Отменён продавцом/)
  assert.match(view, /isAdmin/)
  assert.match(view, /Изменить статус вручную/)
  assert.match(view, /История статусов/)
  assert.match(view, /status-history/)
  assert.match(view, /Одобрить/)
  assert.doesNotMatch(view, /Все собраны/)
  assert.doesNotMatch(view, /Штрихкод сдачи/)
  assert.doesNotMatch(view, /Создать в \{\{/)
  assert.match(view, /Отследить/)
  assert.match(view, /ozon_posting_number/)
  assert.match(view, /Сначала одобрить/)
  assert.doesNotMatch(view, /placeholder="Трек вручную"/)
  assert.doesNotMatch(view, /@click="shipItem"/)
  assert.doesNotMatch(view, /Весь заказ сдан/)
  const approveAt = view.indexOf('Одобрить {{ selected.carrier_label }}')
  const packedAt = view.indexOf('/> Собрана')
  assert.ok(approveAt > 0 && packedAt > approveAt)
})
