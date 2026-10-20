import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')
const view = readFileSync(new URL('../src/views/IntakeView.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const roles = readFileSync(new URL('../../backend/routers/roles.py', import.meta.url), 'utf8')

test('intake sits next to reshipment and is admin-only', () => {
  const reship = app.indexOf('to="/reshipment"')
  const intake = app.indexOf('to="/intake"')
  assert.ok(reship > 0)
  assert.ok(intake > reship)
  const chunk = app.slice(intake - 80, intake + 520)
  assert.match(chunk, /Приёмка/)
  assert.match(chunk, /permissionsStore\.isAdmin/)
  assert.doesNotMatch(app, /can\('warehouse_intake'\)/)
})

test('intake route is registered as admin-only', () => {
  assert.match(router, /path: '\/intake'/)
  assert.match(router, /module: 'warehouse_intake'/)
  assert.match(router, /IntakeView/)
  assert.match(router, /adminOnly: true/)
  assert.match(router, /to\.meta\.adminOnly && !permStore\.isAdmin/)
})

test('intake page loads the receiving journal', () => {
  assert.match(view, /\/api\/return\/intake\?days=/)
  assert.match(view, /По артикулам/)
  assert.match(view, /Журнал/)
})

test('intake journal links to the return queue and the order card', () => {
  assert.match(view, /function returnTo/)
  assert.match(view, /path: '\/returns'/)
  assert.match(view, /q\.mp = String\(row\.marketplace\)/)
  assert.match(view, /function orderTo/)
  assert.match(view, /\/return\/\$\{encodeURIComponent\(code\)\}/)
  assert.match(view, /has_claim/)
  assert.match(view, />Возврат</)
  assert.match(view, />Заказ</)
  assert.match(view, /без претензии/)
})

test('roles catalog hides intake from regular users', () => {
  assert.match(roles, /"key": "warehouse_intake"/)
  assert.match(roles, /"assignable": False/)
  assert.doesNotMatch(roles, /_WAREHOUSE_DEFAULT_MODULES = \([^)]*warehouse_intake/)
})
