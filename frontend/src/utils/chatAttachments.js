export const CHAT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
export const CHAT_ATTACHMENT_MAX_TOTAL_BYTES = 30 * 1024 * 1024
export const CHAT_ATTACHMENT_MAX_COUNT = 6

const ALLOWED_CHAT_ATTACHMENT_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'application/pdf',
])
const ALLOWED_CHAT_ATTACHMENT_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.pdf'])

export function chatAttachmentsSupported(mp) {
  return String(mp || 'wb').toLowerCase() !== 'ym'
}

export function canSendChatContent(text, attachments, sending = false) {
  return !sending && (Boolean(String(text || '').trim()) || Boolean(attachments?.length))
}

export function validateChatAttachment(file) {
  const type = String(file?.type || '').toLowerCase()
  const name = String(file?.name || '').toLowerCase()
  const dot = name.lastIndexOf('.')
  const extension = dot >= 0 ? name.slice(dot) : ''
  // Some browsers/OS file pickers leave File.type empty. Backend verifies
  // extension plus magic bytes, so an empty declared MIME is valid here.
  if ((type && !ALLOWED_CHAT_ATTACHMENT_TYPES.has(type)) || !ALLOWED_CHAT_ATTACHMENT_EXTENSIONS.has(extension)) {
    return 'Разрешены только JPEG, PNG и PDF'
  }
  if (!Number(file?.size)) return 'Файл пустой'
  if (Number(file.size) > CHAT_ATTACHMENT_MAX_BYTES) return 'Файл больше 5 МиБ'
  return ''
}

export function validateChatAttachmentAddition(existingFiles, candidate) {
  const files = [...(existingFiles || []), candidate]
  if (files.length > CHAT_ATTACHMENT_MAX_COUNT) {
    return `Можно прикрепить не больше ${CHAT_ATTACHMENT_MAX_COUNT} файлов`
  }
  const total = files.reduce((sum, file) => sum + Number(file?.size || 0), 0)
  if (total > CHAT_ATTACHMENT_MAX_TOTAL_BYTES) return 'Общий размер файлов больше 30 МиБ'
  return ''
}

export function chatAttachmentKey(file) {
  return `${file?.name || ''}:${Number(file?.size || 0)}:${Number(file?.lastModified || 0)}`
}

export function reconcileChatSendProgress(progress, attachmentIds) {
  const files = Array.isArray(progress?.files) ? progress.files : []
  const confirmedIds = []
  const uncertainIds = []
  const retainedIds = []
  files.forEach((step, index) => {
    const id = attachmentIds?.[Number.isInteger(step?.index) ? step.index : index]
    if (!id) return
    if (step.state === 'confirmed') confirmedIds.push(id)
    else if (step.state === 'unknown') uncertainIds.push(id)
    else retainedIds.push(id)
  })
  return {
    clearText: ['confirmed', 'unknown'].includes(progress?.text?.state),
    textUnknown: progress?.text?.state === 'unknown',
    confirmedIds,
    uncertainIds,
    retainedIds,
    complete: progress?.status === 'complete',
    warnings: Array.isArray(progress?.warnings) ? progress.warnings : [],
  }
}
