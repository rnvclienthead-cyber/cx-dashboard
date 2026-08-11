import { ref } from 'vue'
import { apiFetch } from '../api'
import { CX_BASE } from '../utils/cx'

/**
 * CRUD шаблонов ответов + подбор подходящих под конкретное обращение.
 * Раньше был продублирован (кроме item_type) в ReviewsView и QuestionsView —
 * Фаза 4 унификации CX-воркеров.
 *
 * @param {string} itemType - 'review' | 'question' — тип обращений текущего экрана
 * @param {Function} showToast
 * @param {{defaultMinRating?: number|null, defaultMaxRating?: number|null, alsoTypes?: string[]}} [opts]
 */
export function useCxTemplates(itemType, showToast, { defaultMinRating = null, defaultMaxRating = null, alsoTypes = [] } = {}) {
  const templates   = ref([])
  const tplLoading   = ref(false)
  const tplFormOpen  = ref(false)
  const editTpl      = ref({})

  const tplDefaults = () => ({
    title: '', body: '', mp: 'all', item_type: itemType, tone: 'neutral',
    auto_reply: false, min_rating: defaultMinRating, max_rating: defaultMaxRating, require_no_text: false,
    stop_words: '', ignore_questions: false, requires_confirmation: false,
  })

  async function loadTemplates() {
    tplLoading.value = true
    try {
      const r = await apiFetch(`${CX_BASE}/templates`)
      const d = await r.json()
      templates.value = Array.isArray(d) ? d : (d.templates || [])
    } finally {
      tplLoading.value = false
    }
  }

  function openTplForm(t = null) {
    editTpl.value = t ? { ...t } : tplDefaults()
    tplFormOpen.value = true
  }

  async function saveTpl() {
    const method = editTpl.value.id ? 'PUT' : 'POST'
    const url    = editTpl.value.id ? `${CX_BASE}/templates/${editTpl.value.id}` : `${CX_BASE}/templates`
    await apiFetch(url, { method, body: JSON.stringify(editTpl.value) })
    tplFormOpen.value = false
    await loadTemplates()
    showToast('Шаблон сохранён', 'ok')
  }

  async function deleteTpl(id) {
    if (!confirm('Удалить шаблон?')) return
    await apiFetch(`${CX_BASE}/templates/${id}`, { method: 'DELETE' })
    await loadTemplates()
    showToast('Шаблон удалён', 'ok')
  }

  function relevantTemplates(item) {
    if (!Array.isArray(templates.value)) return []
    const allowed = new Set([itemType, 'all', ...(alsoTypes || [])])
    return templates.value.filter(t => {
      if (t.mp !== 'all' && t.mp !== item.mp) return false
      if (!allowed.has(t.item_type)) return false
      return true
    }).slice(0, 8)
  }

  return {
    templates, tplLoading, tplFormOpen, editTpl,
    loadTemplates, openTplForm, saveTpl, deleteTpl, relevantTemplates,
  }
}
