<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { DollarSign, AlertTriangle, Star, Cpu, Calendar, ChevronDown, Check, ArrowUpRight, TrendingUp, TrendingDown, BarChart3, HelpCircle } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const router = useRouter()
const loading = ref(true)
const dashboardData = ref(null)

// Настройка дат
const startDate = ref('')
const endDate = ref('')
const showMonthDropdown = ref(false)
const selectedMonthLabel = ref('Выбрать месяц')

const today = new Date()
const currentYear = today.getFullYear()

const MONTHS_PRESETS = [
  { label: 'Январь', start: `${currentYear}-01-01`, end: `${currentYear}-01-31` },
  { label: 'Февраль', start: `${currentYear}-02-01`, end: `${currentYear}-02-28` }, // Для високосных лет можно усложнить, но база считает норм
  { label: 'Март', start: `${currentYear}-03-01`, end: `${currentYear}-03-31` },
  { label: 'Апрель', start: `${currentYear}-04-01`, end: `${currentYear}-04-30` },
  { label: 'Май', start: `${currentYear}-05-01`, end: `${currentYear}-05-31` },
  { label: 'Июнь', start: `${currentYear}-06-01`, end: `${currentYear}-06-30` },
  { label: 'Июль', start: `${currentYear}-07-01`, end: `${currentYear}-07-31` },
  { label: 'Август', start: `${currentYear}-08-01`, end: `${currentYear}-08-31` },
  { label: 'Сентябрь', start: `${currentYear}-09-01`, end: `${currentYear}-09-30` },
  { label: 'Октябрь', start: `${currentYear}-10-01`, end: `${currentYear}-10-31` },
  { label: 'Ноябрь', start: `${currentYear}-11-01`, end: `${currentYear}-11-30` },
  { label: 'Декабрь', start: `${currentYear}-12-01`, end: `${currentYear}-12-31` }
]

// Быстрые переключатели периодов
const setPeriodPreset = (type) => {
  const now = new Date()
  if (type === 'current_month') {
    selectedMonthLabel.value = 'Текущий месяц'
    const y = now.getFullYear()
    const m = String(now.getMonth() + 1).padStart(2, '0')
    const d = String(now.getDate()).padStart(2, '0')
    startDate.value = `${y}-${m}-01`
    endDate.value = `${y}-${m}-${d}`
  } else if (type === 'prev_month') {
    selectedMonthLabel.value = 'Прошлый месяц'
    const d = new Date()
    d.setMonth(d.getMonth() - 1)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const lastDay = new Date(y, d.getMonth() + 1, 0).getDate()
    startDate.value = `${y}-${m}-01`
    endDate.value = `${y}-${m}-${lastDay}`
  }
}

const selectSpecificMonth = (preset) => {
  selectedMonthLabel.value = preset.label
  startDate.value = preset.start
  endDate.value = preset.end
  showMonthDropdown.value = false
}

const loadDashboardData = async () => {
  if (!startDate.value || !endDate.value) return
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/dashboard/executive?start_date=${startDate.value}&end_date=${endDate.value}&platform=${platformStore.platform}`)
    if (res.ok) {
      dashboardData.value = await res.json()
    }
  } catch (err) {
    console.error("Ошибка загрузки данных дашборда:", err)
  } finally {
    loading.value = false
  }
}

// Форматирование денег
const formatMoney = (v) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v || 0)

onMounted(() => {
  setPeriodPreset('current_month') // по умолчанию открываем текущий месяц
})

// Следим за изменением дат и платформы
watch([startDate, endDate], loadDashboardData)
watch(() => platformStore.platform, loadDashboardData)
</script>

<template>
  <div class="p-4 md:p-6 max-w-7xl mx-auto bg-slate-50/50 min-h-screen">

    <!-- Плашка про актуальность данных WB -->
    <div v-if="platformStore.platform !== 'ym'" class="mb-4 flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl px-4 py-3 text-sm text-amber-800">
      <span class="text-amber-500 text-base flex-shrink-0">⚠️</span>
      <span><strong>WB:</strong> точные данные доступны только с <strong>апреля 2026 года</strong>. Более ранние периоды могут быть неполными.</span>
    </div>

    <div class="bg-white border border-slate-200 rounded-3xl p-4 md:p-6 shadow-sm mb-6 md:mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <div class="p-3 bg-slate-800 text-white rounded-2xl shadow-sm">
          <BarChart3 class="w-6 h-6" />
        </div>
        <div>
          <h1 class="text-2xl font-black text-slate-800 tracking-tight">
            Панель Управления Компанией
          </h1>
          <p class="text-xs text-slate-400 mt-0.5">Сводные операционные показатели и финансовые риски на маркетплейсах</p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button @click="setPeriodPreset('current_month')" :class="selectedMonthLabel === 'Текущий месяц' ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" class="px-3 py-2 rounded-xl text-xs font-bold transition-all shadow-sm">Текущий месяц</button>
        <button @click="setPeriodPreset('prev_month')" :class="selectedMonthLabel === 'Прошлый месяц' ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'" class="px-3 py-2 rounded-xl text-xs font-bold transition-all shadow-sm">Прошлый месяц</button>

        <div class="relative">
          <button @click="showMonthDropdown = !showMonthDropdown" class="flex items-center gap-2 px-3 py-2 bg-white border border-slate-200 text-slate-700 rounded-xl hover:bg-slate-50 transition-colors shadow-sm text-xs font-bold">
            <Calendar class="w-4 h-4 text-slate-400" />
            <span class="hidden sm:inline">{{ selectedMonthLabel }}</span>
            <ChevronDown class="w-4 h-4 text-slate-400 transition-transform" :class="{'rotate-180': showMonthDropdown}" />
          </button>

          <div v-if="showMonthDropdown" class="absolute right-0 mt-2 w-48 bg-white border border-slate-200 rounded-2xl shadow-xl z-50 p-2 max-h-64 overflow-y-auto">
            <button v-for="m in MONTHS_PRESETS" :key="m.label" @click="selectSpecificMonth(m)" class="w-full text-left px-3 py-2 rounded-xl text-xs font-semibold hover:bg-slate-50 flex items-center justify-between text-slate-700">
              {{ m.label }}
              <Check v-if="selectedMonthLabel === m.label" class="w-3.5 h-3.5 text-slate-800" />
            </button>
          </div>
        </div>

        <div class="flex items-center gap-1 bg-slate-100 p-1.5 rounded-xl border border-slate-200">
          <input type="date" v-model="startDate" class="bg-transparent border-none text-xs font-bold text-slate-700 focus:outline-none w-28" />
          <span class="text-slate-400 text-xs font-bold">→</span>
          <input type="date" v-model="endDate" class="bg-transparent border-none text-xs font-bold text-slate-700 focus:outline-none w-28" />
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">

      <!-- РЯД 1: финансовые потери -->

      <!-- Недополученный доход -->
      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-violet-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-violet-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-violet-50 text-violet-600 rounded-2xl"><TrendingDown class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Недополученный доход</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.lost_revenue) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.lost_revenue_delta != null">
            <span :class="(dashboardData.metrics.lost_revenue_delta) > 0 ? 'text-rose-500' : 'text-emerald-500'"
                  class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.lost_revenue_delta > 0" class="w-3.5 h-3.5"/>
              <TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.lost_revenue_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <p class="text-[11px] text-slate-400">Сумма розничных цен одобренных возвратов и рекламаций</p>
      </div>

      <!-- Потери по себестоимости -->
      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-red-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-red-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-red-50 text-red-600 rounded-2xl"><DollarSign class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Потери по себестоимости</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.total_loss) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.total_loss_delta != null">
            <span :class="dashboardData.metrics.total_loss_delta > 0 ? 'text-rose-500' : 'text-emerald-500'"
                  class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.total_loss_delta > 0" class="w-3.5 h-3.5"/>
              <TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.total_loss_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <p class="text-[11px] text-slate-400">Стоимость подтвержденного фабричного брака в себестоимости</p>
      </div>

      <!-- Недополученная прибыль -->
      <div @click="router.push('/finances')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-purple-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-purple-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-3">
          <div class="p-3.5 bg-purple-50 text-purple-600 rounded-2xl"><DollarSign class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Недополученная прибыль</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-2">{{ formatMoney(dashboardData?.metrics?.lost_profit) }}</div>
        <div class="flex items-center gap-2 mb-2">
          <template v-if="dashboardData?.metrics?.lost_profit_delta != null">
            <span :class="dashboardData.metrics.lost_profit_delta > 0 ? 'text-rose-500' : 'text-emerald-500'"
                  class="flex items-center gap-1 text-xs font-black">
              <TrendingUp v-if="dashboardData.metrics.lost_profit_delta > 0" class="w-3.5 h-3.5"/>
              <TrendingDown v-else class="w-3.5 h-3.5"/>
              {{ Math.abs(dashboardData.metrics.lost_profit_delta) }}%
            </span>
            <span class="text-[10px] text-slate-400">vs {{ dashboardData?.prev_period_label }}</span>
          </template>
          <span v-else class="text-[10px] text-slate-400">нет данных за предыд. период</span>
        </div>
        <p class="text-[11px] text-slate-400">Разница между ценой продажи и себестоимостью по потерям</p>
      </div>

      <!-- РЯД 2: операционные показатели -->

      <div @click="router.push('/production')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-amber-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-amber-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-4">
          <div class="p-3.5 bg-amber-50 text-amber-600 rounded-2xl"><AlertTriangle class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Уровень брака (PPM)</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-1">{{ dashboardData?.metrics?.ppm || 0 }}% <span class="text-sm font-bold text-slate-400">от заказов</span></div>
        <p class="text-[11px] text-slate-400">Средний процент дефектных товаров от общего числа чистых заказов</p>
      </div>

      <div @click="router.push('/voc')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-emerald-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-emerald-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-4">
          <div class="p-3.5 bg-emerald-50 text-emerald-600 rounded-2xl"><Star class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Индекс CSAT (VOC)</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-1">{{ dashboardData?.metrics?.csat || 100 }}%</div>
        <p class="text-[11px] text-slate-400">Доля положительных отзывов (оценки 4 и 5) от общего числа</p>
      </div>

      <div @click="router.push('/ai-tagging')" class="bg-white border border-slate-200 p-6 rounded-3xl shadow-sm hover:border-indigo-400 hover:shadow-md transition-all cursor-pointer group relative overflow-hidden">
        <div class="absolute right-3 top-3 text-slate-300 group-hover:text-indigo-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all"><ArrowUpRight class="w-5 h-5"/></div>
        <div class="flex items-center gap-4 mb-4">
          <div class="p-3.5 bg-indigo-50 text-indigo-600 rounded-2xl"><Cpu class="w-6 h-6"/></div>
          <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Объемы ИИ воркера</div>
        </div>
        <div class="text-2xl font-black text-slate-800 mb-1">{{ dashboardData?.metrics?.ai_processed || 0 }} <span class="text-sm font-bold text-slate-400">обр.</span></div>
        <p class="text-[11px] text-slate-400">Количество отзывов и рекламаций, проанализированных ИИ</p>
      </div>

    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      
      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm md:col-span-2">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-6">
          <span class="w-2 h-4 bg-red-500 rounded-md block"></span>
          Красная зона: Топ-5 SKU по финансовым потерям
        </h3>
        
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs border-collapse">
            <thead>
              <tr class="text-slate-400 font-bold border-b border-slate-100">
                <th class="pb-3">Артикул продавца</th>
                <th class="pb-3 text-center">Выявлено брака</th>
                <th class="pb-3 text-right">Суммарный убыток</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50 font-medium">
              <tr v-for="sku in dashboardData?.top_problem_sku" :key="sku.sku" class="hover:bg-slate-50/50">
                <td class="py-3.5 font-bold text-slate-700">{{ sku.sku }}</td>
                <td class="py-3.5 text-center text-slate-600 font-semibold">{{ sku.defects }} шт.</td>
                <td class="py-3.5 text-right text-red-600 font-black">{{ formatMoney(sku.loss) }}</td>
              </tr>
              <tr v-if="!loading && (!dashboardData?.top_problem_sku || dashboardData?.top_problem_sku.length === 0)">
                <td colspan="3" class="py-6 text-center text-slate-400">За выбранный период критических отклонений не зафиксировано.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-sm flex flex-col justify-between">
        <h3 class="font-black text-slate-800 text-sm uppercase tracking-wider flex items-center gap-2 mb-4">
          <span class="w-2 h-4 bg-slate-800 rounded-md block"></span>
          Воронка данных в периоде
        </h3>
        
        <div class="space-y-4 flex-1 flex flex-col justify-center">
          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Заказы клиентов (Чистые)</span>
              <span class="text-slate-800 font-black">{{ dashboardData?.metrics?.orders_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
              <div class="bg-slate-800 h-full w-full"></div>
            </div>
          </div>

          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Получено отзывов и оценок</span>
              <span class="text-slate-800 font-black">{{ dashboardData?.metrics?.feedbacks_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
              <div class="bg-emerald-500 h-full" :style="{ width: `${Math.min((dashboardData?.metrics?.feedbacks_count / (dashboardData?.metrics?.orders_count || 1)) * 100, 100)}%` }"></div>
            </div>
          </div>

          <div>
            <div class="flex justify-between text-xs font-bold text-slate-500 mb-1">
              <span>Подтвержденный брак по рекламациям</span>
              <span class="text-red-600 font-black">{{ dashboardData?.metrics?.defects_count }} шт.</span>
            </div>
            <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
              <div class="bg-red-500 h-full" :style="{ width: `${Math.min((dashboardData?.metrics?.defects_count / (dashboardData?.metrics?.orders_count || 1)) * 100 * 10, 100)}%` }"></div>
            </div>
          </div>
        </div>

        <p class="text-[10px] text-slate-400 border-t border-slate-100 pt-3 mt-4">Данные обновляются в реальном времени на основе API маркетплейсов и ИИ-анализа.</p>
      </div>

    </div>

  </div>
</template>