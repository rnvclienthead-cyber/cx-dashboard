import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  CHAT_ATTACHMENT_MAX_BYTES,
  CHAT_ATTACHMENT_MAX_COUNT,
  CHAT_ATTACHMENT_MAX_TOTAL_BYTES,
  canSendChatContent,
  chatAttachmentKey,
  chatAttachmentsSupported,
  reconcileChatSendProgress,
  validateChatAttachment,
  validateChatAttachmentAddition,
} from '../src/utils/chatAttachments.js'

test('files enable send even when text is empty', () => {
  assert.equal(canSendChatContent('', [{ id: 'file' }]), true)
  assert.equal(canSendChatContent('   ', []), false)
  assert.equal(canSendChatContent('Текст', [], true), false)
})

test('Yandex Market attachments are unavailable', () => {
  assert.equal(chatAttachmentsSupported('wb'), true)
  assert.equal(chatAttachmentsSupported('ozon'), true)
  assert.equal(chatAttachmentsSupported('ym'), false)
})

test('client validation enforces type and five MiB limit', () => {
  assert.equal(validateChatAttachment({ name: 'screen.png', type: 'image/png', size: 128 }), '')
  assert.equal(validateChatAttachment({ name: 'scan.pdf', type: '', size: 128 }), '')
  assert.match(validateChatAttachment({ name: 'notes.txt', type: 'text/plain', size: 128 }), /JPEG, PNG и PDF/)
  assert.match(
    validateChatAttachment({ name: 'scan.pdf', type: 'application/pdf', size: CHAT_ATTACHMENT_MAX_BYTES + 1 }),
    /5 МиБ/,
  )
})

test('selection validation enforces count and total without mutating existing files', () => {
  const existing = Array.from({ length: CHAT_ATTACHMENT_MAX_COUNT }, (_, index) => ({
    name: `${index}.png`, size: 10,
  }))
  assert.match(validateChatAttachmentAddition(existing, { name: 'extra.png', size: 10 }), /не больше 6/)
  const almostFull = [{ name: 'large.pdf', size: CHAT_ATTACHMENT_MAX_TOTAL_BYTES }]
  assert.match(validateChatAttachmentAddition(almostFull, { name: 'extra.png', size: 1 }), /30 МиБ/)
  assert.equal(existing.length, CHAT_ATTACHMENT_MAX_COUNT)
})

test('attachment key is stable for duplicate selection', () => {
  const file = { name: 'photo.jpg', size: 42, lastModified: 123 }
  assert.equal(chatAttachmentKey(file), 'photo.jpg:42:123')
})

test('partial progress removes confirmed and quarantines unknown while retaining unsent', () => {
  const result = reconcileChatSendProgress({
    status: 'uncertain',
    text: { state: 'confirmed' },
    files: [
      { index: 0, state: 'confirmed' },
      { index: 1, state: 'unknown' },
      { index: 2, state: 'not_attempted' },
      { index: 3, state: 'failed' },
    ],
    warnings: ['Сверьте историю'],
  }, ['a', 'b', 'c', 'd'])
  assert.equal(result.clearText, true)
  assert.deepEqual(result.confirmedIds, ['a'])
  assert.deepEqual(result.uncertainIds, ['b'])
  assert.deepEqual(result.retainedIds, ['c', 'd'])
})

test('Yandex paperclip uses native disabled and adjacent explanation', () => {
  const source = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
  assert.match(source, /:disabled="!chatAttachmentsAvailable \|\| chatSending"/)
  assert.match(source, /На Яндекс Маркете вложения в этой версии пока недоступны/)
})
