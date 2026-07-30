import { ref } from 'vue'

/**
 * Всплывающее уведомление в правом нижнем углу экрана.
 * Один toast за раз, автоматически скрывается через 3 секунды.
 *
 * @returns {{ toast: Ref<{msg: string, type: string}|null>, showToast: Function }}
 */
export function useToast() {
  const toast = ref(null)

  function showToast(msg, type = 'ok') {
    toast.value = { msg, type }
    setTimeout(() => { toast.value = null }, 3000)
  }

  return { toast, showToast }
}
