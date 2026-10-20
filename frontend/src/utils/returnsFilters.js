const STATUS_ALIASES = {
  'На рассмотрении': ['На рассмотрении', '0', '0.0', 'Открыта'],
  'Одобрено': ['Одобрено', '2', '2.0'],
  'Отказ': ['Отказ', 'Отклонено', '1', '1.0', '3'],
  'Отменена': ['Отменена', 'Отменено'],
  'Отменено': ['Отменена', 'Отменено'],
  'Закрыта': ['Закрыта', 'Нет в API WB'],
  'Нет в API WB': ['Закрыта', 'Нет в API WB'],
  'Ждёт нашего решения': ['Ждёт нашего решения'],
  'Ждём Яндекс': ['Ждём Яндекс'],
  'Спор': ['Спор'],
  'Ожидает возврат': ['Ожидает возврат'],
  'Отклонено': ['Отказ', 'Отклонено'],
}

export function offerKindOf(item) {
  if (!item) return 'none'
  if (item.reshipment || item.resolution?.kind === 'reshipment_link') return 'link'
  if (item.resolution?.kind === 'reshipment_offered') return 'offered'
  if (item.resolution?.kind === 'open_question') return 'question'
  return 'none'
}

export function itemMatchesReturnFilters({
  item,
  mp = 'wb',
  filterStatus = '',
  filterOffer = '',
  activeTab = true,
} = {}) {
  if (!item) return false
  if (mp !== 'ozon') {
    const wantActive = activeTab === true || activeTab === 'true'
    if (wantActive && item.is_archive) return false
    if (activeTab === false && !item.is_archive) return false
    const f = String(filterStatus || '').trim()
    if (f) {
      const hay = [item.status_label, item.status, item.status_raw]
        .map(x => String(x || '').trim())
        .filter(Boolean)
      const aliases = STATUS_ALIASES[f] || [f]
      if (!aliases.some(a => hay.includes(a))) return false
    }
  }
  const of = String(filterOffer || '').trim()
  if (of && offerKindOf(item) !== of) return false
  return true
}
