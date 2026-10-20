import test from 'node:test'
import assert from 'node:assert/strict'
import { withReviewSignature, REVIEW_SIGNERS, reviewProductPhoto, wbPhotoUrl } from '../src/utils/cx.js'

test('review signature always has a name', () => {
  const signed = withReviewSignature('Спасибо за отзыв.')
  assert.match(signed, /(Ольга|Софья|Александр), команда Видовито$/)
  assert.equal(signed.endsWith('\n\nКоманда Видовито'), false)
})

test('nameless team sign is replaced', () => {
  const signed = withReviewSignature('Жаль.\n\nКоманда Видовито', 'Александр')
  assert.equal(signed, 'Жаль.\n\nАлександр, команда Видовито')
})

test('existing named sign is kept', () => {
  const original = 'Спасибо.\n\nСофья, команда Видовито'
  assert.equal(withReviewSignature(original, 'Ольга'), original)
})

test('empty text stays empty', () => {
  assert.equal(withReviewSignature(''), '')
  assert.equal(withReviewSignature('   '), '')
})

test('three signers are used', () => {
  assert.deepEqual(REVIEW_SIGNERS, ['Ольга', 'Софья', 'Александр'])
})

test('WB review photo uses nm_id card, not Ozon SKU photo', () => {
  assert.equal(
    reviewProductPhoto({
      mp: 'wb',
      nm_id: '197296830',
      product_photo: 'https://ir.ozone.ru/mop.jpg',
    }),
    '/api/v1/cx-responses/wb-photo/197296830',
  )
  assert.equal(wbPhotoUrl('197296830'), '/api/v1/cx-responses/wb-photo/197296830')
  assert.equal(
    reviewProductPhoto({ mp: 'ozon', product_photo: 'https://ir.ozone.ru/mop.jpg' }),
    'https://ir.ozone.ru/mop.jpg',
  )
})
