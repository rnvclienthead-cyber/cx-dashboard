import { ref } from 'vue'
import { defineStore } from 'pinia'

export const usePlatformStore = defineStore('platform', () => {
  // По умолчанию «Все» — склад и КС видят полный список, пока не выбрали площадку
  const platform = ref('all')

  function setPlatform(p) {
    platform.value = p
  }

  return { platform, setPlatform }
})
