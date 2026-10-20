import test from 'node:test'
import assert from 'node:assert/strict'
import { code128Svg, encodeCode128 } from '../src/utils/code128.js'

test('encodes the Ozon drop-off method barcode', () => {
  const bits = encodeCode128('1020005029438500')
  assert.ok(bits.startsWith('11010010000'))
  assert.ok(bits.endsWith('1100011101011'))
  assert.match(bits, /^[01]+$/)
})

test('svg fills its container', () => {
  const svg = code128Svg('1020005029438500')
  assert.match(svg, /width="100%"/)
  assert.match(svg, /height="100%"/)
  assert.match(svg, /preserveAspectRatio="none"/)
  assert.match(svg, /<rect /)
})
