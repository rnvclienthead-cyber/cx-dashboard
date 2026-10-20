<template>
  <div class="min-h-screen bg-gray-50">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-7xl mx-auto px-5">

        <!-- Строка 1: заголовок + счётчики -->
        <div class="flex items-center justify-between gap-4 py-3">
          <div class="flex items-center gap-3">
            <div class="w-9 h-9 rounded-xl bg-rose-50 flex items-center justify-center text-rose-600 shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
              </svg>
            </div>
            <div>
              <h1 class="text-sm font-medium text-gray-900 leading-tight">Отзывы</h1>
              <p class="text-xs text-gray-400 leading-tight mt-0.5">Wildberries · ЯМаркет · Ozon</p>
            </div>
          </div>

          <!-- Счётчики в очереди -->
          <div v-if="activeSection === 'queue' && counts.total != null" class="flex items-center gap-2 flex-wrap justify-end">
            <span v-if="(counts.reviews || 0) > 0"
              class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
              <span class="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span>
              {{ counts.reviews }} отзывов
            </span>
            <span v-if="(counts.wb || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-purple-50 text-purple-700 border border-purple-200">
              WB {{ counts.wb }}
            </span>
            <span v-if="(counts.ym || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-200">
              ЯМ {{ counts.ym }}
            </span>
              <span v-if="(counts.ozon || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
              Ozon {{ counts.ozon }}
            </span>
            <button v-if="updatedCount > 0" type="button" @click="fKind = fKind === 'updated' ? '' : 'updated'"
              :class="['px-2.5 py-1 rounded-full text-xs font-medium border transition-colors',
                fKind === 'updated'
                  ? 'bg-violet-600 text-white border-violet-600'
                  : 'bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100']">
              Обновлённые {{ updatedCount }}
            </button>
          </div>
        </div>

        <!-- Строка 2: вкладки секций -->
        <div class="flex -mb-px">
          <button v-for="s in visibleSections" :key="s.key"
            @click="switchSection(s.key)"
            :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition-all',
              activeSection === s.key
                ? 'border-rose-500 text-rose-600'
                : 'border-transparent text-gray-500 hover:text-gray-700']">
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- Строка 3: обновить (только очередь) -->
      <div v-if="activeSection === 'queue'" class="border-t border-gray-100 bg-gray-50/60">
        <div class="max-w-7xl mx-auto px-5 py-2 flex items-center justify-end gap-3">
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Обновить
          </button>
        </div>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ ════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'queue'" class="max-w-[1600px] mx-auto px-4 py-4">

      <!-- Фильтры — всегда на виду, даже при загрузке и пустой очереди -->
      <div class="bg-white border border-gray-200 rounded-xl p-3 mb-3">
        <div class="flex flex-wrap items-end gap-2">
          <div class="flex-1 min-w-[180px]">
            <label class="block text-xs text-gray-400 mb-0.5">Поиск</label>
            <input v-model="fText" placeholder="Текст отзыва, артикул…"
              class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Артикул</label>
            <input v-model="fSku" placeholder="Арт…"
              class="w-28 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Текст</label>
            <select v-model="fHasText"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option value="">С текстом и без</option>
              <option value="with">Только с текстом</option>
              <option value="without">Только без текста</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Оценка</label>
            <div class="flex items-center gap-1">
              <button v-for="n in [5,4,3,2,1]" :key="'q'+n" type="button" @click="toggleQueueRating(n)"
                :class="['px-2 py-1.5 rounded-lg text-xs font-medium border transition-colors',
                  fRatings.includes(n)
                    ? 'bg-rose-600 text-white border-rose-600'
                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50']">
                {{ n }}★
              </button>
            </div>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Вид</label>
            <select v-model="fKind"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option value="">Все</option>
              <option value="updated">Обновлённые</option>
              <option value="new">Новые</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Статус</label>
            <select v-model="fStatus"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option value="">Все</option>
              <option value="draft">Черновик</option>
              <option value="check">Проверить</option>
              <option value="none">Без ответа</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Дата с</label>
            <input v-model="fDateFrom" type="date"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Дата по</label>
            <input v-model="fDateTo" type="date"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <button @click="resetQueueFilters"
            class="px-3 py-1.5 border border-gray-200 text-gray-500 rounded-lg text-sm hover:bg-gray-50">Сброс</button>
        </div>
      </div>

      <div v-if="queueLoading" class="flex items-center justify-center py-20">
        <div class="text-center">
          <div class="w-10 h-10 border-4 border-rose-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          <p class="text-gray-400 text-sm">Загружаем отзывы…</p>
        </div>
      </div>

      <template v-else>
        <!-- Баннер о новых отзывах (Centrifugo) -->
        <transition name="toast">
          <button v-if="newReviews > 0" @click="refreshNewReviews"
            class="w-full mb-3 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-sm font-medium shadow-sm">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Пришло новых отзывов: {{ newReviews }} — обновить
          </button>
        </transition>

        <!-- Панель массовых действий -->
        <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div class="flex items-center gap-2 flex-wrap">
            <button @click="generateSelectedTemplate" :disabled="selectedIds.length === 0 || bulkGenerating"
              title="Собрать шаблоны только для выбранных отзывов"
              :class="['inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all',
                selectedIds.length && !bulkGenerating
                  ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
              <svg v-if="bulkGenerating === 'tpl'" class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
              <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h10"/></svg>
              Шаблон для выбранных{{ selectedIds.length ? ` (${selectedIds.length})` : '' }}
            </button>
            <button @click="generateSelectedAI" :disabled="selectedIds.length === 0 || bulkGenerating"
              title="Сгенерировать ИИ только для выбранных отзывов"
              :class="['inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all',
                selectedIds.length && !bulkGenerating
                  ? 'bg-violet-600 hover:bg-violet-700 text-white'
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
              <svg v-if="bulkGenerating === 'ai'" class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
              <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>
              ИИ для выбранных{{ selectedIds.length ? ` (${selectedIds.length})` : '' }}
            </button>
            <button @click="clearSelectedDrafts" :disabled="selectedDraftIds.length === 0 || bulkGenerating || bulkSending"
              title="Очистить текст ответа у выбранных"
              :class="['inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all border',
                selectedDraftIds.length
                  ? 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
                  : 'bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed']">
              Очистить выбранные{{ selectedDraftIds.length ? ` (${selectedDraftIds.length})` : '' }}
            </button>
            <span class="w-px h-6 bg-gray-200"></span>
            <button @click="confirmSelected" :disabled="selectedDraftIds.length === 0 || bulkSending"
              :class="['inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all',
                selectedDraftIds.length && !bulkSending
                  ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
              <svg v-if="bulkSending" class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
              <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
              Отправить выбранные{{ selectedDraftIds.length ? ` (${selectedDraftIds.length})` : '' }}
            </button>
            <button @click="confirmAll" :disabled="draftItems.length === 0 || bulkSending"
              :class="['inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all border',
                draftItems.length && !bulkSending
                  ? 'bg-white border-emerald-300 text-emerald-700 hover:bg-emerald-50'
                  : 'bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed']">
              Отправить все черновики{{ draftItems.length ? ` (${draftItems.length})` : '' }}
            </button>
          </div>
          <div class="flex items-center gap-2">
            <select v-model.number="perPage" @change="currentPage = 1"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm text-gray-600 bg-white focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option v-for="n in [25,50,100]" :key="n" :value="n">{{ n }} на стр.</option>
            </select>
          </div>
        </div>

        <div v-if="filteredItems.length === 0" class="flex flex-col items-center justify-center py-20">
          <div class="text-5xl mb-4">🎉</div>
          <p class="text-gray-500 font-medium">Отзывов нет</p>
          <p class="text-gray-400 text-sm mt-1">Очередь пуста или ничего не найдено по фильтру</p>
        </div>

        <!-- Таблица -->
        <div v-else class="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm border-collapse min-w-[1200px]">
              <thead class="bg-gray-50 text-gray-500 text-xs">
                <tr class="border-b border-gray-200">
                  <th class="w-10 px-2 py-2 text-center">
                    <input type="checkbox" :checked="allSelected" @change="toggleSelectAll"
                      title="Выбрать отзывы на этой странице"
                      class="w-4 h-4 rounded accent-indigo-600 cursor-pointer" />
                  </th>
                  <th class="px-2 py-2 text-left font-medium whitespace-nowrap cursor-pointer select-none" @click="toggleSort">
                    Дата отзыва <span class="text-gray-400">{{ sortDesc ? '↓' : '↑' }}</span>
                  </th>
                  <th class="px-2 py-2 text-left font-medium">Товар</th>
                  <th class="px-2 py-2 text-left font-medium w-[170px] min-w-[150px]">Отзыв</th>
                  <th class="px-2 py-2 text-center font-medium whitespace-nowrap">Оценка</th>
                  <th class="px-2 py-2 text-left font-medium">Дост-ва / Недост.</th>
                  <th class="px-2 py-2 text-left font-medium w-[95px]">Сценарий</th>
                  <th class="px-2 py-2 text-left font-medium min-w-[360px]">Текст ответа</th>
                  <th class="px-2 py-2 text-center font-medium whitespace-nowrap">Статус</th>
                  <th class="px-2 py-2 text-center font-medium">Действия</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in pagedItems" :key="item.mp + item.id"
                  @click="openReviewCard(item)"
                  class="border-b border-gray-100 last:border-0 hover:bg-rose-50/40 align-top cursor-pointer">
                  <!-- Чекбокс -->
                  <td class="px-2 py-3 text-center" @click.stop>
                    <input type="checkbox" :value="rowKey(item)" v-model="selectedIds"
                      class="w-4 h-4 rounded accent-indigo-600 cursor-pointer" />
                  </td>
                  <!-- Дата -->
                  <td class="px-2 py-3 whitespace-nowrap text-xs text-gray-500">
                    <div>{{ fmtDateTime(item.created_at) }}</div>
                    <span :class="['inline-block mt-1 text-xs px-1.5 py-0.5 rounded-full font-medium', ageCls(item.age_sec)]">{{ fmtAge(item.age_sec) }}</span>
                  </td>
                  <!-- Товар -->
                  <td class="px-2 py-3">
                    <div class="flex items-start gap-2">
                      <div class="w-16 h-20 rounded-md overflow-hidden bg-gray-100 border border-gray-200 shrink-0">
                        <img v-if="reviewProductPhoto(item)" :src="reviewProductPhoto(item)" class="w-full h-full object-cover" />
                        <span v-else class="w-full h-full flex items-center justify-center text-gray-300">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
                        </span>
                      </div>
                      <div class="min-w-0">
                        <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp]]">{{ item.mp.toUpperCase() }}</span>
                        <a v-if="item.sku && item.nm_id && item.mp === 'wb'" @click.stop
                          :href="`https://www.wildberries.ru/catalog/${item.nm_id}/detail.aspx`" target="_blank"
                          class="block text-xs text-rose-600 hover:underline mt-1 break-all">{{ item.sku }}</a>
                        <p v-else-if="item.sku" class="text-xs text-gray-500 mt-1 break-all">{{ item.sku }}</p>
                      </div>
                    </div>
                  </td>
                  <!-- Отзыв -->
                  <td class="px-2 py-3 text-gray-700 text-xs leading-relaxed">
                    <p class="line-clamp-4 cursor-help"
                      @mouseenter="showTip($event, item.body)" @mousemove="moveTip" @mouseleave="hideTip">{{ item.body }}</p>
                    <span v-if="item.update_label"
                      class="inline-block mt-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200">
                      {{ item.update_label }}
                      <span v-if="item.prev_valuation != null && item.rating != null && item.prev_valuation !== item.rating">
                        · было {{ item.prev_valuation }}★
                      </span>
                    </span>
                    <p v-if="item.prev_text && item.prev_text !== item.body"
                      class="mt-1 text-[11px] text-gray-400 line-clamp-2">Было: {{ item.prev_text }}</p>
                    <RouterLink v-if="item.reshipment_id" :to="`/reshipment?id=${item.reshipment_id}`"
                      @click.stop
                      class="mt-1 inline-block text-[11px] text-violet-600 hover:underline">
                      Доотправка #{{ item.reshipment_id }}
                    </RouterLink>
                    <div v-if="item.photos && item.photos.length" class="flex flex-wrap gap-1 mt-1.5">
                      <button v-for="(ph, i) in item.photos.slice(0, 4)" :key="i" @click.stop="openLightbox(item.photos, i)"
                        class="block w-8 h-8 rounded overflow-hidden border border-gray-200 hover:border-rose-400 transition-colors">
                        <img :src="ph" class="w-full h-full object-cover" />
                      </button>
                      <span v-if="item.photos.length > 4" @click.stop="openLightbox(item.photos, 4)"
                        class="w-8 h-8 rounded border border-gray-200 flex items-center justify-center text-xs text-gray-400 cursor-pointer hover:border-rose-400">
                        +{{ item.photos.length - 4 }}
                      </span>
                    </div>
                  </td>
                  <!-- Оценка -->
                  <td class="px-2 py-3 text-center whitespace-nowrap">
                    <span v-if="item.rating != null" class="inline-flex items-center gap-px">
                      <svg v-for="i in 5" :key="i" width="11" height="11" viewBox="0 0 24 24"
                        :fill="i <= item.rating ? '#f59e0b' : '#e5e7eb'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    </span>
                    <span v-else class="text-gray-300">—</span>
                  </td>
                  <!-- Дост-ва / Недост. -->
                  <td class="px-2 py-3 text-xs max-w-[150px]">
                    <p v-if="item.pros" class="text-gray-600 line-clamp-2 cursor-help"
                      @mouseenter="showTip($event, 'Достоинства: ' + item.pros)" @mousemove="moveTip" @mouseleave="hideTip"><span class="text-green-600">+</span> {{ item.pros }}</p>
                    <p v-if="item.cons" class="text-gray-600 line-clamp-2 mt-0.5 cursor-help"
                      @mouseenter="showTip($event, 'Недостатки: ' + item.cons)" @mousemove="moveTip" @mouseleave="hideTip"><span class="text-red-500">−</span> {{ item.cons }}</p>
                    <span v-if="!item.pros && !item.cons" class="text-gray-300">—</span>
                  </td>
                  <!-- Сценарий -->
                  <td class="px-2 py-3 text-xs">
                    <button v-if="scenarioText(item)" type="button"
                      class="text-left text-indigo-600 break-words hover:underline"
                      :title="canEditReviewTemplates ? 'Нажмите, чтобы сменить сценарий. Двойной щелчок — править этот текст.' : 'Нажмите, чтобы сменить сценарий'"
                      @click.stop="runUnlessDouble('sc-' + item.id, () => cycleScenario(item))"
                      @dblclick.stop.prevent="cancelPendingClick('sc-' + item.id); openReviewScenarioEditor(item._topic)">
                      {{ scenarioText(item) }}
                    </button>
                    <span v-else class="text-gray-300">—</span>
                    <span v-if="item._manual_check"
                      @mouseenter="showTip($event, 'Требуется ручная проверка перед отправкой')" @mousemove="moveTip" @mouseleave="hideTip"
                      class="block mt-1 text-amber-600 text-xs cursor-help">⚠ проверить</span>
                  </td>
                  <!-- Текст ответа (редактируемый) -->
                  <td class="px-2 py-3" @click.stop>
                    <textarea v-model="item._reply" rows="4"
                      :placeholder="isAiOnly(item) ? 'ИИ, шаблон или свой текст…' : 'Написать ответ…'"
                      class="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-rose-400 min-h-[64px]"></textarea>
                    <p v-if="item._generating === 'ai'" class="mt-1 text-xs text-violet-500">ИИ пишет, обычно 10–20 секунд…</p>
                    <div v-if="topicsFor(item).length" class="mt-1 flex flex-wrap gap-1">
                      <button v-for="tp in topicsFor(item)" :key="tp.id" type="button"
                        :title="canEditReviewTemplates ? 'Двойной щелчок — править этот сценарий' : ''"
                        @click="runUnlessDouble('tp-' + item.id + '-' + tp.id, () => selectTopic(item, tp.id))"
                        @dblclick.stop.prevent="cancelPendingClick('tp-' + item.id + '-' + tp.id); openReviewScenarioEditor(tp.id)"
                        :class="['px-1.5 py-0.5 rounded text-xs border transition-colors',
                          (item._topic || item.pending_topic) === tp.id
                            ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
                            : 'bg-gray-50 text-gray-500 border-gray-200 hover:border-indigo-200 hover:text-indigo-700']">
                        {{ tp.label }}
                      </button>
                    </div>
                    <div v-if="quickReplyTemplates(item).length" class="mt-1 flex flex-wrap gap-1">
                      <button v-for="t in quickReplyTemplates(item)" :key="t.id"
                        :title="canEditReviewTemplates ? 'Двойной щелчок — править этот шаблон' : ''"
                        @click="runUnlessDouble('qt-' + item.id + '-' + t.id, () => { item._reply = withReviewSignature(t.body) })"
                        @dblclick.stop.prevent="cancelPendingClick('qt-' + item.id + '-' + t.id); openReviewQuickTemplate(t)"
                        class="px-1.5 py-0.5 bg-gray-50 hover:bg-rose-50 text-gray-500 hover:text-rose-600 border border-gray-200 rounded text-xs transition-colors">
                        {{ t.title }}
                      </button>
                    </div>
                  </td>
                  <!-- Статус -->
                  <td class="px-2 py-3 text-center whitespace-nowrap">
                    <span :class="['text-xs px-2 py-0.5 rounded-full font-medium border', statusCls(item)]">{{ statusLabel(item) }}</span>
                  </td>
                  <!-- Действия -->
                  <td class="px-2 py-3" @click.stop>
                    <div class="flex items-center justify-center gap-1">
                      <button @click.stop="generateTemplate(item)" :disabled="!!item._generating"
                        title="Шаблон только для этого отзыва"
                        :class="['p-1.5 rounded transition-colors', item._generating ? 'text-gray-300 cursor-wait' : 'text-indigo-600 hover:bg-indigo-50']">
                        <svg v-if="item._generating === 'tpl'" class="animate-spin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                        <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h10"/></svg>
                      </button>
                      <button @click.stop="generateAI(item)" :disabled="!!item._generating"
                        title="ИИ только для этого отзыва"
                        :class="['p-1.5 rounded transition-colors', item._generating ? 'text-gray-300 cursor-wait' : 'text-violet-600 hover:bg-violet-50']">
                        <svg v-if="item._generating === 'ai'" class="animate-spin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                        <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>
                      </button>
                      <button v-if="hasDraft(item)" @click.stop="clearDraft(item)"
                        title="Очистить текст ответа"
                        class="p-1.5 rounded text-gray-300 hover:text-gray-600 hover:bg-gray-50 transition-colors">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                      </button>
                      <button @click="sendReply(item)" :disabled="!item._reply?.trim()"
                        title="Отправить ответ"
                        :class="['p-1.5 rounded transition-colors', item._reply?.trim() ? 'text-emerald-600 hover:bg-emerald-50' : 'text-gray-300 cursor-not-allowed']">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
                      </button>
                      <button @click="dismissItem(item)" title="В архив без ответа"
                        class="p-1.5 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition-colors">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Пагинация -->
        <div class="flex items-center justify-between gap-2 mt-4">
          <p class="text-sm text-gray-500">Показано {{ pagedItems.length }} из {{ filteredItems.length }}</p>
          <div v-if="totalPages > 1" class="flex items-center gap-2">
            <button @click="currentPage = Math.max(1, currentPage - 1)" :disabled="currentPage === 1"
              class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">← Назад</button>
            <span class="text-sm text-gray-500">{{ currentPage }} / {{ totalPages }}</span>
            <button @click="currentPage = Math.min(totalPages, currentPage + 1)" :disabled="currentPage === totalPages"
              class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">Вперёд →</button>
          </div>
        </div>
      </template>
    </div>

    <!-- ══ АРХИВ ══════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'archive'" class="max-w-[1600px] mx-auto px-4 py-4">
      <p class="text-xs text-gray-500 mb-3">
        В архив отзыв попадает после отправки ответа (<span class="text-green-700 font-medium">Отвечено</span>),
        если его закрыли кнопкой «в архив без ответа» (<span class="text-gray-500 font-medium">Закрыто без ответа</span>)
        или если Wildberries сам убрал его из своей очереди, хотя продавец ничего не писал
        (<span class="text-amber-700 font-medium">На WB без ответа</span> — так бывает у оценок без текста).
      </p>

      <!-- Фильтры архива — те же поля, что в очереди -->
      <div class="bg-white border border-gray-200 rounded-xl p-3 mb-4">
        <div class="flex flex-wrap items-end gap-2">
          <div class="flex-1 min-w-[180px]">
            <label class="block text-xs text-gray-400 mb-0.5">Поиск</label>
            <input v-model="aText" @keyup.enter="loadArchive(1)" placeholder="Текст отзыва, артикул…"
              class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Артикул</label>
            <input v-model="aSku" @keyup.enter="loadArchive(1)" placeholder="Арт…"
              class="w-28 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Текст</label>
            <select v-model="aHasText" @change="loadArchive(1)"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option value="">С текстом и без</option>
              <option value="with">Только с текстом</option>
              <option value="without">Только без текста</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Оценка</label>
            <div class="flex items-center gap-1">
              <button v-for="n in [5,4,3,2,1]" :key="'a'+n" type="button" @click="toggleArchiveRating(n)"
                :class="['px-2 py-1.5 rounded-lg text-xs font-medium border transition-colors',
                  aRatings.includes(n)
                    ? 'bg-rose-600 text-white border-rose-600'
                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50']">
                {{ n }}★
              </button>
            </div>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Статус</label>
            <select v-model="aStatus" @change="loadArchive(1)"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400">
              <option value="">Все</option>
              <option value="answered">Отвечено</option>
              <option value="dismissed">Закрыто без ответа</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Дата с</label>
            <input v-model="aDateFrom" @change="loadArchive(1)" type="date"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-0.5">Дата по</label>
            <input v-model="aDateTo" @change="loadArchive(1)" type="date"
              class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-rose-400" />
          </div>
          <button @click="loadArchive(1)"
            class="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-sm font-medium">Найти</button>
          <button @click="resetArchiveFilters"
            class="px-3 py-1.5 border border-gray-200 text-gray-500 rounded-lg text-sm hover:bg-gray-50">Сброс</button>
        </div>
      </div>

      <div v-if="archLoading" class="flex justify-center py-16">
        <div class="w-8 h-8 border-4 border-gray-400 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="archItems.length === 0" class="text-center py-16 text-gray-400">Архив пуст</div>

      <div v-else class="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm border-collapse min-w-[1100px]">
            <thead class="bg-gray-50 text-gray-500 text-xs">
              <tr class="border-b border-gray-200">
                <th class="px-2 py-2 text-left font-medium whitespace-nowrap">Дата отзыва</th>
                <th class="px-2 py-2 text-left font-medium">Товар</th>
                <th class="px-2 py-2 text-left font-medium min-w-[200px]">Отзыв</th>
                <th class="px-2 py-2 text-center font-medium">Оценка</th>
                <th class="px-2 py-2 text-left font-medium min-w-[260px]">Ответ</th>
                <th class="px-2 py-2 text-center font-medium whitespace-nowrap">Статус</th>
                <th class="px-2 py-2 text-left font-medium whitespace-nowrap">Кто ответил</th>
                <th class="px-2 py-2 text-center font-medium">Действия</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in archItems" :key="item.mp + item.item_type + item.id"
                @click="item.item_type === 'review' && openReviewCard(item)"
                class="border-b border-gray-100 last:border-0 align-top hover:bg-rose-50/40 cursor-pointer">
                <td class="px-2 py-3 whitespace-nowrap text-xs text-gray-500">{{ fmtDateTime(item.created_at) }}</td>
                <td class="px-2 py-3">
                  <div class="flex items-start gap-2">
                    <div class="w-14 h-[72px] rounded-md overflow-hidden bg-gray-100 border border-gray-200 shrink-0">
                      <img v-if="reviewProductPhoto(item)" :src="reviewProductPhoto(item)" class="w-full h-full object-cover" />
                      <span v-else class="w-full h-full flex items-center justify-center text-gray-300">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
                      </span>
                    </div>
                    <div class="min-w-0">
                      <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp] || 'bg-gray-100 text-gray-500']">{{ (item.mp||'').toUpperCase() }}</span>
                      <p v-if="item.sku" class="text-xs text-gray-500 mt-1 break-all">{{ item.sku }}</p>
                    </div>
                  </div>
                </td>
                <td class="px-2 py-3 text-gray-700 text-xs leading-relaxed">
                  <p v-if="item.body" class="line-clamp-3" :title="item.body">{{ item.body }}</p>
                  <span v-else class="text-gray-300">—</span>
                </td>
                <td class="px-2 py-3 text-center whitespace-nowrap">
                  <span v-if="item.rating" class="inline-flex items-center gap-px">
                    <svg v-for="i in 5" :key="i" width="11" height="11" viewBox="0 0 24 24"
                      :fill="i <= item.rating ? '#f59e0b' : '#e5e7eb'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                  </span>
                  <span v-else class="text-gray-300">—</span>
                </td>
                <td class="px-2 py-3 text-xs">
                  <p v-if="item.answer_text" class="text-green-800 bg-green-50 rounded px-2 py-1.5 line-clamp-4 whitespace-pre-wrap" :title="item.answer_text">{{ item.answer_text }}</p>
                  <span v-else class="text-gray-300">—</span>
                </td>
                <td class="px-2 py-3 text-center whitespace-nowrap">
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium border', archiveStatusCls(item)]">
                    {{ archiveStatusLabel(item) }}
                  </span>
                </td>
                <td class="px-2 py-3 text-xs text-gray-500 whitespace-nowrap">
                  <template v-if="item.answered_by">
                    <div>{{ item.answered_by === 'auto_reply' ? 'Автоответ' : item.answered_by }}</div>
                    <div v-if="item.answered_at" class="text-gray-400">{{ fmtDateTime(item.answered_at) }}</div>
                  </template>
                  <span v-else class="text-gray-300">—</span>
                </td>
                <td class="px-2 py-3 text-center">
                  <button v-if="item.archive_reason === 'dismissed'" @click.stop="undismissItem(item)"
                    class="text-xs text-indigo-600 hover:underline whitespace-nowrap">Вернуть в очередь</button>
                  <span v-else class="text-gray-300">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="archTotalPages > 1" class="flex items-center justify-center gap-2 mt-6">
        <button @click="loadArchive(archPage - 1)" :disabled="archPage === 1"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40">← Назад</button>
        <span class="text-sm text-gray-500">{{ archPage }} / {{ archTotalPages }}</span>
        <button @click="loadArchive(archPage + 1)" :disabled="archPage === archTotalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40">Вперёд →</button>
      </div>
    </div>

    <!-- ══ КОНСТРУКТОР (база знаний) ═══════════════════════════════════════ -->
    <div v-if="activeSection === 'constructor'" class="max-w-6xl mx-auto px-4 py-4">
      <KbConstructor :read-only="templatesReadOnly" />
    </div>

    <!-- ══ ШАБЛОНЫ ══════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'templates'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex justify-between items-start gap-3 mb-4">
        <div>
          <h2 class="font-semibold text-gray-800">Шаблоны ответов</h2>
          <p class="text-xs text-gray-500 mt-0.5 max-w-xl">
            Кнопки под отзывом. Двойной щелчок по кнопке в очереди открывает её текст здесь.
          </p>
        </div>
        <button v-if="!templatesReadOnly" @click="startNewQuickTemplate()" class="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors shrink-0">
          + Создать шаблон
        </button>
      </div>

      <h3 class="text-sm font-semibold text-gray-800 mb-2">Сценарии</h3>
      <div class="space-y-2 mb-6">
        <div v-for="card in scenarioCards" :key="card.key" :id="'review-scenario-' + card.key"
          :class="['bg-white rounded-xl border px-4 py-3',
            focusedTplKey === card.key ? 'border-indigo-400 ring-2 ring-indigo-100' : 'border-gray-200']">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="font-medium text-gray-800 text-sm">{{ card.label }}</p>
              <p class="text-sm text-gray-500 line-clamp-2 mt-0.5">{{ card.body || 'Текст ещё не задан' }}</p>
            </div>
            <button v-if="!templatesReadOnly" type="button" @click="editScenarioCard(card)"
              class="text-xs text-indigo-600 hover:underline shrink-0">Править</button>
          </div>
        </div>
      </div>

      <div v-if="tplFormOpen" id="review-quick-template-form" class="bg-white border border-indigo-200 rounded-xl shadow-sm p-5 mb-4">
        <h3 class="font-semibold text-gray-800 mb-4">{{ editTpl.title || (editTpl.id ? 'Редактировать' : 'Новый шаблон') }}</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Название</label>
            <input v-model="editTpl.title" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Площадка</label>
            <select v-model="editTpl.mp" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="all">Все</option>
              <option value="wb">WB</option>
              <option value="ym">ЯМ</option>
              <option value="ozon">Ozon</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Рейтинг от</label>
            <input type="number" v-model.number="editTpl.min_rating" min="1" max="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Рейтинг до</label>
            <input type="number" v-model.number="editTpl.max_rating" min="1" max="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" />
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Текст шаблона</label>
            <textarea v-model="editTpl.body" rows="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Стоп-слова (по одному на строку)</label>
            <textarea v-model="editTpl.stop_words" rows="2" placeholder="брак&#10;возврат"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2 flex flex-wrap gap-6">
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.auto_reply" class="w-4 h-4 rounded accent-indigo-600" />
              Автоответ
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.requires_confirmation" class="w-4 h-4 rounded accent-amber-500" />
              Требует подтверждения
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.ignore_questions" class="w-4 h-4 rounded accent-indigo-600" />
              Игнорировать вопросительные
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.require_no_text" class="w-4 h-4 rounded accent-indigo-600" />
              Только без текста
            </label>
          </div>
        </div>
        <div class="flex gap-2 mt-4">
          <button @click="saveTpl" class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">
            {{ editTpl.id ? 'Сохранить' : 'Создать' }}
          </button>
          <button @click="tplFormOpen = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
            Отмена
          </button>
        </div>
      </div>

      <h3 class="text-sm font-semibold text-gray-800 mb-2">Готовые ответы</h3>
      <div v-if="tplLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="otherTemplates.length === 0" class="text-center py-10 text-gray-400 text-sm">Готовых ответов пока нет</div>
      <div v-else class="space-y-2">
        <div v-for="t in otherTemplates" :key="t.id" :id="'review-tpl-' + t.id"
          :class="['bg-white rounded-xl border shadow-sm px-4 py-3',
            focusedTplKey === ('tpl-' + t.id) ? 'border-indigo-400 ring-2 ring-indigo-100' : 'border-gray-200']">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-2 mb-1">
                <span class="font-medium text-gray-800 text-sm">{{ t.title }}</span>
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[t.mp] || 'bg-gray-100 text-gray-500']">{{ t.mp }}</span>
                <span v-if="t.auto_reply" class="text-xs bg-green-50 text-green-700 border border-green-200 rounded px-1.5">авто</span>
                <span v-if="t.requires_confirmation" class="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5">подтверждение</span>
                <span v-if="t.stop_words" class="text-xs bg-red-50 text-red-600 border border-red-200 rounded px-1.5">стоп-слова</span>
              </div>
              <p class="text-sm text-gray-500 line-clamp-2">{{ t.body }}</p>
            </div>
            <div v-if="!templatesReadOnly" class="flex gap-2 shrink-0">
              <button @click="openReviewQuickTemplate(t)" class="text-xs text-indigo-600 hover:underline">Править</button>
              <button @click="deleteTpl(t.id)" class="text-xs text-red-500 hover:underline">Удалить</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ СТАТИСТИКА ══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'stats'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex items-center gap-3 mb-4">
        <label class="text-sm text-gray-600">Период:</label>
        <select v-model.number="statsDays" @change="loadStats"
          class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm bg-white text-gray-700">
          <option :value="7">7 дней</option>
          <option :value="14">14 дней</option>
          <option :value="30">30 дней</option>
        </select>
        <p class="text-xs text-gray-400">Цифры из журнала реальных отправок по отзывам</p>
      </div>

      <div v-if="statsLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statTotal }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Ответов отправлено</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statAuto }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Автоответов</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statManual }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Вручную</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ (statsData.managers || []).length }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Сотрудников</p>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
            <h3 class="font-medium text-gray-700 mb-3 text-sm">По площадкам</h3>
            <div v-if="Object.keys(statsData.by_mp || {}).length" class="space-y-2">
              <div v-for="(cnt, mp) in statsData.by_mp" :key="mp" class="flex items-center justify-between text-sm">
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[mp] || 'bg-gray-100 text-gray-600']">{{ mp }}</span>
                <span class="font-semibold text-gray-800">{{ cnt }}</span>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm text-center py-4">Нет данных</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
            <h3 class="font-medium text-gray-700 mb-3 text-sm">По сотрудникам</h3>
            <div v-if="(statsData.managers || []).length" class="space-y-2">
              <div v-for="m in statsData.managers" :key="m.manager"
                class="flex items-center justify-between text-sm border-b border-gray-50 pb-2 last:border-0">
                <span class="text-gray-700 truncate">{{ m.manager === 'auto_reply' ? 'Автоответы' : m.manager }}</span>
                <div class="flex items-center gap-3 shrink-0 text-xs text-gray-400">
                  <span>отзывы {{ m.reviews }}</span>
                  <span class="font-semibold text-gray-800 text-sm">{{ m.total }}</span>
                </div>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm text-center py-4">Нет данных за период</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ АВТООТВЕТЫ ══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'settings' && permissionsStore.isAdmin" class="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <div class="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div class="px-5 py-3 border-b border-gray-100">
          <h2 class="text-sm font-semibold text-gray-900">Площадки</h2>
          <p class="text-[11px] text-gray-400 mt-0.5">
            Если площадка выкл — система ничего не отправляет сама, даже при включённых сценариях.
          </p>
        </div>
        <div class="divide-y divide-gray-100">
          <div v-for="row in autoMpRows" :key="row.mp"
            class="px-5 py-4 flex items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[row.mp]]">{{ row.label }}</span>
                <p class="text-xs font-medium text-gray-800">Автоотправка ответа</p>
              </div>
              <p class="text-[11px] text-gray-400 mt-1">{{ row.hint }}</p>
            </div>
            <button type="button" role="switch"
              :aria-checked="!!autoEnabled[row.mp]"
              :disabled="autoSaving"
              @click="toggleAutoMp(row.mp)"
              :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                autoEnabled[row.mp] ? 'bg-rose-500' : 'bg-gray-200']">
              <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                autoEnabled[row.mp] ? 'translate-x-5' : '']"/>
            </button>
          </div>
        </div>
      </div>

      <div class="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div class="px-5 py-3 border-b border-gray-100">
          <h2 class="text-sm font-semibold text-gray-900">Сценарии</h2>
          <p class="text-[11px] text-gray-400 mt-0.5">
            Можно включить все сразу или только нужные. С текстом или фото система сама не отвечает.
          </p>
        </div>
        <div class="divide-y divide-gray-100">
          <div class="px-5 py-4 flex items-start justify-between gap-4">
            <div class="min-w-0">
              <p class="text-xs font-medium text-gray-800">Все сценарии сразу</p>
              <p class="text-[11px] text-gray-400 mt-1">5★, 4★ и 1–3★ без текста и фото</p>
            </div>
            <button type="button" role="switch"
              :aria-checked="allAutoScenariosOn"
              :disabled="autoSaving"
              @click="toggleAllAutoScenarios"
              :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                allAutoScenariosOn ? 'bg-rose-500' : 'bg-gray-200']">
              <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                allAutoScenariosOn ? 'translate-x-5' : '']"/>
            </button>
          </div>
          <div v-for="row in autoScenarioRows" :key="row.key"
            class="px-5 py-4 flex items-start justify-between gap-4">
            <div class="min-w-0">
              <p class="text-xs font-medium text-gray-800">{{ row.label }}</p>
              <p class="text-[11px] text-gray-400 mt-1">{{ row.hint }}</p>
            </div>
            <button type="button" role="switch"
              :aria-checked="!!autoScenarios[row.key]"
              :disabled="autoSaving"
              @click="toggleAutoScenario(row.key)"
              :class="['relative shrink-0 w-11 h-6 rounded-full transition-colors',
                autoScenarios[row.key] ? 'bg-rose-500' : 'bg-gray-200']">
              <span :class="['absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform',
                autoScenarios[row.key] ? 'translate-x-5' : '']"/>
            </button>
          </div>
        </div>
      </div>

      <div class="bg-white border border-gray-200 rounded-xl p-5">
        <p class="text-sm text-gray-600 leading-relaxed">
          Когда включите тумблер, само уйдёт только выбранный сценарий на включённой площадке.
        </p>
        <p class="text-sm text-gray-600 mt-3 leading-relaxed">
          Сами не уходят: 4–5 с текстом или фото — черновик на одобрение;
          1–3 с текстом или фото — пустое поле, ответ только через кнопку «ИИ».
        </p>
      </div>
    </div>

    <!-- Карточка отзыва -->
    <ReviewCard v-if="reviewCard.show" :mp="reviewCard.mp" :review-id="reviewCard.id" @close="closeReviewCard" />

    <!-- Всплывающая подсказка (полный текст) -->
    <div v-if="tip.show" class="fixed z-[60] pointer-events-none max-w-md px-3 py-2 rounded-lg bg-gray-900 text-white text-sm leading-snug shadow-xl whitespace-pre-wrap"
      :style="{ left: tip.x + 'px', top: tip.y + 'px' }">
      {{ tip.text }}
    </div>

    <!-- Лайтбокс фото отзыва -->
    <div v-if="lightbox.show" @click="closeLightbox"
      class="fixed inset-0 z-[70] bg-black/80 flex items-center justify-center p-4">
      <button @click.stop="closeLightbox" class="absolute top-4 right-5 text-white/80 hover:text-white text-4xl leading-none">×</button>
      <button v-if="lightbox.photos.length > 1" @click.stop="prevPhoto"
        class="absolute left-4 md:left-10 text-white/70 hover:text-white text-5xl leading-none select-none">‹</button>
      <img :src="lightbox.photos[lightbox.index]" @click.stop
        class="max-w-full max-h-[85vh] rounded-lg object-contain shadow-2xl" />
      <button v-if="lightbox.photos.length > 1" @click.stop="nextPhoto"
        class="absolute right-4 md:right-10 text-white/70 hover:text-white text-5xl leading-none select-none">›</button>
      <span v-if="lightbox.photos.length > 1"
        class="absolute bottom-5 left-1/2 -translate-x-1/2 text-white/80 text-sm bg-black/40 rounded-full px-3 py-1">
        {{ lightbox.index + 1 }} / {{ lightbox.photos.length }}
      </span>
    </div>

    <!-- Toast -->
    <transition name="toast">
      <div v-if="toast" :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white max-w-sm',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">
        {{ toast.msg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { apiFetch } from '../api'
import { centrifugoUrl } from '../realtime'
import { usePlatformStore } from '../stores/platform'
import { usePermissionsStore } from '../stores/permissions'
import KbConstructor from '../components/KbConstructor.vue'
import ReviewCard from '../components/ReviewCard.vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { Centrifuge } from 'centrifuge'
import { useToast } from '../composables/useToast'
import { useCxTemplates } from '../composables/useCxTemplates'
import { useCxQueueActions } from '../composables/useCxQueueActions'
import { CX_BASE as BASE, MP_BADGE_CLASSES as mpBadgeCls, fmtAge, ageCls, fmtDateTime, withReviewSignature, reviewProductPhoto } from '../utils/cx'

const platformStore = usePlatformStore()
const permissionsStore = usePermissionsStore()
const currentMp = computed(() => platformStore.platform || 'all')
/** Правка шаблонов — администратор и менеджер КС. Автоответы — только администратор. */
const canEditReviewTemplates = computed(() =>
  permissionsStore.isAdmin || permissionsStore.role === 'cs_manager'
)
const templatesReadOnly = computed(() => !canEditReviewTemplates.value)

const { toast, showToast } = useToast()

// ── Навигация ──────────────────────────────────────────────────────────────
const visibleSections = computed(() => {
  const list = [
    { key: 'queue', label: 'Очередь' },
    { key: 'archive', label: 'Архив' },
    { key: 'constructor', label: 'Конструктор' },
  ]
  if (canEditReviewTemplates.value) list.push({ key: 'templates', label: 'Шаблоны' })
  list.push({ key: 'stats', label: 'Статистика' })
  if (permissionsStore.isAdmin) list.push({ key: 'settings', label: 'Автоответы' })
  return list
})
const activeSection = ref('queue')

function switchSection(key) {
  activeSection.value = key
  if (key === 'archive' && archItems.value.length === 0) loadArchive(1)
  if (key === 'stats' && !statsData.value.managers) loadStats()
  if (key === 'settings') loadAutoSettings()
}

watch(currentMp, () => {
  if (activeSection.value === 'queue')   loadQueue()
  if (activeSection.value === 'archive') loadArchive(1)
})

// ── Очередь ────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const counts       = ref({})
const perPage      = ref(25)
const currentPage  = ref(1)
const sortDesc     = ref(true)

// Фильтры по колонкам
const fSku      = ref('')
const fText     = ref('')
const fRatings  = ref([])
const fStatus   = ref('')
const fKind     = ref('')   // '' | 'updated' | 'new'
const fHasText  = ref('')   // '' | 'with' | 'without'
const fDateFrom = ref('')
const fDateTo   = ref('')
const updatedCount = computed(() =>
  (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'review' && i.update_kind).length
)

function _hasBodyText(i) {
  const b = (i.body || '').trim()
  return b !== '' && b !== '—'
}

function toggleQueueRating(n) {
  const i = fRatings.value.indexOf(n)
  if (i >= 0) fRatings.value = fRatings.value.filter(x => x !== n)
  else fRatings.value = [...fRatings.value, n]
}

function resetQueueFilters() {
  fSku.value = ''; fText.value = ''; fRatings.value = []; fStatus.value = ''
  fKind.value = ''; fHasText.value = ''; fDateFrom.value = ''; fDateTo.value = ''
}

// Выбор строк для массовых действий
const selectedIds = ref([])

function rowKey(item) {
  return `${item.mp}:${item.id}`
}

function toggleSort() {
  sortDesc.value = !sortDesc.value
  currentPage.value = 1
}

function hasDraft(item) {
  return !!(item._reply && item._reply.trim())
}

function clearDraft(item) {
  item._reply = ''
  item.pending_template = ''
  item._manual_check = false
}

function clearSelectedDrafts() {
  const keys = new Set(selectedIds.value)
  let n = 0
  for (const item of filteredItems.value) {
    if (keys.has(rowKey(item)) && hasDraft(item)) {
      clearDraft(item)
      n++
    }
  }
  if (n) showToast(`Очищено черновиков: ${n}`, 'ok')
}

const filteredItems = computed(() => {
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'review')

  if (fSku.value.trim()) {
    const q = fSku.value.trim().toLowerCase()
    list = list.filter(i => String(i.sku || '').toLowerCase().includes(q) || String(i.nm_id || '').includes(q))
  }
  if (fText.value.trim()) {
    const q = fText.value.trim().toLowerCase()
    list = list.filter(i => {
      const hay = [i.body, i.pros, i.cons, i.sku, i.nm_id].map(x => String(x || '').toLowerCase()).join(' ')
      return hay.includes(q)
    })
  }
  if (fRatings.value.length) {
    const want = new Set(fRatings.value.map(String))
    list = list.filter(i => want.has(String(i.rating)))
  }
  if (fHasText.value === 'with')    list = list.filter(_hasBodyText)
  if (fHasText.value === 'without') list = list.filter(i => !_hasBodyText(i))
  if (fKind.value === 'updated') list = list.filter(i => !!i.update_kind)
  if (fKind.value === 'new') list = list.filter(i => !i.update_kind)
  if (fStatus.value === 'draft') list = list.filter(i => hasDraft(i))
  if (fStatus.value === 'check') list = list.filter(i => !!i._manual_check)
  if (fStatus.value === 'none')  list = list.filter(i => !hasDraft(i))
  if (fDateFrom.value) {
    const from = new Date(fDateFrom.value + 'T00:00:00')
    list = list.filter(i => i.created_at && new Date(i.created_at) >= from)
  }
  if (fDateTo.value) {
    const to = new Date(fDateTo.value + 'T23:59:59')
    list = list.filter(i => i.created_at && new Date(i.created_at) <= to)
  }

  const dir = sortDesc.value ? 1 : -1
  return [...list].sort((a, b) => ((a.age_sec || 0) - (b.age_sec || 0)) * dir)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))
const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

// Черновики (строки с заполненным текстом ответа)
const draftItems = computed(() => filteredItems.value.filter(hasDraft))
const selectedDraftIds = computed(() =>
  selectedIds.value.filter(id => draftItems.value.some(i => rowKey(i) === id))
)
const allSelected = computed(() =>
  pagedItems.value.length > 0 && pagedItems.value.every(i => selectedIds.value.includes(rowKey(i)))
)

function toggleSelectAll(e) {
  const keys = pagedItems.value.map(rowKey)
  if (e.target.checked) {
    selectedIds.value = [...new Set([...selectedIds.value, ...keys])]
  } else {
    const drop = new Set(keys)
    selectedIds.value = selectedIds.value.filter(k => !drop.has(k))
  }
}

// Сброс фильтров и страницы при их изменении
watch([fSku, fText, fRatings, fStatus, fKind, fHasText, fDateFrom, fDateTo], () => { currentPage.value = 1 })

async function loadQueue() {
  queueLoading.value = true
  currentPage.value  = 1
  selectedIds.value  = []
  try {
    const res = await apiFetch(`${BASE}/queue?mp=${currentMp.value}`)
    const data = await res.json()
    // Предзаполняем текст ответа черновиком с модерации, если он есть
    queueItems.value = (data.items || []).map(i => ({
      ...i,
      _reply: withReviewSignature(i.pending_draft || ''),
      _generating: false,
      _manual_check: !!i.manual_check,
      _topic: i.pending_topic || '',
    }))
    counts.value = data.counts || {}
  } catch {
    showToast('Ошибка загрузки очереди', 'error')
  } finally {
    queueLoading.value = false
  }
}

// ── Генерация ответов ────────────────────────────────────────────────────────
const bulkGenerating = ref(false)   // 'tpl' | 'ai' | false

function _fullText(item) {
  return [item.body === '—' ? '' : item.body, item.pros, item.cons].filter(Boolean).join('. ')
}

// 📋 Из шаблонов (детерминированно, через базу знаний)
function isAiOnly(item) {
  return item.item_type === 'review' && item.reply_mode === 'ai_only'
}

async function generateTemplate(item, { quiet = false } = {}) {
  if (item._generating) return false
  item._generating = 'tpl'
  try {
    const res = await apiFetch('/api/v1/kb/build', {
      method: 'POST',
      body: JSON.stringify({
        supplier_article: item.sku || '',
        text: _fullText(item),
        rating: item.rating || 0,
        use_overlay: Boolean(item._topic),
        avoid: item._reply || '',
        topic: item._topic || '',
      }),
    })
    const data = await res.json()
    if (data.ok && data.answer) {
      item._reply = withReviewSignature(data.answer.trim())
      item.pending_template = data.scenario || ''
      item._manual_check = !!data.manual_check
      return true
    }
    item.pending_template = data.scenario || 'Не подобрано'
    if (!quiet) showToast(data.scenario || 'Шаблон не подобрался', 'error')
    return false
  } catch (e) {
    if (!quiet) showToast(e.message || 'Не удалось собрать шаблон', 'error')
    return false
  } finally {
    item._generating = false
  }
}

const AI_DRAFT_TIMEOUT_MS = 120000

// ✨ ИИ (Qwen Plus, с опорой на фирменные шаблоны)
async function generateAI(item, { quiet = false } = {}) {
  if (item._generating) return false
  item._generating = 'ai'
  item._aiError = ''
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), AI_DRAFT_TIMEOUT_MS)
  try {
    const res = await apiFetch(`${BASE}/draft`, {
      method: 'POST',
      signal: ac.signal,
      body: JSON.stringify({
        item_type: 'review',
        mp: item.mp || 'wb',
        body: item.body === '—' ? '' : (item.body || ''),
        rating: item.rating || 0,
        pros: item.pros || '',
        cons: item.cons || '',
        sku: item.sku || '',
        has_media: item.photos && item.photos.length ? '1' : '',
      }),
    })
    const data = await res.json()
    item._reply = withReviewSignature((data.draft || '').trim())
    item.pending_template = 'ИИ'
    if (!quiet) showToast('Черновик ИИ готов', 'ok')
    return true
    } catch (e) {
    const raw = e.message || ''
    const msg = e.name === 'AbortError'
      ? 'ИИ не ответил за 2 минуты — нажмите ещё раз'
      : /quota|free tier|403|лимит|паузе/i.test(raw)
        ? 'Сейчас ИИ недоступен — закончился бесплатный лимит. Напишите ответ вручную или нажмите шаблон.'
        : (raw || 'ИИ недоступен')
    item._aiError = msg
    if (!quiet) showToast(msg, 'error')
    return false
  } finally {
    clearTimeout(timer)
    item._generating = false
  }
}

async function _bulkGenerate(mode, fn, allow = null) {
  const items = filteredItems.value.filter(i =>
    selectedIds.value.includes(rowKey(i)) && (!allow || allow(i))
  )
  if (!items.length) return
  if (items.length > 1) {
    const label = mode === 'ai' ? 'ИИ' : 'шаблон'
    if (!confirm(`Сгенерировать ${label} для ${items.length} выбранных отзывов?`)) return
  }
  bulkGenerating.value = mode
  let ok = 0, fail = 0, lastErr = ''
  for (const item of items) {
    if (await fn(item)) ok++; else { fail++; lastErr = item._aiError || lastErr }
  }
  bulkGenerating.value = false
  if (ok === 0 && lastErr) showToast('Ошибка: ' + lastErr, 'error')
  else showToast(fail ? `Готово ${ok}, ошибок ${fail}` : `Сгенерировано ответов: ${ok}`, fail ? 'error' : 'ok')
}

function generateSelectedTemplate() {
  _bulkGenerate('tpl', item => generateTemplate(item, { quiet: true }))
}
function generateSelectedAI() {
  _bulkGenerate('ai', item => generateAI(item, { quiet: true }))
}

// ── Массовая отправка ────────────────────────────────────────────────────────
const bulkSending = ref(false)

async function _sendOne(item) {
  const txt = item._reply?.trim()
  if (!txt) return { ok: false, error: 'Пустой текст' }
  const path = replyEndpoint(item)
  if (!path) return { ok: false, error: 'Этот отзыв нельзя отправить' }
  try {
    const res = await apiFetch(`${BASE}${path}`, { method: 'POST', body: JSON.stringify({ text: txt }) })
    const data = await res.json().catch(() => ({}))
    return { ok: true, via: data?.via || '' }
  } catch (e) {
    return { ok: false, error: e?.message || 'Ошибка отправки' }
  }
}

async function _bulkSend(items) {
  if (!items.length) return
  bulkSending.value = true
  let sent = 0, queued = 0, fail = 0, lastErr = ''
  const done = new Set()
  for (const item of items) {
    const result = await _sendOne(item)
    if (!result.ok) {
      fail++
      lastErr = result.error || lastErr
      continue
    }
    done.add(rowKey(item))
    if (result.via === 'cabinet_bot') queued++
    else sent++
  }
  queueItems.value = queueItems.value.filter(i => !done.has(rowKey(i)))
  selectedIds.value = selectedIds.value.filter(k => !done.has(k))
  bulkSending.value = false
  if (fail && !sent && !queued) {
    showToast(lastErr ? `Ошибка: ${lastErr}` : `Ошибок: ${fail}`, 'error')
    return
  }
  const parts = []
  if (sent) parts.push(`Отправлено ${sent}`)
  if (queued) parts.push(`Бот отправит клиентам: ${queued}`)
  if (fail) parts.push(`ошибок ${fail}${lastErr ? ` (${lastErr})` : ''}`)
  showToast(parts.join(', '), fail ? 'error' : 'ok')
}

function confirmSelected() {
  const items = draftItems.value.filter(i => selectedIds.value.includes(rowKey(i)))
  _bulkSend(items)
}

function confirmAll() {
  if (!confirm(`Отправить все черновики (${draftItems.value.length})?`)) return
  _bulkSend([...draftItems.value])
}

// ── Статус строки ────────────────────────────────────────────────────────────
function statusLabel(item) {
  if (item.pending_mod_id) return 'На модерации'
  if (item._manual_check)  return 'Проверить'
  if (hasDraft(item))      return 'Черновик'
  return 'Не отправлено'
}
function statusCls(item) {
  if (item.pending_mod_id) return 'bg-amber-50 text-amber-700 border-amber-200'
  if (item._manual_check)  return 'bg-amber-50 text-amber-700 border-amber-200'
  if (hasDraft(item))      return 'bg-blue-50 text-blue-700 border-blue-200'
  return 'bg-gray-100 text-gray-500 border-gray-200'
}

function archiveStatusKind(item) {
  if (item.archive_reason === 'dismissed') return 'dismissed'
  if ((item.answer_text || '').trim()) return 'answered'
  return 'wb_empty'
}
function archiveStatusLabel(item) {
  const kind = archiveStatusKind(item)
  if (kind === 'dismissed') return 'Закрыто без ответа'
  if (kind === 'wb_empty') return 'На WB без ответа'
  return 'Отвечено'
}
function archiveStatusCls(item) {
  const kind = archiveStatusKind(item)
  if (kind === 'dismissed') return 'bg-gray-100 text-gray-500 border-gray-200'
  if (kind === 'wb_empty') return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-green-50 text-green-700 border-green-200'
}

// ── Отправка / модерация / dismiss (общая логика — см. composables/useCxQueueActions) ──
function replyEndpoint(item) {
  const id = encodeURIComponent(String(item.id))
  const map = {
    'wb:review':   `/wb/feedbacks/${id}/answer`,
    'ym:review':   `/ym/feedbacks/${id}/answer`,
    'ozon:review': `/ozon/reviews/${id}/answer`,
  }
  return map[`${item.mp}:${item.item_type}`] || null
}

// confirmMod/rejectMod здесь не нужны — таблица отзывов не показывает
// карточку модерации черновика (она есть у вопросов), поэтому не подключаем.
const { sendReply: _sendReply, dismissItem } = useCxQueueActions(queueItems, showToast)
function sendReply(item) { return _sendReply(item, replyEndpoint) }

// ── Шаблоны (общая логика — см. composables/useCxTemplates) ─────────────────
const {
  templates, tplLoading, tplFormOpen, editTpl,
  loadTemplates, openTplForm, saveTpl, deleteTpl, relevantTemplates, looksLikePaintHoles,
} = useCxTemplates('review', showToast, { defaultMinRating: 1, defaultMaxRating: 5 })

const reviewTopics = ref([
  { id: 'incomplete', label: 'Некомплект' },
  { id: 'defect', label: 'Брак' },
  { id: 'delivery', label: 'Упаковка / доставка' },
  { id: 'assembly', label: 'Сборка' },
  { id: 'load', label: 'Нагрузка' },
  { id: 'unsuitable', label: 'Не подошло' },
  { id: 'size', label: 'Размеры' },
  { id: 'already_returned', label: 'Уже возврат' },
  { id: 'four_ask', label: '4★ уточнить' },
  { id: 'paint_holes', label: 'Технологические отверстия' },
])

const THANKS_5 = [
  { id: 'thanks_5_a', label: 'Спасибо' },
  { id: 'thanks_5_b', label: 'Рады оценке' },
  { id: 'thanks_5_c', label: 'Приятного пользования' },
]
const THANKS_5_TEXT = {
  thanks_5_a: 'Здравствуйте! Спасибо за высокую оценку — нам очень приятно.',
  thanks_5_b: 'Здравствуйте! Благодарим за пятёрку. Рады, что товар вам понравился.',
  thanks_5_c: 'Здравствуйте! Спасибо, что поделились впечатлением. Приятного пользования!',
}
const SCENARIO_DEFAULT = {
  incomplete: 'Здравствуйте! Очень жаль, что в комплекте не хватило деталей. Напишите, пожалуйста, в «Чат с продавцом» — разберёмся.',
  defect: 'Здравствуйте. Очень жаль, что товар пришёл с браком. Оформите, пожалуйста, возврат по браку в личном кабинете. Если нужна помощь — напишите в «Чат с продавцом».',
  delivery: 'Здравствуйте. Очень жаль, что товар приехал в помятой или порванной упаковке. Напишите, пожалуйста, в «Чат с продавцом» — разберёмся.',
  assembly: 'Здравствуйте. Очень жаль, что со сборкой вышло неудобно. Если не получится — напишите в «Чат с продавцом», подскажем.',
  load: 'Здравствуйте. Жаль, что товар не оправдал ожидания. Важно собрать по инструкции и не превышать допустимую нагрузку.',
  unsuitable: 'Здравствуйте! Жаль, что товар не до конца подошёл. Спасибо, что выбрали нас — будем работать над ассортиментом.',
  size: 'Здравствуйте! Спасибо за отзыв. Размеры указаны в карточке товара.',
  already_returned: 'Здравствуйте. Очень жаль, что так вышло.',
  four_ask: 'Здравствуйте! Спасибо за оценку! Подскажите, чего не хватило до «пятёрки»? Напишите в «Чат с продавцом» — это поможет нам стать лучше.',
  paint_holes: 'Понимаем, выглядит необычно. Это технологические отверстия: за них деталь вешают на линии покраски. Собирать через них ничего не нужно, на устойчивость не влияют.',
}
const focusedTplKey = ref('')

function topicLabel(item) {
  const id = item?._topic || item?.pending_topic || ''
  return (THANKS_5.find(t => t.id === id) || reviewTopics.value.find(t => t.id === id) || {}).label || ''
}

function scenarioText(item) {
  return item?.pending_template || topicLabel(item) || ''
}

function topicsFor(item) {
  const rating = Number(item?.rating) || 0
  const problem = reviewTopics.value.filter(tp => {
    if (tp.id !== 'paint_holes') return true
    return looksLikePaintHoles(item) || item._topic === 'paint_holes'
  })
  if (rating >= 5) return [...THANKS_5, ...problem]
  return problem
}

function scenarioTitle(topicId) {
  const label = topicLabel({ _topic: topicId })
  return label ? `Сценарий · ${label}` : ''
}

function scenarioTemplate(topicId) {
  const title = scenarioTitle(topicId)
  if (!title) return null
  return (templates.value || []).find(t => t.title === title) || null
}

function scenarioBody(topicId) {
  return scenarioTemplate(topicId)?.body || THANKS_5_TEXT[topicId] || SCENARIO_DEFAULT[topicId] || ''
}

const scenarioCards = computed(() => [...THANKS_5, ...reviewTopics.value].map(tp => ({
  key: tp.id,
  label: tp.label,
  body: scenarioBody(tp.id),
  saved: scenarioTemplate(tp.id),
})))

const otherTemplates = computed(() =>
  (templates.value || []).filter(t => !String(t.title || '').startsWith('Сценарий ·'))
)

function quickReplyTemplates(item) {
  return relevantTemplates(item).filter(t => !String(t.title || '').startsWith('Сценарий ·'))
}

function blankReviewTpl(over = {}) {
  return {
    title: '', body: '', mp: 'all', item_type: 'review', tone: 'neutral',
    auto_reply: false, min_rating: 1, max_rating: 5, require_no_text: false,
    stop_words: '', ignore_questions: false, requires_confirmation: false,
    ...over,
  }
}

async function scrollToReviewEditor() {
  await nextTick()
  document.getElementById('review-quick-template-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function startNewQuickTemplate() {
  focusedTplKey.value = 'new'
  openTplForm(blankReviewTpl())
  scrollToReviewEditor()
}

function editScenarioCard(card) {
  if (!canEditReviewTemplates.value || !card) return
  focusedTplKey.value = card.key
  const isThanks = Boolean(THANKS_5_TEXT[card.key])
  if (card.saved) openTplForm({ ...card.saved })
  else openTplForm(blankReviewTpl({
    title: `Сценарий · ${card.label}`,
    body: card.body,
    min_rating: isThanks ? 5 : 1,
    max_rating: 5,
  }))
  switchSection('templates')
  scrollToReviewEditor()
}

function openReviewScenarioEditor(topicId) {
  if (!canEditReviewTemplates.value) return
  const card = scenarioCards.value.find(c => c.key === topicId)
  if (card) {
    editScenarioCard(card)
    return
  }
  switchSection('templates')
  scrollToReviewEditor()
}

async function openReviewQuickTemplate(tpl) {
  if (!canEditReviewTemplates.value || !tpl) return
  focusedTplKey.value = tpl.id ? `tpl-${tpl.id}` : 'new'
  switchSection('templates')
  openTplForm(tpl.id ? tpl : blankReviewTpl(tpl))
  await scrollToReviewEditor()
}

const pendingClicks = new Map()
function runUnlessDouble(key, fn) {
  if (pendingClicks.has(key)) {
    clearTimeout(pendingClicks.get(key))
    pendingClicks.delete(key)
    return
  }
  pendingClicks.set(key, setTimeout(() => {
    pendingClicks.delete(key)
    fn()
  }, 250))
}
function cancelPendingClick(key) {
  const timer = pendingClicks.get(key)
  if (!timer) return
  clearTimeout(timer)
  pendingClicks.delete(key)
}

function cycleScenario(item) {
  const list = topicsFor(item)
  if (!list.length) return
  const cur = item._topic || ''
  const i = list.findIndex(t => t.id === cur)
  selectTopic(item, list[(i + 1) % list.length].id)
}

async function loadReviewTopics() {
  try {
    const r = await apiFetch('/api/v1/kb/topics')
    const d = await r.json()
    if (Array.isArray(d) && d.length) reviewTopics.value = d
  } catch { /* оставляем запасной список */ }
}

async function selectTopic(item, topicId) {
  item._topic = item._topic === topicId ? '' : topicId
  if (!item._topic) return
  const savedScenario = scenarioTemplate(item._topic)
  if (savedScenario?.body) {
    item._reply = withReviewSignature(savedScenario.body)
    item.pending_template = topicLabel(item)
    item._manual_check = false
    return
  }
  if (THANKS_5_TEXT[item._topic]) {
    item._reply = withReviewSignature(THANKS_5_TEXT[item._topic])
    item.pending_template = topicLabel(item)
    item._manual_check = false
    return
  }
  await generateTemplate(item)
}

// ── Архив ──────────────────────────────────────────────────────────────────
const archItems      = ref([])
const archLoading    = ref(false)
const archPage       = ref(1)
const archTotalPages = ref(1)

// Фильтры архива (серверные)
const aSku      = ref('')
const aText     = ref('')
const aRatings  = ref([])
const aHasText  = ref('')
const aStatus   = ref('')
const aDateFrom = ref('')
const aDateTo   = ref('')

function toggleArchiveRating(n) {
  const i = aRatings.value.indexOf(n)
  if (i >= 0) aRatings.value = aRatings.value.filter(x => x !== n)
  else aRatings.value = [...aRatings.value, n]
  loadArchive(1)
}

async function loadArchive(page = 1) {
  archLoading.value = true
  archPage.value = page
  try {
    const params = new URLSearchParams({
      mp: currentMp.value, item_type: 'review', page, per_page: 25,
    })
    if (aSku.value.trim()) params.set('sku', aSku.value.trim())
    if (aText.value.trim()) params.set('q', aText.value.trim())
    if (aRatings.value.length) params.set('ratings', aRatings.value.join(','))
    if (aHasText.value)    params.set('has_text', aHasText.value)
    if (aStatus.value)     params.set('status', aStatus.value)
    if (aDateFrom.value)   params.set('date_from', aDateFrom.value)
    if (aDateTo.value)     params.set('date_to', aDateTo.value)
    const r = await apiFetch(`${BASE}/archive?${params}`)
    const d = await r.json()
    archItems.value      = d.items || []
    archTotalPages.value = d.total_pages || 1
  } finally {
    archLoading.value = false
  }
}

function resetArchiveFilters() {
  aSku.value = ''; aText.value = ''; aRatings.value = []
  aHasText.value = ''; aStatus.value = ''; aDateFrom.value = ''; aDateTo.value = ''
  clearTimeout(archFilterTimer)
  loadArchive(1)
}

let archFilterTimer = null
watch([aText, aSku], () => {
  if (activeSection.value !== 'archive') return
  clearTimeout(archFilterTimer)
  archFilterTimer = setTimeout(() => loadArchive(1), 400)
})

async function undismissItem(item) {
  await apiFetch(`${BASE}/dismiss/${item.mp}/${item.item_type}/${item.id}`, { method: 'DELETE' })
  await loadArchive(archPage.value)
  showToast('Возвращено в очередь', 'ok')
}

// ── Статистика ─────────────────────────────────────────────────────────────
const statsData    = ref({})
const statsLoading = ref(false)
const statsDays    = ref(7)

const statTotal  = computed(() => (statsData.value.managers || []).reduce((s, m) => s + (m.total || 0), 0))
const statAuto   = computed(() => (statsData.value.managers || []).reduce((s, m) => s + (m.auto_sent || 0), 0))
const statManual = computed(() => statTotal.value - statAuto.value)

async function loadStats() {
  statsLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/stats?days=${statsDays.value}&item_type=review`)
    statsData.value = await r.json()
  } finally {
    statsLoading.value = false
  }
}

// ── Автоответы по площадкам и сценариям ────────────────────────────────────
const autoEnabled = ref({ wb: false, ym: false, ozon: false })
const autoScenarios = ref({ thanks_5: true, thanks_4: true, sorry: true })
const autoSaving = ref(false)
const autoMpRows = [
  { mp: 'wb',   label: 'WB',   hint: 'Ответы уходят в кабинет Wildberries' },
  { mp: 'ym',   label: 'ЯМ',   hint: 'Ответы уходят в кабинет Яндекс Маркета' },
  { mp: 'ozon', label: 'Ozon', hint: 'Ответы уходят в кабинет Ozon (или в очередь, если API закрыт)' },
]
const autoScenarioRows = [
  { key: 'thanks_5', label: '5 звёзд без текста и фото', hint: 'Короткое спасибо' },
  { key: 'thanks_4', label: '4 звезды без текста и фото', hint: 'Спасибо и вопрос, чего не хватило до пятёрки' },
  { key: 'sorry',    label: '1–3 звезды без текста и фото', hint: 'Извинение и приглашение в чат' },
]
const allAutoScenariosOn = computed(() =>
  !!autoScenarios.value.thanks_5 && !!autoScenarios.value.thanks_4 && !!autoScenarios.value.sorry
)

function applyAutoSettings(d) {
  autoEnabled.value = { wb: false, ym: false, ozon: false, ...(d.enabled || {}) }
  autoScenarios.value = { thanks_5: true, thanks_4: true, sorry: true, ...(d.scenarios || {}) }
}

async function loadAutoSettings() {
  try {
    const r = await apiFetch(`${BASE}/review-auto-settings`)
    applyAutoSettings(await r.json())
  } catch {
    showToast('Не удалось загрузить настройки автоответов', 'error')
  }
  if (!templates.value.length) await loadTemplates()
}

async function saveAutoSettings(payload, revert) {
  autoSaving.value = true
  try {
    const r = await apiFetch(`${BASE}/review-auto-settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
    applyAutoSettings(await r.json())
  } catch {
    if (revert) revert()
    showToast('Не удалось сохранить настройку', 'error')
  } finally {
    autoSaving.value = false
  }
}

async function toggleAutoMp(mp) {
  const next = !autoEnabled.value[mp]
  autoEnabled.value = { ...autoEnabled.value, [mp]: next }
  await saveAutoSettings({ [mp]: next }, () => {
    autoEnabled.value = { ...autoEnabled.value, [mp]: !next }
  })
}

async function toggleAutoScenario(key) {
  const next = !autoScenarios.value[key]
  autoScenarios.value = { ...autoScenarios.value, [key]: next }
  await saveAutoSettings({ [key]: next }, () => {
    autoScenarios.value = { ...autoScenarios.value, [key]: !next }
  })
}

async function toggleAllAutoScenarios() {
  const next = !allAutoScenariosOn.value
  const prev = { ...autoScenarios.value }
  autoScenarios.value = { thanks_5: next, thanks_4: next, sorry: next }
  await saveAutoSettings(
    { thanks_5: next, thanks_4: next, sorry: next },
    () => { autoScenarios.value = prev },
  )
}

// ── Всплывающая подсказка ────────────────────────────────────────────────────
const tip = ref({ show: false, text: '', x: 0, y: 0 })
function _place(e) {
  // сместить чуть ниже-правее курсора, не вылезая за правый край
  const maxX = window.innerWidth - 480
  tip.value.x = Math.min(e.clientX + 14, maxX)
  tip.value.y = Math.min(e.clientY + 16, window.innerHeight - 80)
}
function showTip(e, text) {
  if (!text || !String(text).trim() || text === '—') return
  tip.value = { show: true, text, x: 0, y: 0 }
  _place(e)
}
function moveTip(e) { if (tip.value.show) _place(e) }
function hideTip() { tip.value.show = false }

// ── Лайтбокс фото ────────────────────────────────────────────────────────────
const lightbox = ref({ show: false, photos: [], index: 0 })
function openLightbox(photos, index = 0) {
  lightbox.value = { show: true, photos: [...photos], index }
}
function closeLightbox() { lightbox.value.show = false }
function nextPhoto() { lightbox.value.index = (lightbox.value.index + 1) % lightbox.value.photos.length }
function prevPhoto() { lightbox.value.index = (lightbox.value.index - 1 + lightbox.value.photos.length) % lightbox.value.photos.length }

function _lightboxKeys(e) {
  if (!lightbox.value.show) return
  if (e.key === 'Escape') closeLightbox()
  else if (e.key === 'ArrowRight') nextPhoto()
  else if (e.key === 'ArrowLeft') prevPhoto()
}

// ── Реалтайм новых отзывов (Centrifugo) ──────────────────────────────────────
const newReviews = ref(0)
let centrifuge = null
async function initCentrifugo() {
  try {
    const res = await apiFetch('/api/auth/centrifugo-token')
    const { token } = await res.json()
    centrifuge = new Centrifuge(centrifugoUrl(), { token })
    const sub = centrifuge.newSubscription('reviews')
    sub.on('publication', ctx => {
      if (ctx.data?.type === 'reviews_updated') newReviews.value += (ctx.data.count || 1)
    })
    sub.subscribe()
    centrifuge.connect()
  } catch (e) { /* нет токена/соединения — тихо игнорируем */ }
}
function refreshNewReviews() {
  newReviews.value = 0
  loadQueue()
}

// ── Карточка отзыва ──────────────────────────────────────────────────────────
const route = useRoute()
const router = useRouter()
const reviewCard = ref({ show: false, mp: '', id: '' })
function openReviewCard(item) {
  if (item.item_type && item.item_type !== 'review') return
  reviewCard.value = { show: true, mp: item.mp, id: String(item.id) }
  const key = `${item.mp}:${item.id}`
  if (String(route.query.review || '') !== key) {
    router.replace({ query: { ...route.query, review: key } })
  }
}
function closeReviewCard() {
  reviewCard.value.show = false
  if (route.query.review) {
    const q = { ...route.query }
    delete q.review
    router.replace({ query: q })
  }
}
function _openFromQuery() {
  const q = route.query.review   // формат "wb:<id>"
  if (q && typeof q === 'string' && q.includes(':')) {
    const [m, ...rest] = q.split(':')
    reviewCard.value = { show: true, mp: m, id: rest.join(':') }
  } else if (reviewCard.value.show) {
    reviewCard.value.show = false
  }
}

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  window.addEventListener('keydown', _lightboxKeys)
  _openFromQuery()
  initCentrifugo()
  await Promise.all([loadQueue(), loadTemplates(), loadReviewTopics()])
})
watch(() => route.query.review, () => { _openFromQuery() })
onUnmounted(() => {
  window.removeEventListener('keydown', _lightboxKeys)
  centrifuge?.disconnect()
  clearTimeout(archFilterTimer)
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.line-clamp-3 { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.line-clamp-4 { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
</style>
