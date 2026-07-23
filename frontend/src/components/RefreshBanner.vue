<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    enter-from-class="opacity-0 -translate-y-2"
    leave-active-class="transition-all duration-200 ease-in"
    leave-to-class="opacity-0 -translate-y-2"
  >
    <div
      v-if="show"
      class="flex items-center gap-3 px-4 py-2.5 mb-4 bg-blue-50 border border-blue-200 rounded-xl text-sm text-blue-700"
    >
      <span class="relative flex h-2 w-2">
        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
        <span class="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
      </span>
      <span class="flex-1">Данные обновились</span>
      <button
        @click="$emit('refresh')"
        class="px-3 py-1 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 transition-colors"
      >
        Обновить
      </button>
      <button @click="$emit('dismiss')" class="text-blue-400 hover:text-blue-600 transition-colors">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>
  </Transition>
</template>

<script setup>
defineProps({ show: Boolean })
defineEmits(['refresh', 'dismiss'])
</script>
