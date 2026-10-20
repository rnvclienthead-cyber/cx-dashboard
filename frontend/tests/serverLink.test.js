import test from 'node:test'
import assert from 'node:assert/strict'
import {
  httpErrorMessage,
  isIdempotentMethod,
  networkErrorMessage,
  shouldRecreateEventSource,
  shouldRetryGet,
} from '../src/utils/apiNetwork.js'
import { readFileSync } from 'node:fs'

const shell = readFileSync(new URL('../src/layouts/DashboardShell.vue', import.meta.url), 'utf8')

test('retries only GET/HEAD', () => {
  assert.equal(isIdempotentMethod('GET'), true)
  assert.equal(isIdempotentMethod('POST'), false)
  assert.equal(shouldRetryGet('GET', 1), true)
  assert.equal(shouldRetryGet('GET', 2), false)
  assert.equal(shouldRetryGet('POST', 1), false)
})

test('explains gateway and network failures', () => {
  assert.match(httpErrorMessage(502, false), /временно не отвечает/)
  assert.equal(httpErrorMessage(400, true), 'Ошибка сервера')
  assert.match(networkErrorMessage(), /Нет связи с сервером/)
})

test('recreates SSE only when the browser closed the stream', () => {
  assert.equal(shouldRecreateEventSource(0), false)
  assert.equal(shouldRecreateEventSource(1), false)
  assert.equal(shouldRecreateEventSource(2), true)
})

test('shell shows a reconnect banner and pings live health', () => {
  assert.match(shell, /Нет связи с сервером/)
  assert.match(shell, /\/api\/v1\/health\/live/)
  assert.match(shell, /setInterval\(pingLive, 12000\)/)
})
