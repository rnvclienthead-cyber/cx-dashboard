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
