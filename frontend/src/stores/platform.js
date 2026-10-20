import { ref } from 'vue'
import { defineStore } from 'pinia'

function storedPlatform() {
  if (typeof sessionStorage === 'undefined') return 'all'
  const value = sessionStorage.getItem('cx.platform') || ''
  return ['wb', 'ym', 'ozon', 'all'].includes(value) ? value : 'all'
}

export const usePlatformStore = defineStore('platform', () => {
  // Помним выбор в этой вкладке, чтобы вопросы не успевали открыться «всеми площадками».
  const platform = ref(storedPlatform())

  function setPlatform(p) {
    platform.value = p
    try { sessionStorage.setItem('cx.platform', p) } catch { /* приватный режим */ }
  }

  return { platform, setPlatform }
})
