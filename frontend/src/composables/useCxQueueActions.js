import { apiFetch } from '../api'
import { CX_BASE } from '../utils/cx'

/**
 * Действия над элементом очереди отзывов/вопросов: отправка ответа,
 * подтверждение/отклонение черновика с модерации, перенос в архив без ответа.
 * Раньше были продублированы 1:1 в ReviewsView и QuestionsView —
 * Фаза 4 унификации CX-воркеров.
 *
 * @param {Ref<Array>} queueItems - ref со списком очереди; элемент убирается из него после действия
 * @param {Function} showToast
 */
export function useCxQueueActions(queueItems, showToast) {
  /**
   * @param {object} item
   * @param {Function} resolveEndpoint - (item) => путь `/wb/feedbacks/{id}/answer` или null
   */
  async function sendReply(item, resolveEndpoint) {
    const txt = item._reply?.trim()
    if (!txt) return
    const path = resolveEndpoint(item)
    if (!path) { showToast('Этот тип нельзя отправить', 'error'); return }
    try {
      const res = await apiFetch(`${CX_BASE}${path}`, {
        method: 'POST',
        body: JSON.stringify({ text: txt }),
      })
      let via = ''
      try { via = (await res.json())?.via || '' } catch { via = '' }
      showToast(via === 'cabinet_bot' ? 'Бот отправит клиенту' : 'Ответ отправлен', 'ok')
      queueItems.value = queueItems.value.filter(i =>
        !(i.mp === item.mp && i.item_type === item.item_type && String(i.id) === String(item.id))
      )
      return true
    } catch (e) {
      showToast('Ошибка отправки: ' + (e?.message || ''), 'error')
      return false
    }
  }

  async function confirmMod(item, visibility = '') {
    try {
      const suffix = visibility ? `?visibility=${encodeURIComponent(visibility)}` : ''
      await apiFetch(`${CX_BASE}/moderation/${item.pending_mod_id}/confirm${suffix}`, { method: 'POST' })
      showToast('Ответ отправлен', 'ok')
      queueItems.value = queueItems.value.filter(i => i !== item)
    } catch (e) {
      showToast('Ошибка: ' + (e?.message || ''), 'error')
    }
  }

  async function rejectMod(item) {
    try {
      await apiFetch(`${CX_BASE}/moderation/${item.pending_mod_id}`, { method: 'DELETE' })
      item.pending_mod_id = null
      item.pending_draft  = null
      showToast('Черновик отклонён', 'ok')
    } catch {
      showToast('Ошибка', 'error')
    }
  }

  async function dismissItem(item) {
    if (!confirm('Закрыть без ответа и перенести в архив?')) return
    try {
      await apiFetch(`${CX_BASE}/dismiss`, {
        method: 'POST',
        body: JSON.stringify({ mp: item.mp, item_type: item.item_type, item_id: String(item.id), note: '' }),
      })
      queueItems.value = queueItems.value.filter(i => i !== item)
      showToast('Перенесено в архив', 'ok')
    } catch {
      showToast('Ошибка', 'error')
    }
  }

  return { sendReply, confirmMod, rejectMod, dismissItem }
}
