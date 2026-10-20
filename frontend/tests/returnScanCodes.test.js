import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createHidWedge,
  dropStickerQr,
  formatManualCode,
  formatReceivedAt,
  html5QrcodeLinearConfig,
  isBoxOrRouteCode,
  isLinearSticker,
  isStickerQr,
  LINEAR_DETECTOR_FORMATS,
  parseStickerDigits,
  pickBestScanCode,
  ZXING_LINEAR_FORMATS,
} from '../src/utils/returnScan.js'

test('USB pistol burst plus Enter is a scan, slow typing is not', () => {
  const wedge = createHidWedge({ maxGapMs: 120, minLen: 6 })
  let t = 1000
  for (const ch of '56903118348') {
    const r = wedge.push(ch, t)
    assert.equal(r.kind, 'char')
    t += 20
  }
  assert.equal(wedge.push('Enter', t).code, '56903118348')

  const slow = createHidWedge({ maxGapMs: 120, minLen: 6 })
  slow.push('5', 0)
  slow.push('6', 400)
  assert.equal(slow.push('Enter', 500).kind, 'enter')
})

test('receipt time is shown in Moscow clock', () => {
  const text = formatReceivedAt('2026-09-16T08:57:00+00:00')
  assert.match(text, /16\.09/)
  assert.match(text, /11:57/)
})

test('one field accepts two numbers or a glued sticker', () => {
  assert.equal(formatManualCode('3554806 4307'), '35548064307')
  assert.equal(formatManualCode('35548064307'), '35548064307')
  assert.equal(formatManualCode(' 5690311 '), '5690311')
})

test('camera prefers printed numbers over the service QR', () => {
  assert.equal(pickBestScanCode(['TRBX4913687809', '56903118348']), '56903118348')
  assert.equal(pickBestScanCode(['*CUbB6ORp', '39841818852']), '39841818852')
  assert.equal(pickBestScanCode(['*CUbB6ORp'], { numericOnly: true }), '')
  assert.equal(pickBestScanCode(['*DTeDvnOC', '50', '41']), '*DTeDvnOC')
  assert.equal(pickBestScanCode(['TRBX4913687809']), '')
  assert.ok(isBoxOrRouteCode('TRBX4913687809'))
})

test('sticker OCR keeps the two printed numbers and ignores QR text', () => {
  assert.equal(parseStickerDigits('{"part_a":"5690311","part_b":"8348"}'), '56903118348')
  assert.equal(parseStickerDigits('50 41 5690311 8348'), '56903118348')
  assert.equal(parseStickerDigits('```json\n{"code":"39841818852"}\n```'), '39841818852')
})

test('pistol barcode is digits, sticker QR is rejected', () => {
  assert.equal(isLinearSticker('56903118348'), true)
  assert.equal(isLinearSticker('5690311 8348'), true)
  assert.equal(isLinearSticker('*ВУ9ПЕРрЙ'), false)
  assert.equal(isStickerQr('*ВУ9ПЕРрЙ'), true)
  assert.equal(isStickerQr('*CUbB6ORp'), true)
})

test('scanner drops sticker QR and never asks the camera for QR formats', () => {
  assert.deepEqual(dropStickerQr(['*CUbB6ORp', '39841818852', 'TRBX1']), ['39841818852', 'TRBX1'])
  assert.ok(!LINEAR_DETECTOR_FORMATS.includes('qr_code'))
  assert.ok(LINEAR_DETECTOR_FORMATS.includes('code_128'))
  assert.ok(!ZXING_LINEAR_FORMATS.includes(0))
  assert.ok(ZXING_LINEAR_FORMATS.includes(5))
  assert.deepEqual(html5QrcodeLinearConfig().formatsToSupport, ZXING_LINEAR_FORMATS)
})
