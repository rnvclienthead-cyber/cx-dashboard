<template>
  <div class="max-w-6xl mx-auto">
    <div v-if="loading" class="flex justify-center py-16">
      <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
    </div>

    <template v-else>
      <div class="mb-4">
        <h2 class="font-semibold text-gray-800">Шаблоны ответов</h2>
        <p class="text-xs text-gray-500 mt-0.5">
          Ответ собирается по формуле <b>Приветствие + Эмпатия + Решение + Энергия + Прощание</b>.
          <template v-if="readOnly"> Только просмотр.</template>
          <template v-else> Структура вшита; можно править текст вариантов и включать/выключать их.</template>
        </p>
      </div>

      <!-- Вкладки групп -->
      <div class="flex flex-wrap gap-1.5 mb-4">
        <button v-for="g in groups" :key="g.id" @click="activeGroup = g.id"
          :class="['px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors',
            activeGroup === g.id ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-600 border-gray-200 hover:border-indigo-300']">
          {{ g.name }}
          <span :class="['ml-1 text-xs', activeGroup === g.id ? 'text-indigo-100' : 'text-gray-400']">{{ g.articles }} арт.</span>
        </button>
      </div>

      <!-- Категории с вариантами -->
      <div class="space-y-3 mb-8">
        <div v-for="cat in categories" :key="cat.problem"
          class="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <button @click="toggle(cat.problem)"
            class="w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-gray-50 text-left">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-medium text-gray-800 text-sm">{{ cat.problem }}</span>
              <span v-if="cat.manual_check" title="Ручная проверка" class="text-amber-500 text-xs">⚠ проверка</span>
              <span class="text-xs text-gray-400">{{ cat.ratings }}</span>
              <span class="text-xs text-gray-400">· {{ cat.variants.length }} вар.</span>
              <span v-if="cat.triggers" class="text-xs text-gray-400 truncate max-w-[280px]">· {{ cat.triggers }}</span>
            </div>
            <svg :class="['w-4 h-4 text-gray-400 transition-transform', expanded[cat.problem] ? 'rotate-180' : '']"
              viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
          </button>

          <div v-if="expanded[cat.problem]" class="border-t border-gray-100 divide-y divide-gray-100">
            <div v-for="(v, i) in cat.variants" :key="v.id" class="px-4 py-3"
              :class="v.active ? '' : 'opacity-50'">
              <div class="flex items-center justify-between mb-2">
                <span class="text-xs font-medium text-gray-500">Вариант {{ i + 1 }}</span>
                <div class="flex items-center gap-3">
                  <label v-if="!readOnly" class="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                    <input type="checkbox" v-model="v.active" @change="saveVariant(v)" class="w-3.5 h-3.5 rounded accent-emerald-600" />
                    активен
                  </label>
                  <span v-else class="text-xs text-gray-400">{{ v.active ? 'активен' : 'выкл' }}</span>
                  <button v-if="!readOnly" @click="saveVariant(v)" :disabled="!v._dirty"
                    :class="['text-xs font-medium', v._dirty ? 'text-indigo-600 hover:underline' : 'text-gray-300']">
                    Сохранить
                  </button>
                </div>
              </div>
              <div class="space-y-1.5">
                <textarea v-model="v.empathy" @input="v._dirty = true" rows="2" placeholder="Эмпатия"
                  :readonly="readOnly"
                  class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-green-400"></textarea>
                <textarea v-model="v.solution" @input="v._dirty = true" rows="2" placeholder="Решение"
                  :readonly="readOnly"
                  class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-blue-400"></textarea>
                <textarea v-model="v.energy" @input="v._dirty = true" rows="2" placeholder="Энергия"
                  :readonly="readOnly"
                  class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-rose-400"></textarea>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Приветствия и прощания -->
      <h3 class="font-semibold text-gray-800 mb-3 text-sm">Приветствия и прощания</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div v-for="block in phraseBlocks" :key="block.key" class="bg-white border border-gray-200 rounded-xl p-4">
          <p class="text-sm font-medium text-gray-700 mb-2">{{ block.label }}</p>
          <div class="space-y-2">
            <div v-for="p in block.items" :key="p.id" class="flex items-start gap-2">
              <span class="text-xs text-gray-400 mt-2 shrink-0">{{ p.variant_no }}</span>
              <textarea v-model="p.text" @blur="!readOnly && savePhrase(p)" rows="2"
                :readonly="readOnly"
                class="flex-1 border border-gray-200 rounded-lg px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-indigo-400"></textarea>
            </div>
          </div>
        </div>
      </div>
    </template>

    <transition name="toast">
      <div v-if="toast" :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">{{ toast.msg }}</div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../api'

const props = defineProps({
  readOnly: { type: Boolean, default: false },
})

const loading = ref(true)
const groups  = ref([])
const rules   = ref([])
const phrases = ref([])
const activeGroup = ref(null)
const expanded = ref({})

const SIT_LABEL = { positive: 'позитив (4-5★)', problem: 'проблема (1-3★)' }

function toggle(problem) {
  expanded.value[problem] = !expanded.value[problem]
}

// Группируем правила текущей группы по категории (проблеме)
const categories = computed(() => {
  const list = rules.value.filter(r => r.group_id === activeGroup.value)
  const byProblem = new Map()
  for (const r of list) {
    if (!byProblem.has(r.problem)) {
      byProblem.set(r.problem, {
        problem: r.problem,
        manual_check: r.manual_check,
        ratings: r.ratings ? r.ratings.split(',').map(x => x + '★').join(' ') : '',
        triggers: (r.triggers || '').split('\n').filter(Boolean).join(', '),
        variants: [],
      })
    }
    byProblem.get(r.problem).variants.push(r)
  }
  return [...byProblem.values()]
})

const phraseBlocks = computed(() => {
  const blocks = []
  for (const kind of ['greeting', 'farewell']) {
    for (const sit of ['positive', 'problem']) {
      const items = phrases.value.filter(p => p.kind === kind && p.situation === sit)
        .sort((a, b) => a.variant_no - b.variant_no)
      if (items.length) blocks.push({
        key: kind + sit,
        label: (kind === 'greeting' ? 'Приветствие' : 'Прощание') + ' — ' + SIT_LABEL[sit],
        items,
      })
    }
  }
  return blocks
})

async function loadKb() {
  loading.value = true
  try {
    const res = await apiFetch('/api/v1/kb')
    const d = await res.json()
    groups.value  = d.groups || []
    rules.value   = (d.rules || []).map(r => ({ ...r, _dirty: false }))
    phrases.value = d.phrases || []
    if (!activeGroup.value && groups.value.length) activeGroup.value = groups.value[0].id
  } catch {
    showToast('Ошибка загрузки шаблонов', 'error')
  } finally {
    loading.value = false
  }
}

async function saveVariant(v) {
  try {
    await apiFetch(`/api/v1/kb/rules/${v.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        group_id: v.group_id, problem: v.problem, triggers: v.triggers, ratings: v.ratings,
        empathy: v.empathy, solution: v.solution, energy: v.energy,
        manual_check: v.manual_check, sort_order: v.sort_order, active: v.active,
      }),
    })
    v._dirty = false
    showToast('Сохранено', 'ok')
  } catch {
    showToast('Ошибка сохранения', 'error')
  }
}

async function savePhrase(p) {
  try {
    await apiFetch(`/api/v1/kb/phrases/${p.id}`, { method: 'PUT', body: JSON.stringify({ text: p.text }) })
    showToast('Сохранено', 'ok')
  } catch {
    showToast('Ошибка сохранения', 'error')
  }
}

const toast = ref(null)
function showToast(msg, type = 'ok') {
  toast.value = { msg, type }
  setTimeout(() => { toast.value = null }, 2500)
}

onMounted(loadKb)
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
</style>
