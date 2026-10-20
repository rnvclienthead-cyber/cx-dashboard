import test from 'node:test'
import assert from 'node:assert/strict'
import { offerKindOf, itemMatchesReturnFilters } from '../src/utils/returnsFilters.js'

test('offer kind follows reshipment then resolution', () => {
  assert.equal(offerKindOf({ reshipment: { id: 1 } }), 'link')
  assert.equal(offerKindOf({ resolution: { kind: 'reshipment_offered' } }), 'offered')
  assert.equal(offerKindOf({ resolution: { kind: 'open_question' } }), 'question')
  assert.equal(offerKindOf({}), 'none')
})

test('status filter drops approved claim from in-review queue', () => {
  const item = { status_label: 'Одобрено', status: '2', is_archive: false }
  assert.equal(itemMatchesReturnFilters({
    item, mp: 'wb', filterStatus: 'На рассмотрении', filterOffer: '',
  }), false)
  assert.equal(itemMatchesReturnFilters({
    item, mp: 'wb', filterStatus: 'Одобрено', filterOffer: '',
  }), true)
  assert.equal(itemMatchesReturnFilters({
    item, mp: 'wb', filterStatus: '', filterOffer: '',
  }), true)
})

test('offer filter drops card after a link appears', () => {
  const before = { status_label: 'На рассмотрении', is_archive: false }
  const after = { ...before, resolution: { kind: 'reshipment_link' } }
  assert.equal(itemMatchesReturnFilters({
    item: before, filterStatus: 'На рассмотрении', filterOffer: 'none',
  }), true)
  assert.equal(itemMatchesReturnFilters({
    item: after, filterStatus: 'На рассмотрении', filterOffer: 'none',
  }), false)
  assert.equal(itemMatchesReturnFilters({
    item: after, filterStatus: 'На рассмотрении', filterOffer: 'link',
  }), true)
})

test('ym waiting filter keeps only waiting claims', () => {
  assert.equal(itemMatchesReturnFilters({
    item: { status_label: 'Ждёт нашего решения', is_archive: false },
    mp: 'ym',
    filterStatus: 'Ждёт нашего решения',
  }), true)
  assert.equal(itemMatchesReturnFilters({
    item: { status_label: 'Ожидает возврат', is_archive: false },
    mp: 'ym',
    filterStatus: 'Ждёт нашего решения',
  }), false)
})
