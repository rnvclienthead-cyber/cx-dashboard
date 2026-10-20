<script setup>
import { ref, watch } from 'vue'
import { PROBLEM_TAGS } from '../utils/problemTags'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: 'Одобрить заявку' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['cancel', 'confirm'])

const selected = ref([])
const error = ref('')

watch(() => props.open, (v) => {
  if (v) {
    selected.value = []
    error.value = ''
  }
})

function toggle(id) {
  const n = Number(id)
  const i = selected.value.indexOf(n)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(n)
}

function confirm() {
  if (!selected.value.length) {
    error.value = 'Отметьте хотя бы один тег — как на карте проблем'
    return
  }
  emit('confirm', [...selected.value].sort((a, b) => a - b))
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-[80] bg-black/40 flex items-center justify-center p-4" @click.self="emit('cancel')">
    <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-5">
      <div class="text-base font-semibold text-slate-800">{{ title }}</div>
      <p class="mt-1 text-sm text-slate-500">Отметьте причину — те же теги, что на карте проблем. ИИ потом сравним с вашей оценкой.</p>
      <div class="mt-4 flex flex-wrap gap-2">
        <button
          v-for="(name, id) in PROBLEM_TAGS"
          :key="id"
          type="button"
          @click="toggle(id)"
          :class="[
            'px-2.5 py-1.5 text-xs font-semibold rounded-lg border transition-colors text-left',
            selected.includes(Number(id))
              ? 'bg-indigo-600 border-indigo-700 text-white'
              : 'bg-white border-slate-200 text-slate-600 hover:border-indigo-300'
          ]"
        >{{ name }}</button>
      </div>
      <p v-if="error" class="mt-3 text-xs text-rose-600">{{ error }}</p>
      <div class="mt-4 flex gap-2">
        <button
          type="button"
          :disabled="busy"
          @click="confirm"
          class="flex-1 text-sm font-semibold px-3 py-2.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
        >{{ busy ? 'Отправляем…' : 'Одобрить и сохранить тег' }}</button>
        <button
          type="button"
          :disabled="busy"
          @click="emit('cancel')"
          class="text-sm px-3 py-2.5 rounded-lg text-slate-500 hover:bg-slate-50"
        >Отмена</button>
      </div>
    </div>
  </div>
</template>
