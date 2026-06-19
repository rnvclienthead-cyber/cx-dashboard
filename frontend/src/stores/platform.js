import { ref } from 'vue'
import { defineStore } from 'pinia'

export const usePlatformStore = defineStore('platform', () => {
  const platform = ref('wb')

  function setPlatform(p) {
    platform.value = p
  }

  return { platform, setPlatform }
})
