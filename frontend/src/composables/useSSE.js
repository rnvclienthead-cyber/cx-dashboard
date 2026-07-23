import { ref, onMounted, onUnmounted } from 'vue'

/**
 * Подписывается на SSE-поток. Не перезагружает автоматически —
 * возвращает флаг hasUpdate и функцию applyUpdate, чтобы пользователь
 * сам решал когда обновить (фильтры не сбрасываются).
 *
 * @param {string|string[]} modules  - модуль или массив модулей
 * @param {Function}        onUpdate - функция перезагрузки данных
 * @returns {{ hasUpdate: Ref<boolean>, applyUpdate: Function }}
 */
export function useSSE(modules, onUpdate) {
  const watched = Array.isArray(modules) ? modules : [modules]
  const hasUpdate = ref(false)
  let es = null

  function applyUpdate() {
    hasUpdate.value = false
    onUpdate()
  }

  function connect() {
    const token = localStorage.getItem('token')
    if (!token) return

    es = new EventSource(`/api/v1/events?token=${encodeURIComponent(token)}`)

    es.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'data_updated' && watched.includes(msg.module)) {
          hasUpdate.value = true
        }
      } catch (_) {}
    }

    es.onerror = () => {
      es.close()
      setTimeout(connect, 10_000)
    }
  }

  onMounted(connect)
  onUnmounted(() => { if (es) es.close() })

  return { hasUpdate, applyUpdate }
}
