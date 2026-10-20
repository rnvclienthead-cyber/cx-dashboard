import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const registry = readFileSync(new URL('../src/views/RegistryView.vue', import.meta.url), 'utf8')
const ppm = readFileSync(new URL('../src/views/PPMView.vue', import.meta.url), 'utf8')

for (const [name, source] of [['registry', registry], ['ppm', ppm]]) {
  test(`${name} hides unclosed-invoice placeholders from invoice search`, () => {
    assert.match(source, /isPlaceholderInvoice/)
    assert.match(source, /не закрыт/)
  })

  test(`${name} searches invoices on the server instead of a short local list`, () => {
    assert.match(source, /claims-metadata\?invoice_q=/)
    assert.match(source, /onInvoiceSearchInput/)
    assert.match(source, /max-h-64/)
  })
}
