import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const worker = readFileSync(new URL('../../cloudflare/telegram-worker.js', import.meta.url), 'utf8')

test('worker keeps telegram root POST and proxies the rest', () => {
  assert.match(worker, /handleTelegram/)
  assert.match(worker, /proxySite/)
  assert.match(worker, /https:\/\/cxvo\.ru/)
  assert.match(worker, /pathname === "\/" && request\.method === "POST"/)
  assert.match(worker, /websocket/)
    assert.match(worker, /reply_markup/)
    assert.match(worker, /parse_mode/)
})
