// Общие константы и функции форматирования для экранов CX (отзывы, вопросы, чаты).
// Раньше были продублированы в каждом экране по отдельности — Фаза 4 унификации CX-воркеров.

export const CX_BASE = '/api/v1/cx-responses'

export const MP_BADGE_CLASSES = {
  wb:   'bg-purple-100 text-purple-700',
  ym:   'bg-yellow-100 text-yellow-700',
  ozon: 'bg-blue-100 text-blue-700',
  all:  'bg-gray-100 text-gray-600',
}

export function fmtAge(sec) {
  if (sec == null) return '?'
  if (sec < 3600)  return `${Math.round(sec / 60)} мин`
  if (sec < 86400) return `${Math.round(sec / 3600)} ч`
  return `${Math.round(sec / 86400)} дн`
}

export function ageCls(sec) {
  if (sec == null)       return 'bg-gray-100 text-gray-500'
  if (sec > 86400 * 2)   return 'bg-red-100 text-red-700'
  if (sec > 86400)       return 'bg-orange-100 text-orange-700'
  if (sec > 3600 * 4)    return 'bg-yellow-100 text-yellow-700'
  return 'bg-green-100 text-green-700'
}

export function fmtDateTime(s) {
  if (!s) return ''
  const d = new Date(s)
  return d.toLocaleDateString('ru', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
    ' ' + d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

export const REVIEW_SIGNERS = ['Ольга', 'Софья', 'Александр']

const NAMED_SIGN_RE = /(?:\n\n|\s+)(?:С уважением,\s*)?(?:Ольга|Софья|Александр),?\s*команда Видовито\.?\s*$/i
const NAKED_SIGN_RE = /(?:\n\n|\s+)(?:С уважением,\s*)?Команда Видовито\.?\s*$/i

export function withReviewSignature(text, signer) {
  const body = String(text || '').replace(/\s+$/, '')
  if (!body) return body
  if (NAMED_SIGN_RE.test(body)) return body
  const name = REVIEW_SIGNERS.includes(signer)
    ? signer
    : REVIEW_SIGNERS[Math.floor(Math.random() * REVIEW_SIGNERS.length)]
  const stripped = body.replace(NAKED_SIGN_RE, '').replace(/\s+$/, '')
  return `${stripped}\n\n${name}, команда Видовито`
}

export function wbPhotoUrl(nmId) {
  const n = parseInt(nmId, 10)
  if (!n) return ''
  return `/api/v1/cx-responses/wb-photo/${n}`
}

export function reviewProductPhoto(item) {
  if (!item) return ''
  if (item.mp === 'wb' && item.nm_id) return wbPhotoUrl(item.nm_id)
  return item.product_photo || ''
}
