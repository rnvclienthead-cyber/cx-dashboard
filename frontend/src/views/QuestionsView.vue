<template>
  <div class="min-h-screen bg-gray-50">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-7xl mx-auto px-5">

        <!-- Строка 1: заголовок + счётчики -->
        <div class="flex items-center justify-between gap-4 py-3">
          <div class="flex items-center gap-3">
            <div :class="['w-9 h-9 rounded-xl flex items-center justify-center shrink-0',
              hasQualityQuestionsAccess ? 'bg-violet-50 text-violet-600' : 'bg-sky-50 text-sky-600']">
              <ClipboardPenLine v-if="hasQualityQuestionsAccess" class="w-[18px] h-[18px]" aria-hidden="true" />
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <div>
              <h1 class="text-sm font-medium text-gray-900 leading-tight">
                {{ hasQualityQuestionsAccess ? 'Уточнения по вопросам' : 'Вопросы' }}
              </h1>
              <p class="text-xs text-gray-400 leading-tight mt-0.5">{{ questionsSubtitle }}</p>
            </div>
          </div>

          <div v-if="activeSection === 'queue'"
            class="flex items-center gap-2">
            <span v-if="counts.questions != null && counts.questions > 0"
              class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-sky-50 text-sky-700 border border-sky-200">
              <span class="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse"></span>
              {{ counts.questions }} {{ hasQualityQuestionsAccess ? 'уточнений' : 'вопросов' }}
            </span>
            <button v-if="canShowAnsweredFilter && answeredCount > 0"
              @click="toggleAnsweredFilter"
              :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors',
                showAnsweredOnly
                  ? 'bg-emerald-600 text-white border-emerald-600'
                  : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100']"
              :title="showAnsweredOnly ? 'Показать все вопросы' : 'Только вопросы с ответом качества'">
              Получен ответ · {{ answeredCount }}
            </button>
          </div>
        </div>

        <!-- Строка 2: вкладки секций -->
        <div class="flex -mb-px">
          <button v-for="s in visibleSections" :key="s.key"
            @click="switchSection(s.key)"
            :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition-all inline-flex items-center gap-1.5',
              activeSection === s.key
                ? 'border-sky-500 text-sky-600'
                : 'border-transparent text-gray-500 hover:text-gray-700']">
            {{ s.label }}
            <span v-if="s.key === 'review' && pendingTemplateCount > 0"
              class="inline-flex items-center justify-center min-w-[1.1rem] h-4 px-1 rounded-full text-[10px] font-semibold bg-amber-500 text-white">
              {{ pendingTemplateCount }}
            </span>
          </button>
        </div>
      </div>

      <!-- Строка 3: фильтр ответа качества + обновить -->
      <div v-if="activeSection === 'queue'" class="border-t border-gray-100 bg-gray-50/60">
        <div class="max-w-7xl mx-auto px-5 py-2 flex items-center justify-between gap-3">
          <button v-if="canShowAnsweredFilter" @click="toggleAnsweredFilter"
            :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors',
              showAnsweredOnly
                ? 'bg-emerald-600 text-white border-emerald-600'
                : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300 hover:text-emerald-700']"
            :aria-pressed="showAnsweredOnly"
            title="Показать только вопросы, на которые ответил отдел качества">
            Получен ответ
            <span v-if="answeredCount" class="opacity-80">{{ answeredCount }}</span>
          </button>
          <span v-else></span>
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Обновить
          </button>
        </div>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ ════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'queue'" class="max-w-7xl mx-auto px-4 py-4">

      <div v-if="queueLoading" class="flex items-center justify-center py-20">
        <div class="text-center">
          <div class="w-10 h-10 border-4 border-sky-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          <p class="text-gray-400 text-sm">{{ hasQualityQuestionsAccess ? 'Загружаем уточнения…' : 'Загружаем вопросы…' }}</p>
        </div>
      </div>

      <div v-else-if="filteredItems.length === 0" class="flex flex-col items-center justify-center py-20">
        <CircleCheckBig class="w-12 h-12 mb-4 text-emerald-500" aria-hidden="true" />
        <p class="text-gray-500 font-medium">{{ emptyQueueTitle }}</p>
        <p class="text-gray-400 text-sm mt-1">{{ emptyQueueHint }}</p>
        <button v-if="showAnsweredOnly" @click="toggleAnsweredFilter"
          class="mt-3 text-sm text-emerald-700 hover:text-emerald-800 font-medium">
          Показать все вопросы
        </button>
      </div>

      <div v-else class="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p class="text-sm text-gray-500">
          Показано {{ pagedItems.length }} из {{ filteredItems.length }}
        </p>
        <div class="flex items-center gap-2">
          <button @click="toggleSort"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 hover:border-gray-300 hover:text-gray-800 transition-all">
            <span>{{ sortDesc ? 'Сначала старые' : 'Сначала новые' }}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path v-if="sortDesc" d="M12 5v14M19 12l-7 7-7-7"/>
              <path v-else d="M12 19V5M5 12l7-7 7 7"/>
            </svg>
          </button>
          <select v-model.number="perPage" @change="currentPage = 1"
            class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-gray-600 focus:outline-none focus:ring-1 focus:ring-sky-400">
            <option v-for="n in [10,25,50,100]" :key="n" :value="n">{{ n }} на стр.</option>
          </select>
        </div>
      </div>

      <!-- Карточки -->
      <div v-if="pagedItems.length > 0" class="space-y-2.5">
        <div v-for="item in pagedItems" :key="item.mp + item.item_type + item.id"
          class="bg-white rounded-xl border border-gray-200 hover:border-gray-300 transition-colors overflow-hidden">

          <div class="flex gap-3 p-4">
            <!-- Превью товара -->
            <div class="shrink-0">
              <a v-if="item.product_photo && productCardUrl(item)"
                :href="productCardUrl(item)"
                target="_blank"
                rel="noopener noreferrer"
                class="block w-[72px] h-24 rounded-lg overflow-hidden bg-gray-100 border border-gray-200 hover:border-sky-300 transition-colors"
                :aria-label="item.sku ? `Открыть карточку товара ${item.sku}` : 'Открыть карточку товара'">
                <img :src="item.product_photo" :alt="item.sku ? `Товар ${item.sku}` : 'Товар'" class="w-full h-full object-cover" />
              </a>
              <div v-else-if="item.product_photo"
                class="w-[72px] h-24 rounded-lg overflow-hidden bg-gray-100 border border-gray-200">
                <img :src="item.product_photo" :alt="item.sku ? `Товар ${item.sku}` : 'Товар'" class="w-full h-full object-cover" />
              </div>
              <div v-else class="w-[72px] h-24 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-300">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
            </div>

            <!-- Основной блок -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-2 mb-1.5">
                <div class="flex items-center gap-2 min-w-0">
                  <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp]]">{{ item.mp.toUpperCase() }}</span>
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium bg-sky-50 text-sky-600">Вопрос</span>
                  <span v-if="item.contact" class="text-xs text-gray-400 truncate">· {{ item.contact }}</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <button v-if="productCardRequestFor(item)" type="button" @click="toggleProductCard(item)"
                    :aria-expanded="!!item._productCardOpen"
                    :class="[
                      'inline-flex h-8 w-8 items-center justify-center rounded-lg border bg-white transition-colors',
                      item._productCardOpen
                        ? 'border-amber-300 bg-amber-50 text-amber-700'
                        : 'border-gray-200 text-gray-500 hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700'
                    ]"
                    title="Карточка товара">
                    <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5A2.5 2.5 0 0 0 17.5 16H4z" />
                      <path d="M6.5 3A2.5 2.5 0 0 0 4 5.5V19a2 2 0 0 0 2 2h11.5A2.5 2.5 0 0 0 20 18.5V16" />
                      <path d="M8 7h8M8 10h8M8 13h5" />
                    </svg>
                  </button>
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(item.age_sec)]">{{ fmtAge(item.age_sec) }}</span>
                  <button @click="dismissItem(item)" title="В архив без ответа"
                    aria-label="Перенести вопрос в архив без ответа"
                    class="text-gray-300 hover:text-red-400 transition-colors">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                  </button>
                </div>
              </div>

              <p v-if="item.sku" class="text-xs mb-1">
                <a v-if="productCardUrl(item)"
                  :href="productCardUrl(item)"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-gray-400 hover:text-sky-600 transition-colors underline decoration-transparent hover:decoration-sky-300 underline-offset-2">
                  Арт. {{ item.sku }}
                </a>
                <span v-else class="text-gray-400">Арт. {{ item.sku }}</span>
              </p>
              <p class="text-sm text-gray-700 leading-relaxed">{{ item.body }}</p>
              <div class="mt-2">
                <button type="button" @click="loadSimilar(item)"
                  class="text-xs text-sky-700 hover:text-sky-800 font-medium">
                  {{ item._similarLoading ? 'Ищем похожие…' : 'Похожие вопросы и ответы' }}
                </button>
                <div v-if="item._similarGroups?.length" class="mt-1.5 space-y-1">
                  <div v-for="g in visibleSimilarGroups(item)" :key="g.category"
                    class="flex items-start gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5">
                    <button type="button"
                      :title="similarCurrent(g).text || ''"
                      @click="item._reply = similarCurrent(g).answer || item._reply"
                      class="min-w-0 flex-1 text-left">
                      <span class="text-[11px] font-semibold text-gray-700">{{ g.category }}</span>
                      <p class="text-xs text-gray-600 line-clamp-2">{{ similarCurrent(g).answer || similarCurrent(g).text }}</p>
                    </button>
                    <button v-if="g.items.length > 1" type="button"
                      @click="cycleSimilar(g)"
                      class="shrink-0 text-[11px] font-medium text-sky-700 hover:text-sky-800">
                      Другой ответ
                    </button>
                  </div>
                  <button v-if="hiddenSimilarCount(item) > 0" type="button"
                    @click="item._similarExpanded = !item._similarExpanded"
                    class="text-[11px] font-medium text-sky-700 hover:text-sky-800">
                    {{ item._similarExpanded ? 'Свернуть' : `Ещё ${hiddenSimilarCount(item)}` }}
                  </button>
                </div>
                <p v-else-if="item._similarLoaded && !item._similarLoading" class="mt-1 text-[11px] text-gray-400">
                  Похожих с готовым ответом не нашлось
                </p>
              </div>
              <div v-if="productCardRequestFor(item) && item._productCardOpen"
                class="mt-2 rounded-xl border border-gray-200 bg-white px-3 py-2.5">
                <div class="flex items-center justify-between gap-3 border-b border-gray-100 pb-2">
                  <div>
                    <p class="text-xs font-semibold text-slate-900">Карточка товара</p>
                    <p class="mt-0.5 text-[11px] text-slate-500">
                      <span v-if="productCardRequestFor(item).article">арт. {{ productCardRequestFor(item).article }}</span>
                      <span v-else-if="productCardRequestFor(item).nmId">WB nm_id {{ productCardRequestFor(item).nmId }}</span>
                    </p>
                  </div>
                  <button type="button" @click="toggleProductCard(item)"
                    class="inline-flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
                    title="Свернуть карточку">
                    <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                      <path d="m6 15 6-6 6 6" />
                    </svg>
                  </button>
                </div>
                <div class="pt-2 text-xs text-slate-700">
                  <p v-if="item._productCardLoading" class="text-gray-400">Загружаем карточку товара…</p>
                  <template v-else>
                    <p v-if="productFastenersLine(item._productCard)" class="mt-1">
                      Винты: <span class="font-medium">{{ productFastenersLine(item._productCard) }}</span>
                    </p>
                    <template v-if="item._productCard?.status === 'ok'">
                      <p v-if="productDimensionsLine(item._productCard.product.dimensions)" class="mt-1">
                        Габариты упаковки: <span class="font-medium">{{ productDimensionsLine(item._productCard.product.dimensions) }} см</span>
                      </p>
                      <p v-if="item._productCard.product.dimensions?.weight != null" class="mt-1">
                        Вес: <span class="font-medium">{{ item._productCard.product.dimensions.weight }} кг</span>
                      </p>
                      <dl v-if="item._productCard.product.params?.length" class="mt-2 grid gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(140px,200px)_1fr]">
                        <template v-for="param in item._productCard.product.params" :key="`${param.name}:${param.value}`">
                          <dt class="text-slate-500">{{ param.name }}</dt>
                          <dd class="font-medium break-words">{{ param.value }}</dd>
                        </template>
                      </dl>
                      <p v-if="item._productCard.cached" class="mt-2 text-[10px] text-slate-400">Загружено из локальной базы</p>
                    </template>
                    <p v-else-if="!productFastenersLine(item._productCard) && item._productCard?.status === 'not_found'" class="text-slate-500">
                      Для этого товара карточка пока не найдена.
                    </p>
                    <p v-else-if="!productFastenersLine(item._productCard)" class="text-slate-500">Карточка товара сейчас недоступна. Попробуйте позже.</p>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- Общая очередь QA: только предложение текста, без отправки в МП -->
          <div v-if="hasQualityQuestionsAccess" class="border-t border-violet-200 bg-violet-50/60 px-4 py-3 space-y-2">
            <p class="text-xs font-medium text-violet-800">
              Уточнение от {{ item.clarification_requested_by }}
            </p>
            <p v-if="item.clarification_note" class="text-xs text-gray-600 whitespace-pre-wrap">
              {{ item.clarification_note }}
            </p>
            <textarea
              v-model="item._qaReply"
              placeholder="Предложите ответ для менеджера КС…"
              :rows="replyRows(item._qaReply)"
              class="w-full min-h-[6.5rem] border border-violet-200 rounded-lg px-3 py-2.5 text-sm leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent bg-white"
            ></textarea>
            <div class="flex items-center justify-between gap-3">
              <p class="text-[11px] text-violet-800 leading-snug">Текст вернётся менеджеру КС. На маркетплейс он не отправляется.</p>
              <button @click="sendQaAnswer(item)" :disabled="!item._qaReply?.trim() || item._qaSaving"
                :class="['shrink-0 min-h-10 px-4 py-2 rounded-lg text-sm font-medium transition-all',
                  item._qaReply?.trim() && !item._qaSaving
                    ? 'bg-violet-600 hover:bg-violet-700 text-white'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                {{ item._qaSaving ? 'Сохраняем…' : 'Передать менеджеру' }}
              </button>
            </div>
          </div>

          <!-- Менеджер видит своё уточнение, администратор — все уточнения -->
          <div v-else-if="canRequestClarification && item.clarification && !item._clarificationConsumed"
            :class="['border-t px-4 py-3 space-y-2',
              item.clarification.status === 'answered' ? 'border-emerald-200 bg-emerald-50/70' : 'border-violet-200 bg-violet-50/60']">
            <p :class="['text-xs font-medium', item.clarification.status === 'answered' ? 'text-emerald-800' : 'text-violet-800']">
              {{ item.clarification.status === 'answered' ? 'Качество подготовило ответ' : 'Вопрос передан в отдел качества' }}
            </p>
            <p v-if="permissionsStore.isAdmin && item.clarification.requested_by" class="text-[11px] text-gray-500">
              Запросил: {{ item.clarification.requested_by }}
            </p>
            <p v-if="item.clarification.note" class="text-xs text-gray-600 whitespace-pre-wrap">{{ item.clarification.note }}</p>
            <template v-if="item.clarification.status === 'answered'">
              <p class="text-sm text-gray-700 whitespace-pre-wrap bg-white border border-emerald-200 rounded-lg px-3 py-2">{{ item.clarification.proposed_text }}</p>
              <button @click="applyClarification(item)"
                class="min-h-10 px-3 py-1.5 bg-white hover:bg-emerald-50 text-emerald-700 border border-emerald-200 text-sm rounded-lg transition-colors font-medium">
                Вставить в ответ
              </button>
            </template>
            <p v-else class="text-[11px] text-violet-800">Ответ будет доступен здесь; покупателю ничего не отправляется.</p>
          </div>
          <div v-else-if="canRequestClarification && item._clarificationOpen" class="border-t border-violet-200 bg-violet-50/60 px-4 py-3 space-y-2">
            <textarea v-model="item._clarificationNote" maxlength="2000"
              placeholder="Что нужно уточнить у качества? (необязательно)"
              rows="2"
              class="w-full border border-violet-200 rounded-lg px-3 py-2 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-violet-400 bg-white"></textarea>
            <div class="flex justify-end gap-2">
              <button @click="item._clarificationOpen = false" class="px-3 py-1.5 text-sm text-gray-600">Отмена</button>
              <button @click="requestClarification(item)" :disabled="item._clarificationSaving"
                class="min-h-10 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 text-white text-sm rounded-lg font-medium disabled:opacity-50">
                {{ item._clarificationSaving ? 'Отправляем…' : 'Передать качеству' }}
              </button>
            </div>
          </div>

          <!-- Модерация черновика -->
          <div v-else-if="item.pending_mod_id" class="border-t border-amber-200 bg-amber-50/70 px-4 py-3 space-y-2">
            <p class="text-xs font-medium text-amber-700">Черновик автоответа ожидает подтверждения</p>
            <p class="text-sm text-gray-700 whitespace-pre-wrap bg-white border border-amber-200 rounded-lg px-3 py-2">{{ item.pending_draft }}</p>
            <div class="flex flex-wrap gap-2">
              <button v-if="isWbQuestion(item)" @click="confirmMod(item, 'private')"
                title="Ответ увидит только автор вопроса"
                :aria-describedby="`question-moderation-publish-help-${item.mp}-${item.id}`"
                class="min-h-11 px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-700 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Подтвердить и ответить автору
              </button>
              <button @click="confirmMod(item, 'public')" :title="publicAnswerHelp(item)"
                :aria-describedby="`question-moderation-publish-help-${item.mp}-${item.id}`"
                class="min-h-11 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors font-medium">
                {{ moderationPublishLabel(item) }}
              </button>
              <button @click="rejectMod(item)"
                class="px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-600 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Отклонить
              </button>
            </div>
            <p :id="`question-moderation-publish-help-${item.mp}-${item.id}`"
              class="text-[11px] text-amber-800 leading-snug">
              {{ questionReplyHelp(item) }}
            </p>
          </div>

          <!-- Форма ответа -->
          <div v-else class="border-t border-gray-100 px-4 py-3 bg-gray-50/40">
            <!-- Автоподбор как в чатах -->
            <div v-if="item._suggest?.reply_text"
              class="mb-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
              <div class="flex items-start justify-between gap-2">
                <p class="font-medium">
                  Подобран шаблон
                  <span v-if="item._suggest.theme_name" class="font-normal opacity-80">
                    · {{ item._suggest.theme_name }}
                  </span>
                  <span v-if="item._suggest.confidence != null" class="font-normal opacity-70">
                    · {{ Math.round((item._suggest.confidence || 0) * 100) }}%
                  </span>
                  <span v-if="item._suggest.need_human" class="ml-1 text-rose-600">· лучше проверить</span>
                </p>
                <button type="button" @click="generateSuggest(item)" :disabled="item._suggestGenerating"
                  class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md bg-white border border-sky-200 text-sky-700 hover:bg-sky-100 disabled:opacity-50">
                  {{ item._suggestGenerating ? '…' : 'Сгенерировать' }}
                </button>
              </div>
            </div>
            <div v-else-if="item._suggestLoading" class="mb-2 text-[10px] text-gray-400">Подбираем шаблон…</div>
            <div v-else-if="item._suggestMiss"
              class="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 flex items-center justify-between gap-2">
              <span>{{ item._suggestMiss.reason || 'Нет подходящего шаблона' }}</span>
              <button v-if="item._suggestMiss.can_generate !== false"
                type="button" @click="generateSuggest(item)" :disabled="item._suggestGenerating"
                class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md bg-white border border-amber-200 text-amber-800 hover:bg-amber-100 disabled:opacity-50">
                {{ item._suggestGenerating ? '…' : 'Сгенерировать ответ' }}
              </button>
            </div>
            <div v-else class="mb-2 flex justify-end">
              <button type="button" @click="generateSuggest(item)"
                :disabled="item._suggestGenerating || item._suggestLoading || !(item.body || '').trim()"
                class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md bg-white border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50">
                {{ item._suggestGenerating ? '…' : 'Сгенерировать ответ' }}
              </button>
            </div>

            <div v-if="chipGroupsFor(item).length" class="relative mb-2">
              <div class="flex min-w-0 flex-wrap items-center gap-1.5">
                <button v-for="group in compactChipGroupsFor(item)" :key="group.theme_key"
                  type="button"
                  :title="canEditQuestionTemplates ? 'Двойной щелчок — править шаблон' : ''"
                  @click="onQuestionThemeChip(item, group)"
                  @dblclick.stop.prevent="openQuestionTemplateEditor(group.theme_key, group.templates?.[0])"
                  :class="[
                    'px-2.5 py-1 rounded-full text-xs font-medium transition-colors border',
                    isQuestionThemeActive(item, group)
                      ? 'bg-sky-600 text-white border-sky-600'
                      : 'bg-white hover:bg-sky-50 text-gray-600 hover:text-sky-700 border-gray-200 hover:border-sky-200'
                  ]">
                  {{ group.theme_name }}
                  <span v-if="group.templates.length > 1" class="opacity-70">· {{ group.templates.length }}</span>
                </button>
                <button v-if="chipGroupsFor(item).length > 3" type="button"
                  @click="toggleQuestionTplPanel(item)"
                  class="px-2.5 py-1 rounded-full text-xs font-medium border border-gray-200 bg-white text-gray-500 hover:bg-gray-50">
                  {{ item._tplOpen ? 'Свернуть' : `Темы · ${chipGroupsFor(item).length}` }}
                </button>
              </div>
              <div v-if="item._tplOpen"
                class="mt-2 rounded-xl border border-gray-200 bg-white p-2.5 shadow-sm">
                <div class="mb-2 flex items-center justify-between gap-2">
                  <p class="text-xs font-semibold text-gray-700">
                    <template v-if="item._tplTheme">
                      {{ focusedChipGroup(item)?.theme_name || 'Тема' }}
                    </template>
                    <template v-else>Темы · {{ chipGroupsFor(item).length }}</template>
                  </p>
                  <button v-if="item._tplTheme" type="button" @click="item._tplTheme = ''"
                    class="text-[11px] font-medium text-gray-500 hover:text-gray-700">
                    ← Все темы
                  </button>
                </div>
                <div v-if="!item._tplTheme" class="flex flex-wrap gap-1.5">
                  <button v-for="group in chipGroupsFor(item)" :key="group.theme_key"
                    type="button"
                    :title="canEditQuestionTemplates ? 'Двойной щелчок — править шаблон' : ''"
                    @click="onQuestionThemeChip(item, group)"
                    @dblclick.stop.prevent="openQuestionTemplateEditor(group.theme_key, group.templates?.[0])"
                    :class="[
                      'px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors',
                      isQuestionThemeActive(item, group)
                        ? 'bg-sky-600 text-white border-sky-600'
                        : 'bg-white text-gray-700 border-gray-200 hover:border-sky-200 hover:bg-sky-50'
                    ]">
                    {{ group.theme_name }}
                    <span class="opacity-60">· {{ group.templates.length }}</span>
                  </button>
                </div>
                <div v-else class="flex flex-wrap gap-1.5">
                  <button v-for="t in (focusedChipGroup(item)?.templates || [])" :key="t.id"
                    type="button"
                    :title="(templatePreviewText(t) || templateVariantLabel(t)) + (canEditQuestionTemplates ? ' · двойной щелчок — править' : '')"
                    @click="applyChip(item, t)"
                    @dblclick.stop.prevent="openQuestionTemplateEditor(t.theme_key, t)"
                    :class="[
                      'max-w-full px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors text-left',
                      isChipActive(item, t)
                        ? 'bg-sky-600 text-white border-sky-600'
                        : 'bg-white text-gray-700 border-gray-200 hover:border-sky-200 hover:bg-sky-50'
                    ]">
                    <span class="block font-semibold">{{ templateVariantLabel(t) }}</span>
                    <span v-if="templatePreviewText(t)"
                      class="mt-0.5 block text-[11px] font-normal leading-snug line-clamp-2 opacity-80">
                      {{ templatePreviewText(t) }}
                    </span>
                  </button>
                </div>
              </div>
            </div>
            <textarea
              v-model="item._reply"
              placeholder="Написать ответ…"
              :maxlength="item.mp === 'ozon' ? 3000 : undefined"
              :rows="replyRows(item._reply)"
              class="w-full min-h-[6.5rem] border border-gray-200 rounded-lg px-3 py-2.5 text-sm leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent"
            ></textarea>
            <div v-if="canSaveQuestionAsTemplate && (item._reply || '').trim()"
              class="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-600">
              <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
                <input type="checkbox" v-model="item._saveAsTemplate"
                  class="w-3.5 h-3.5 rounded text-sky-600" />
                Сделать шаблоном
              </label>
              <select v-if="item._saveAsTemplate" v-model="item._saveAsThemeKey"
                class="border border-gray-200 rounded-lg px-2 py-1 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-sky-400 max-w-[16rem]">
                <option value="">Группа ответов…</option>
                <option value="__unassigned__">Без темы</option>
                <option v-for="th in saveAsTemplateThemes" :key="th.theme_key" :value="th.theme_key">
                  {{ th.name || th.theme_key }}
                </option>
              </select>
            </div>
            <div class="mt-2 flex flex-col sm:flex-row sm:items-center gap-2">
              <button v-if="canRequestClarification && item.clarification?.status !== 'pending'" @click="item._clarificationOpen = true"
                title="Необязательное действие: покупателю ничего не отправляется"
                class="self-start min-h-11 px-4 py-2 rounded-lg text-sm font-medium transition-all border border-violet-200 bg-white hover:bg-violet-50 text-violet-700">
                Уточнить!
              </button>
              <div class="flex flex-wrap gap-2 sm:ml-auto sm:justify-end">
                <button v-if="isWbQuestion(item)" @click="sendReply(item, 'private')"
                  :disabled="!canSendQuestionReply(item)"
                  title="Ответ увидит только автор вопроса"
                  :aria-describedby="`question-publish-help-${item.mp}-${item.id}`"
                  :class="['min-h-11 px-4 py-2 rounded-lg text-sm font-medium transition-all border',
                    canSendQuestionReply(item)
                      ? 'bg-white hover:bg-gray-50 text-gray-700 border-gray-300'
                      : 'bg-gray-100 text-gray-400 border-gray-100 cursor-not-allowed']">
                  Ответить автору
                </button>
                <button @click="sendReply(item, 'public')"
                  :disabled="!canSendQuestionReply(item)"
                  :title="publicAnswerHelp(item)"
                  :aria-describedby="`question-publish-help-${item.mp}-${item.id}`"
                  :class="['min-h-11 px-4 py-2 rounded-lg text-sm font-medium transition-all',
                    canSendQuestionReply(item)
                      ? 'bg-sky-600 hover:bg-sky-700 text-white'
                      : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                  {{ publicAnswerLabel(item) }}
                </button>
              </div>
            </div>
            <p :id="`question-publish-help-${item.mp}-${item.id}`"
              class="mt-2 text-[11px] text-gray-600 leading-snug">
              {{ questionReplyHelp(item) }}
            </p>
          </div>
        </div>
      </div>

      <!-- Пагинация -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-2 mt-6">
        <button @click="currentPage = Math.max(1, currentPage - 1)"
          :disabled="currentPage === 1"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          ← Назад
        </button>
        <div class="flex items-center gap-1.5">
          <input type="number" :min="1" :max="totalPages"
            :value="currentPage"
            @change="e => currentPage = Math.min(totalPages, Math.max(1, parseInt(e.target.value) || 1))"
            @keydown.enter="e => e.target.blur()"
            class="w-14 text-center border border-gray-200 rounded-lg px-1 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
          <span class="text-sm text-gray-400">/ {{ totalPages }}</span>
        </div>
        <button @click="currentPage = Math.min(totalPages, currentPage + 1)"
          :disabled="currentPage === totalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          Вперёд →
        </button>
      </div>
    </div>

    <!-- ══ ШАБЛОНЫ (плейбук вопросов) ═══════════════════════════════════════ -->
    <div v-if="activeSection === 'templates'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex flex-wrap justify-between items-start gap-3 mb-4">
        <div>
          <h2 class="font-semibold text-gray-800">Плейбук вопросов</h2>
          <p class="text-xs text-gray-400 mt-0.5 max-w-xl">
            Копия из чатов, урезано до тем, которые реально использовались в ответах за последний месяц.
            Правки здесь влияют только на вопросы.
          </p>
        </div>
        <div class="flex flex-wrap gap-2" v-if="canEditQuestionTemplates">
          <button @click="syncPlaybookFromChats" :disabled="pbSyncing"
            class="px-3 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
            {{ pbSyncing ? '…' : 'Подтянуть недостающие из чатов' }}
          </button>
          <button @click="openVariantForm()" class="px-3 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700">
            + Вариант
          </button>
        </div>
        <p v-else class="text-[11px] text-gray-400 self-center">Только просмотр</p>
      </div>

      <div v-if="tplFormOpen && canEditQuestionTemplates" class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 mb-4">
        <h3 class="font-semibold text-gray-800 mb-4">{{ editVariant.id ? 'Редактировать вариант' : 'Новый вариант' }}</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Тема</label>
            <select v-model="editVariant.theme_key" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option v-for="th in pbThemes" :key="th.theme_key" :value="th.theme_key">{{ th.name }}</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Площадка</label>
            <select v-model="editVariant.mp" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="">Все / WB</option>
              <option value="wb">WB</option>
              <option value="ym">ЯМ</option>
              <option value="ozon">Ozon</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Ветка (ключ)</label>
            <input v-model="editVariant.branch_key" placeholder="пусто = основная"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Подпись ветки</label>
            <input v-model="editVariant.branch_label"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400" />
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Текст ответа</label>
            <textarea v-model="editVariant.body" rows="6"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-sky-400"></textarea>
          </div>
        </div>
        <div class="flex gap-2 mt-4">
          <button @click="saveVariant" class="px-4 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700">
            {{ editVariant.id ? 'Сохранить' : 'Создать' }}
          </button>
          <button @click="tplFormOpen = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
            Отмена
          </button>
        </div>
      </div>

      <div v-if="pbLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-sky-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="pbThemes.length === 0" class="text-center py-16 text-gray-400">
        Плейбук пуст.
        <button @click="syncPlaybookFromChats" class="ml-1 text-sky-600 hover:underline">Скопировать из чатов</button>
      </div>
      <div v-else class="space-y-3">
        <div v-for="th in pbThemes" :key="th.theme_key" :id="'q-theme-' + th.theme_key"
          class="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <button type="button" @click="toggleTheme(th.theme_key)"
            class="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-gray-50">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium text-gray-800 text-sm">{{ th.name }}</span>
                <span class="text-[10px] uppercase tracking-wide text-gray-400">{{ th.theme_key }}</span>
                <span class="text-xs text-gray-400">{{ (th.variants || []).length }} вар.</span>
              </div>
            </div>
            <span class="text-gray-400 text-sm">{{ expandedThemes[th.theme_key] ? '▾' : '▸' }}</span>
          </button>
          <div v-if="expandedThemes[th.theme_key]" class="border-t border-gray-100 divide-y divide-gray-50">
            <div v-for="v in th.variants" :key="v.id" class="px-4 py-3">
              <div class="flex items-start justify-between gap-3 mb-1.5">
                <div class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span v-if="v.branch_label || v.branch_key" class="px-1.5 py-0.5 rounded bg-gray-100">
                    {{ v.branch_label || v.branch_key || 'основной' }}
                  </span>
                  <span v-else class="px-1.5 py-0.5 rounded bg-gray-100">основной</span>
                  <span v-if="v.mp" class="uppercase font-medium">{{ v.mp }}</span>
                  <span v-if="v.review_status === 'pending'"
                    class="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-200">
                    на проверке
                  </span>
                </div>
                <div v-if="canEditQuestionTemplates" class="flex gap-2 shrink-0">
                  <button @click="openVariantForm(v)" class="text-xs text-sky-600 hover:underline">Ред.</button>
                  <button @click="deleteVariant(v.id)" class="text-xs text-red-500 hover:underline">Удалить</button>
                </div>
              </div>
              <p class="text-sm text-gray-600 whitespace-pre-wrap leading-relaxed">{{ v.body }}</p>
            </div>
            <div v-if="!(th.variants || []).length" class="px-4 py-3 text-sm text-gray-400">Нет вариантов</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ НОВЫЕ ШАБЛОНЫ (проверка админом) ══════════════════════════════════ -->
    <div v-if="activeSection === 'review' && canReviewQuestionTemplates" class="max-w-4xl mx-auto px-4 py-4">
      <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 class="font-semibold text-gray-800">Новые шаблоны вопросов</h2>
            <p class="text-xs text-gray-500 mt-1 leading-relaxed max-w-2xl">
              Добавлены менеджером КС из ответа на вопрос. Уже могут подставляться — проверьте текст и нажмите «Проверено» или удалите.
            </p>
          </div>
          <button type="button" @click="loadPendingQuestionTemplates"
            class="text-xs px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">
            Обновить
          </button>
        </div>
        <div v-if="pendingLoading" class="px-5 py-10 text-center text-xs text-gray-400">Загрузка…</div>
        <div v-else-if="!pendingTemplates.length" class="px-5 py-10 text-center text-sm text-gray-400">
          Нет шаблонов на проверке
        </div>
        <div v-else class="divide-y divide-gray-100">
          <div v-for="p in pendingTemplates" :key="p.id" class="px-5 py-4 space-y-2">
            <div class="flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
              <span class="font-medium text-gray-700">
                {{ p.needs_theme || p.theme_key === '__unassigned__' ? 'Без темы' : (p.theme_name || p.theme_key) }}
              </span>
              <span v-if="p.needs_theme || p.theme_key === '__unassigned__'"
                class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">
                нужна тема
              </span>
              <span class="uppercase tracking-wide px-1.5 py-0.5 rounded border bg-gray-50">
                {{ p.mp || 'все' }}
              </span>
              <span v-if="p.created_by">{{ p.created_by }}</span>
              <span v-if="p.created_at">{{ fmtPendingAt(p.created_at) }}</span>
              <span v-if="p.source_question_id" class="text-gray-400">вопрос {{ p.source_question_id }}</span>
            </div>
            <div class="flex flex-wrap items-end gap-2">
              <label class="text-[11px] text-gray-500">
                Тема
                <select v-model="p._assignMode"
                  class="mt-0.5 block border border-gray-200 rounded-lg px-2 py-1.5 text-xs bg-white max-w-[16rem]">
                  <option value="keep" v-if="p.theme_key && p.theme_key !== '__unassigned__'">
                    Оставить: {{ p.theme_name || p.theme_key }}
                  </option>
                  <option value="existing">Выбрать из списка…</option>
                  <option value="new">Создать новую тему…</option>
                </select>
              </label>
              <select v-if="p._assignMode === 'existing'" v-model="p._assignThemeKey"
                class="border border-gray-200 rounded-lg px-2 py-1.5 text-xs bg-white max-w-[16rem]">
                <option value="">Тема…</option>
                <option v-for="th in saveAsTemplateThemes" :key="th.theme_key" :value="th.theme_key">
                  {{ th.name || th.theme_key }}
                </option>
              </select>
              <template v-if="p._assignMode === 'new'">
                <input v-model="p._newThemeName" type="text" placeholder="Название новой темы"
                  class="border border-gray-200 rounded-lg px-2 py-1.5 text-xs min-w-[12rem] focus:outline-none focus:ring-2 focus:ring-sky-400" />
                <input v-model="p._newThemeWhen" type="text" placeholder="Когда использовать (необяз.)"
                  class="border border-gray-200 rounded-lg px-2 py-1.5 text-xs min-w-[12rem] focus:outline-none focus:ring-2 focus:ring-sky-400" />
              </template>
            </div>
            <textarea v-model="p.body" rows="4"
              class="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs text-gray-700 resize-y focus:outline-none focus:ring-2 focus:ring-sky-400"></textarea>
            <div class="flex flex-wrap gap-2">
              <button type="button" @click="approvePendingQuestion(p)" :disabled="p._busy"
                class="text-xs font-medium px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">
                Проверено
              </button>
              <button type="button" @click="deletePendingQuestion(p)" :disabled="p._busy"
                class="text-xs font-medium px-3 py-1.5 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50 disabled:opacity-50">
                Удалить
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ АВТООТВЕТЫ ═══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'settings' && !isCsManager" class="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <div class="bg-white border border-gray-200 rounded-xl p-5">
        <h2 class="font-semibold text-gray-800 mb-1">Автоответы на вопросы</h2>
        <p class="text-sm text-gray-500 leading-relaxed mb-4">
          В очереди шаблон подбирается классификатором по плейбуку вопросов (вкладка «Шаблоны»).
          Автоотправка без проверки пока готовится — сейчас тексты подставляются в поле ответа.
        </p>
        <ul class="text-sm text-gray-600 space-y-2 list-disc pl-5">
          <li>WB: можно опубликовать ответ на карточке или отправить его только автору вопроса.</li>
          <li>ЯМ: ответ публикуется на карточке товара.</li>
          <li>Ozon: ответ публикуется в разделе вопросов на карточке товара.</li>
          <li>Плейбук вопросов — отдельная копия чатового; можно править независимо.</li>
        </ul>
        <div class="mt-4 flex flex-wrap gap-3 text-xs">
          <span class="px-2.5 py-1 rounded-full bg-sky-50 text-sky-700 border border-sky-200">
            Тем: {{ pbThemes.length }}
          </span>
          <span class="px-2.5 py-1 rounded-full bg-slate-50 text-slate-600 border border-slate-200">
            Вариантов: {{ pbVariantCount }}
          </span>
        </div>
      </div>
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
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import { usePermissionsStore } from '../stores/permissions'
import { useToast } from '../composables/useToast'
import { useCxQueueActions } from '../composables/useCxQueueActions'
import { CX_BASE as BASE, MP_BADGE_CLASSES as mpBadgeCls, fmtAge, ageCls } from '../utils/cx'
import { groupTemplatesByTheme, templateVariantLabel, templatePreviewText } from '../utils/templateGroups'
import { CircleCheckBig, ClipboardPenLine } from 'lucide-vue-next'

const platformStore = usePlatformStore()
const permissionsStore = usePermissionsStore()
const route = useRoute()
const currentMp = computed(() => platformStore.platform || 'all')
const canEditQuestionTemplates = computed(() =>
  ['admin', 'moderator', 'cs_manager'].includes(permissionsStore.role)
)
const canSaveQuestionAsTemplate = computed(() => canEditQuestionTemplates.value)
const canReviewQuestionTemplates = computed(() => permissionsStore.isAdmin)
const isCsManager = computed(() => permissionsStore.role === 'cs_manager')
const canRequestClarification = computed(() =>
  permissionsStore.isAdmin || permissionsStore.can('cx_responses')
)
// Один и тот же компонент обслуживает два разных рабочих стола. Наличие
// обоих прав не должно подменять обычные вопросы очередью качества.
const hasQualityQuestionsAccess = computed(() =>
  route.path === '/questions/quality' &&
  (permissionsStore.isAdmin || permissionsStore.can('quality_questions'))
)
const questionsSubtitle = computed(() => hasQualityQuestionsAccess.value
  ? 'Общая очередь уточнений от менеджеров КС'
  : 'WB · ЯМ · Ozon — ответ публикуется на карточке товара'
)

const { toast, showToast } = useToast()

// ── Навигация ──────────────────────────────────────────────────────────────
const sections = [
  { key: 'queue',     label: 'Очередь'  },
  { key: 'templates', label: 'Шаблоны'  },
  { key: 'review',    label: 'Новые шаблоны', adminOnly: true },
  { key: 'settings',  label: 'Автоответы' },
]
const visibleSections = computed(() => {
  if (hasQualityQuestionsAccess.value) {
    return sections.filter(s => s.key === 'queue')
  }
  return sections.filter(s => {
    if (s.key === 'settings' && isCsManager.value) return false
    if (s.adminOnly && !canReviewQuestionTemplates.value) return false
    return true
  })
})
const activeSection = ref('queue')

function switchSection(key) {
  activeSection.value = key
  if (key === 'review' && canReviewQuestionTemplates.value) loadPendingQuestionTemplates()
}

watch(currentMp, () => {
  if (!hasQualityQuestionsAccess.value) loadPlaybookTemplates()
  if (activeSection.value === 'queue') loadQueue()
})

watch(
  () => (queueItems.value || []).some(i => i._saveAsTemplate),
  (on) => {
    if (on && canSaveQuestionAsTemplate.value && !pbThemes.value.length) {
      loadQuestionPlaybook()
    }
  },
)

// ── Очередь ────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const counts       = ref({})
const perPage      = ref(25)
const currentPage  = ref(1)
const sortDesc     = ref(true)
const showAnsweredOnly = ref(false)

function hasQualityAnswer(item) {
  return item?.clarification?.status === 'answered'
}

const canShowAnsweredFilter = computed(() =>
  canRequestClarification.value && !hasQualityQuestionsAccess.value
)

const answeredCount = computed(() =>
  (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'question' && hasQualityAnswer(i)).length
)

const emptyQueueTitle = computed(() => {
  if (hasQualityQuestionsAccess.value) return 'Уточнений нет'
  if (showAnsweredOnly.value) return 'Нет вопросов с ответом качества'
  return 'Вопросов нет'
})

const emptyQueueHint = computed(() => {
  if (hasQualityQuestionsAccess.value) return 'Все уточнения обработаны'
  if (showAnsweredOnly.value) return 'Качество ещё не ответило — или снимите фильтр, чтобы увидеть все вопросы'
  return 'Все вопросы обработаны'
})

function toggleAnsweredFilter() {
  showAnsweredOnly.value = !showAnsweredOnly.value
  currentPage.value = 1
}

function toggleSort() {
  sortDesc.value = !sortDesc.value
  currentPage.value = 1
}

const filteredItems = computed(() => {
  const mp = currentMp.value || 'all'
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'question')
    .filter(i => mp === 'all' || i.mp === mp)
  if (showAnsweredOnly.value) {
    list = list.filter(hasQualityAnswer)
  }
  const dir = sortDesc.value ? 1 : -1
  return [...list].sort((a, b) => ((a.age_sec || 0) - (b.age_sec || 0)) * dir)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))
const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

let queueRequest = 0
async function loadQueue() {
  const requestId = ++queueRequest
  const mp = currentMp.value || 'all'
  queueLoading.value = true
  currentPage.value  = 1
  try {
    const queueBase = hasQualityQuestionsAccess.value ? '/api/v1/quality-questions' : BASE
    const res = await apiFetch(`${queueBase}/queue?mp=${encodeURIComponent(mp)}`)
    if (requestId !== queueRequest) return
    const data = await res.json()
    if ((currentMp.value || 'all') !== mp) return
    const rawItems = mp === 'all'
      ? (data.items || [])
      : (data.items || []).filter(i => i.mp === mp)
    queueItems.value = rawItems.map(i => ({
      ...i,
      nm_id: i.nm_id ? Number(i.nm_id) : null,
      question_wb_nm_id: i.question_wb_nm_id ? Number(i.question_wb_nm_id) : null,
      question_product_url: String(i.question_product_url || '').trim() || null,
      question_market_sku: String(i.question_market_sku || '').trim() || null,
      _productCardOpen: false,
      _productCardLoading: false,
      _productCard: null,
      _reply: '',
      _saveAsTemplate: false,
      _saveAsThemeKey: '',
      _suggest: null,
      _suggestMiss: null,
      _suggestLoading: false,
      _suggestGenerating: false,
      _clarificationOpen: false,
      _clarificationSaving: false,
      _clarificationNote: '',
      _clarificationConsumed: false,
      _qaReply: '',
      _qaSaving: false,
      _similar: [],
      _similarLoading: false,
      _similarLoaded: false,
    }))
    counts.value = data.counts || {}
    // Автоподбор для первой страницы
    if (!hasQualityQuestionsAccess.value) suggestForPage()
  } catch {
    if (requestId === queueRequest) showToast('Ошибка загрузки очереди', 'error')
  } finally {
    if (requestId !== queueRequest) return
    queueLoading.value = false
  }
}

const BOT = '/api/v1/chat-bot'
const QPB = '/api/v1/question-playbook'
const PB  = '/api/v1/chat-playbook'
const playbookTemplates = ref([])
const pbThemes = ref([])
const pbLoading = ref(false)
const pbSyncing = ref(false)
const tplFormOpen = ref(false)
const editVariant = ref({})
const expandedThemes = ref({})
const replySignature = ref('')
const pendingTemplates = ref([])
const pendingTemplateCount = ref(0)
const pendingLoading = ref(false)

const pbVariantCount = computed(() =>
  pbThemes.value.reduce((n, th) => n + ((th.variants || []).length), 0)
)
const saveAsTemplateThemes = computed(() =>
  (pbThemes.value || []).filter(t => t.active !== false && t.theme_key !== '__unassigned__')
)

function replyRows(text) {
  const lines = String(text || '').split('\n').length
  const approx = Math.ceil(String(text || '').length / 72)
  return Math.min(14, Math.max(4, Math.max(lines, approx) + 1))
}

function isWbQuestion(item) {
  return item.mp === 'wb' && item.item_type === 'question'
}

function wbProductUrl(nmId) {
  const nm = Number(nmId)
  return Number.isFinite(nm) && nm > 0
    ? `https://www.wildberries.ru/catalog/${nm}/detail.aspx`
    : ''
}

function ymProductUrl(marketSku, offerId) {
  const sku = String(marketSku || '').trim()
  if (sku) return `https://market.yandex.ru/card/slug/${encodeURIComponent(sku)}`
  const offer = String(offerId || '').trim()
  // Если номера карточки ещё нет в справочнике — поиск по нашему артикулу.
  return offer
    ? `https://market.yandex.ru/search?text=${encodeURIComponent(offer)}&cvredirect=1`
    : ''
}

function productCardUrl(item) {
  if (!item || item.item_type !== 'question') return ''
  if (item.mp === 'wb') return wbProductUrl(item.nm_id || item.question_wb_nm_id)
  if (item.mp === 'ozon') return String(item.question_product_url || '').trim()
  if (item.mp === 'ym') return ymProductUrl(item.question_market_sku, item.sku)
  return ''
}

function productCardRequestFor(item) {
  if (!item || item.item_type !== 'question') return null
  const mp = String(item.mp || '').toLowerCase()
  const article = String(item.sku || '').trim()
  const nmId = Number(item.nm_id || item.question_wb_nm_id || 0)
  const hasNm = Number.isFinite(nmId) && nmId > 0
  if (mp === 'wb') {
    if (article || hasNm) return { mp, article, nmId: hasNm ? nmId : null }
    return null
  }
  if (['ym', 'ozon'].includes(mp) && article) return { mp, article, nmId: null }
  return null
}

function productDimensionsLine(dimensions) {
  if (!dimensions || dimensions.length == null || dimensions.width == null || dimensions.height == null) return ''
  return `${dimensions.length} × ${dimensions.width} × ${dimensions.height}`
}

function productFastenersLine(card) {
  const items = card?.fasteners || card?.product?.fasteners || []
  return items.map(item => item.label || item.code).filter(Boolean).join('; ')
}

async function toggleProductCard(item) {
  if (!item) return
  item._productCardOpen = !item._productCardOpen
  if (!item._productCardOpen || item._productCard || item._productCardLoading) return
  const req = productCardRequestFor(item)
  if (!req) return
  item._productCardLoading = true
  try {
    const params = new URLSearchParams({ mp: req.mp || 'wb' })
    if (req.article) params.set('article', req.article)
    if (req.nmId) params.set('nm_id', String(req.nmId))
    const r = await apiFetch(`/api/v1/chat-bot/product-card?${params}`)
    item._productCard = await r.json()
  } catch {
    item._productCard = { status: 'unavailable' }
  } finally {
    item._productCardLoading = false
  }
}

function publicAnswerLabel(item) {
  return isWbQuestion(item) ? 'Опубликовать на карточке' : 'Опубликовать ответ'
}

function moderationPublishLabel(item) {
  return `Подтвердить и ${publicAnswerLabel(item).toLowerCase()}`
}

function publicAnswerHelp(item) {
  if (isWbQuestion(item)) {
    return 'Ответ будет виден всем покупателям в разделе «Вопросы» на карточке товара.'
  }
  return item.mp === 'ozon'
    ? 'Ответ публикуется на карточке товара. Ограничение Ozon — от 2 до 3000 символов.'
    : 'Ответ публикуется на карточке товара.'
}

function questionReplyHelp(item) {
  return isWbQuestion(item)
    ? '«Опубликовать» увидят все покупатели на карточке. «Ответить автору» увидит только автор вопроса.'
    : publicAnswerHelp(item)
}

function canSendQuestionReply(item) {
  const text = String(item?._reply || '').trim()
  if (item?.mp === 'ozon') return text.length >= 2 && text.length <= 3000
  return Boolean(text)
}

function normalizeBody(s) {
  return String(s || '').replace(/\s+/g, ' ').trim()
}

function withSignature(body) {
  const text = String(body || '').trim()
  const sig = (replySignature.value || '').trim()
  if (!text || !sig) return text
  if (text.includes(sig) || /команда\s+видовито/i.test(text)) return text
  return `${text}\n\n${sig}`
}

/** Текст для сохранения в плейбук: без приветствия и подписи. */
function bodyForTemplate(raw) {
  let text = String(raw || '').trim()
  if (!text) return ''
  const sig = (replySignature.value || '').trim()
  if (sig && text.includes(sig)) text = text.replace(sig, '').trim()
  text = text.replace(/^(здравствуйте|добрый\s+(день|вечер))[!.,\s]*/i, '').trim()
  text = text.replace(/\n*\s*александр,?\s+команда\s+видовито\s*$/i, '').trim()
  return text
}

async function saveQuestionAsTemplateIfNeeded(item, replyText) {
  if (!canSaveQuestionAsTemplate.value || !item?._saveAsTemplate) return
  const themeKey = item._saveAsThemeKey
  const body = bodyForTemplate(replyText)
  if (!themeKey || !body) {
    if (item._saveAsTemplate && !themeKey) {
      showToast('Выберите группу для шаблона', 'error')
    }
    return
  }
  try {
    await apiFetch(`${QPB}/variants/from-question`, {
      method: 'POST',
      body: JSON.stringify({
        theme_key: themeKey,
        body,
        mp: item.mp || 'wb',
        question_id: String(item.id || ''),
      }),
    })
    item._saveAsTemplate = false
    item._saveAsThemeKey = ''
    showToast(
      themeKey === '__unassigned__'
        ? 'Шаблон без темы — админ назначит группу при проверке'
        : 'Ответ сохранён как шаблон вопросов (на проверке)',
      'ok',
    )
    if (pbThemes.value.length) loadQuestionPlaybook()
    if (canReviewQuestionTemplates.value) loadPendingQuestionTemplates(true)
  } catch {
    showToast('Ответ ушёл, но шаблон не сохранился', 'error')
  }
}

function isChipActive(item, t) {
  const reply = normalizeBody(item._reply)
  if (!reply) return false
  const body = normalizeBody(t.body)
  return reply.includes(body.slice(0, Math.min(60, body.length))) || body.includes(reply.slice(0, 60))
}

function applyChip(item, t) {
  item._reply = withSignature(t.body || '')
  item._chipId = t.id
  item._tplOpen = false
  item._tplTheme = ''
}

function chipsFor(item) {
  return (playbookTemplates.value || []).filter(t =>
    t.mp === 'all' || !t.mp || t.mp === item.mp
  )
}

function chipGroupsFor(item) {
  return groupTemplatesByTheme(chipsFor(item), item._suggest?.theme || '')
}

function compactChipGroupsFor(item) {
  return chipGroupsFor(item).slice(0, 3)
}

function focusedChipGroup(item) {
  return chipGroupsFor(item).find(g => g.theme_key === item._tplTheme) || null
}

function toggleQuestionTplPanel(item) {
  item._tplOpen = !item._tplOpen
  if (!item._tplOpen) item._tplTheme = ''
}

async function openQuestionTemplateEditor(themeKey, variant) {
  if (!canEditQuestionTemplates.value || !themeKey) return
  if (!pbThemes.value.length) await loadQuestionPlaybook()
  switchSection('templates')
  expandedThemes.value = { ...expandedThemes.value, [themeKey]: true }
  const theme = pbThemes.value.find(t => t.theme_key === themeKey)
  const full = variant?.id
    ? (theme?.variants || []).find(v => v.id === variant.id) || variant
    : null
  if (full) openVariantForm(full)
  await nextTick()
  document.getElementById('q-theme-' + themeKey)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onQuestionThemeChip(item, group) {
  if (!group) return
  if ((group.templates || []).length === 1) {
    applyChip(item, group.templates[0])
    return
  }
  item._tplTheme = group.theme_key
  item._tplOpen = true
}

function isQuestionThemeActive(item, group) {
  return (group?.templates || []).some(t => isChipActive(item, t))
}

async function loadSignature() {
  try {
    const r = await apiFetch(PB)
    const d = await r.json()
    const row = (d.settings || []).find(s => s.key === 'signature')
    replySignature.value = (row?.value || '').trim()
  } catch {
    replySignature.value = ''
  }
}

async function loadPlaybookTemplates() {
  try {
    const mp = currentMp.value === 'all' ? 'all' : currentMp.value
    const r = await apiFetch(`${BOT}/question-templates?mp=${mp}`)
    const d = await r.json()
    playbookTemplates.value = d.templates || []
  } catch {
    playbookTemplates.value = []
  }
}

async function loadQuestionPlaybook() {
  pbLoading.value = true
  try {
    const r = await apiFetch(QPB)
    const d = await r.json()
    pbThemes.value = d.themes || []
    if (typeof d.pending_count === 'number') {
      pendingTemplateCount.value = d.pending_count
    }
    if (!Object.keys(expandedThemes.value).length && pbThemes.value[0]) {
      expandedThemes.value = { [pbThemes.value[0].theme_key]: true }
    }
  } catch {
    pbThemes.value = []
    showToast('Не удалось загрузить плейбук вопросов', 'error')
  } finally {
    pbLoading.value = false
  }
}

async function loadPendingQuestionTemplates(silent = false) {
  if (!canReviewQuestionTemplates.value) return
  pendingLoading.value = true
  try {
    const r = await apiFetch(`${QPB}/variants/pending`)
    const d = await r.json()
    pendingTemplates.value = (d.items || []).map(x => {
      const needs = !!(x.needs_theme || x.theme_key === '__unassigned__')
      return {
        ...x,
        _busy: false,
        _assignMode: needs ? 'existing' : 'keep',
        _assignThemeKey: needs ? '' : (x.theme_key || ''),
        _newThemeName: '',
        _newThemeWhen: '',
      }
    })
    pendingTemplateCount.value = d.count ?? pendingTemplates.value.length
  } catch {
    if (!silent) showToast('Не удалось загрузить новые шаблоны', 'error')
  } finally {
    pendingLoading.value = false
  }
}

function fmtPendingAt(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('ru', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    })
  } catch { return String(iso) }
}

function pendingQuestionReviewPayload(p) {
  const body = { status: 'approved', body: p.body }
  if (p._assignMode === 'new') {
    const name = (p._newThemeName || '').trim()
    if (!name) return { error: 'Укажите название новой темы' }
    body.new_theme_name = name
    body.new_theme_when = (p._newThemeWhen || '').trim()
  } else if (p._assignMode === 'existing') {
    const key = (p._assignThemeKey || '').trim()
    if (!key || key === '__unassigned__') return { error: 'Выберите тему' }
    body.theme_key = key
  } else if (p.needs_theme || p.theme_key === '__unassigned__') {
    return { error: 'Выберите или создайте тему' }
  }
  return { body }
}

async function approvePendingQuestion(p) {
  if (!p?.id || p._busy) return
  const prepared = pendingQuestionReviewPayload(p)
  if (prepared.error) {
    showToast(prepared.error, 'error')
    return
  }
  p._busy = true
  try {
    await apiFetch(`${QPB}/variants/${p.id}/review`, {
      method: 'PUT',
      body: JSON.stringify(prepared.body),
    })
    pendingTemplates.value = pendingTemplates.value.filter(x => x.id !== p.id)
    pendingTemplateCount.value = Math.max(0, pendingTemplateCount.value - 1)
    showToast('Шаблон проверен', 'ok')
    await loadQuestionPlaybook()
  } catch {
    showToast('Не удалось сохранить', 'error')
  } finally {
    p._busy = false
  }
}

async function deletePendingQuestion(p) {
  if (!p?.id || p._busy) return
  if (!confirm('Удалить этот шаблон?')) return
  p._busy = true
  try {
    await apiFetch(`${QPB}/variants/${p.id}`, { method: 'DELETE' })
    pendingTemplates.value = pendingTemplates.value.filter(x => x.id !== p.id)
    pendingTemplateCount.value = Math.max(0, pendingTemplateCount.value - 1)
    showToast('Шаблон удалён', 'ok')
    await loadQuestionPlaybook()
  } catch {
    showToast('Не удалось удалить', 'error')
  } finally {
    p._busy = false
  }
}

function toggleTheme(key) {
  expandedThemes.value = {
    ...expandedThemes.value,
    [key]: !expandedThemes.value[key],
  }
}

function openVariantForm(v = null) {
  if (v) {
    editVariant.value = { ...v }
  } else {
    editVariant.value = {
      theme_key: pbThemes.value[0]?.theme_key || 'nekomplekt',
      branch_key: '',
      branch_label: '',
      body: '',
      mp: '',
      active: true,
    }
  }
  tplFormOpen.value = true
}

async function saveVariant() {
  const v = editVariant.value
  if (!v.body?.trim()) {
    showToast('Введите текст шаблона', 'error')
    return
  }
  try {
    if (v.id) {
      await apiFetch(`${QPB}/variants/${v.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          body: v.body,
          branch_label: v.branch_label || '',
          mp: v.mp || '',
          theme_key: v.theme_key,
          active: v.active !== false,
        }),
      })
    } else {
      await apiFetch(`${QPB}/variants`, {
        method: 'POST',
        body: JSON.stringify({
          theme_key: v.theme_key,
          branch_key: v.branch_key || '',
          branch_label: v.branch_label || '',
          body: v.body,
          mp: v.mp || '',
          active: true,
        }),
      })
    }
    tplFormOpen.value = false
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast('Шаблон сохранён', 'ok')
  } catch {
    showToast('Ошибка сохранения', 'error')
  }
}

async function deleteVariant(id) {
  if (!confirm('Удалить вариант?')) return
  try {
    await apiFetch(`${QPB}/variants/${id}`, { method: 'DELETE' })
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast('Удалено', 'ok')
  } catch {
    showToast('Ошибка удаления', 'error')
  }
}

async function syncPlaybookFromChats() {
  pbSyncing.value = true
  try {
    const r = await apiFetch(`${QPB}/sync-from-chats`, { method: 'POST' })
    const d = await r.json()
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast(
      `Добавлено: тем ${d.added_themes || 0}, вариантов ${d.added_variants || 0}`,
      'ok',
    )
  } catch {
    showToast('Не удалось синхронизировать', 'error')
  } finally {
    pbSyncing.value = false
  }
}

async function suggestOne(item, { forceAi = false } = {}) {
  const text = (item.body || '').trim()
  if (!text) return
  if (forceAi) item._suggestGenerating = true
  else item._suggestLoading = true
  try {
    const r = await apiFetch(`${BOT}/suggest-question`, {
      method: 'POST',
      body: JSON.stringify({
        text,
        mp: item.mp || 'wb',
        article: item.sku || '',
        nm_id: item.nm_id ? Number(item.nm_id) : null,
        force_ai: forceAi,
      }),
    })
    const d = await r.json()
    if (d?.reply_text) {
      item._suggest = d
      item._suggestMiss = null
      if (!item._reply?.trim() || forceAi) item._reply = String(d.reply_text).replace(/^\s+/, '')
    } else {
      item._suggest = null
      const reason = (d?.reason || '').trim()
      // Нет шаблона (в т.ч. бывшее «меню 1–5») — без жёлтого баннера, только кнопка генерации
      if (!reason || reason === 'Нет подходящего шаблона') {
        item._suggestMiss = null
      } else {
        item._suggestMiss = {
          reason,
          can_generate: d?.can_generate !== false,
        }
      }
    }
  } catch {
    item._suggestMiss = { reason: 'Ошибка подбора шаблона', can_generate: true }
  } finally {
    item._suggestLoading = false
    item._suggestGenerating = false
  }
}

function suggestForPage() {
  const list = pagedItems.value || []
  list.forEach(item => {
    if (item._suggest || item._suggestMiss || item._suggestLoading) return
    suggestOne(item)
  })
}

async function loadSimilar(item) {
  if (!item || item._similarLoading) return
  item._similarLoading = true
  try {
    const params = new URLSearchParams({
      q: item.body || '',
      mp: item.mp || 'all',
      exclude: String(item.id || ''),
    })
    const r = await apiFetch(`${BASE}/questions/similar?${params}`)
    const d = await r.json()
    const groups = Array.isArray(d.groups) ? d.groups : []
    item._similarGroups = groups.map(g => ({
      category: g.category || 'Без категории',
      items: Array.isArray(g.items) ? g.items : [],
      _i: 0,
    })).filter(g => g.items.length)
    item._similar = item._similarGroups.map(g => g.items[0])
  } catch {
    item._similarGroups = []
    item._similar = []
  } finally {
    item._similarLoading = false
    item._similarLoaded = true
  }
}

function visibleSimilarGroups(item) {
  const groups = item?._similarGroups || []
  if (item?._similarExpanded) return groups
  return groups.slice(0, 3)
}

function hiddenSimilarCount(item) {
  const groups = item?._similarGroups || []
  if (item?._similarExpanded) return 0
  return Math.max(0, groups.length - 3)
}

function similarCurrent(group) {
  const items = group?.items || []
  if (!items.length) return {}
  const i = Number(group._i) || 0
  return items[((i % items.length) + items.length) % items.length]
}

function cycleSimilar(group) {
  const n = (group?.items || []).length
  if (n < 2) return
  group._i = ((Number(group._i) || 0) + 1) % n
}

function loadSimilarForPage() {
  const list = pagedItems.value || []
  list.forEach(item => {
    if (item._similarLoaded || item._similarLoading) return
    loadSimilar(item)
  })
}

function generateSuggest(item) {
  return suggestOne(item, { forceAi: true })
}

watch([currentPage, perPage], () => {
  if (!hasQualityQuestionsAccess.value) suggestForPage()
})

watch(activeSection, (s) => {
  if (!hasQualityQuestionsAccess.value && s === 'templates' && !pbThemes.value.length) loadQuestionPlaybook()
  if (s === 'review' && canReviewQuestionTemplates.value) loadPendingQuestionTemplates()
})

async function requestClarification(item) {
  item._clarificationSaving = true
  try {
    const r = await apiFetch(`${BASE}/questions/${encodeURIComponent(item.mp)}/${encodeURIComponent(String(item.id))}/clarification`, {
      method: 'POST',
      body: JSON.stringify({ note: item._clarificationNote || '' }),
    })
    const d = await r.json()
    item.clarification = {
      id: d.id,
      mp: item.mp,
      question_id: String(item.id),
      note: item._clarificationNote || '',
      status: 'pending',
      proposed_text: null,
      requested_at: d.requested_at,
    }
    item._clarificationOpen = false
    window.dispatchEvent(new Event('quality-questions-updated'))
    showToast('Передано в отдел качества', 'ok')
  } catch (e) {
    showToast('Не удалось передать: ' + (e?.message || ''), 'error')
  } finally {
    item._clarificationSaving = false
  }
}

function applyClarification(item) {
  item._reply = item.clarification?.proposed_text || ''
  item._clarificationConsumed = true
  showToast('Текст вставлен в ответ — проверьте и отправьте вручную', 'ok')
}

async function sendQaAnswer(item) {
  item._qaSaving = true
  try {
    await apiFetch(`/api/v1/quality-questions/${item.clarification_id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text: item._qaReply }),
    })
    queueItems.value = queueItems.value.filter(i => i !== item)
    counts.value = { ...counts.value, questions: Math.max(0, (counts.value.questions || 1) - 1) }
    window.dispatchEvent(new Event('quality-questions-updated'))
    showToast('Ответ передан менеджеру КС', 'ok')
  } catch (e) {
    showToast('Не удалось сохранить: ' + (e?.message || ''), 'error')
  } finally {
    item._qaSaving = false
  }
}

// ── Отправка / модерация / dismiss (общая логика — см. composables/useCxQueueActions) ──
function replyEndpoint(item, visibility = 'public') {
  const id = encodeURIComponent(String(item.id))
  const map = {
    'wb:question': `/wb/questions/${id}/answer?visibility=${visibility === 'private' ? 'private' : 'public'}`,
    'ym:question': `/ym/questions/${id}/answer`,
    'ozon:question': `/ozon/questions/${id}/answer`,
  }
  return map[`${item.mp}:${item.item_type}`] || null
}

const { sendReply: _sendReply, confirmMod: _confirmMod, rejectMod, dismissItem } = useCxQueueActions(queueItems, showToast)
async function sendReply(item, visibility = 'public') {
  if (item._saveAsTemplate && !item._saveAsThemeKey) {
    showToast('Выберите группу для шаблона', 'error')
    return
  }
  const txt = item._reply?.trim() || ''
  const wantTemplate = canSaveQuestionAsTemplate.value && item._saveAsTemplate && !!txt
  const themeKey = item._saveAsThemeKey
  const ok = await _sendReply(item, entry => replyEndpoint(entry, visibility))
  if (ok && wantTemplate) {
    item._saveAsTemplate = true
    item._saveAsThemeKey = themeKey
    await saveQuestionAsTemplateIfNeeded(item, txt)
  }
}
function confirmMod(item, visibility = 'public') {
  return _confirmMod(item, isWbQuestion(item) ? visibility : '')
}

// ── Init ───────────────────────────────────────────────────────────────────
// /questions и /questions/quality используют один Vue-компонент. При переходе
// между ними компонент не размонтируется, поэтому очередь нужно перегрузить.
watch(hasQualityQuestionsAccess, async (qualityMode) => {
  activeSection.value = 'queue'
  if (qualityMode) {
    await loadQueue()
    return
  }
  await Promise.all([loadQueue(), loadPlaybookTemplates(), loadQuestionPlaybook(), loadSignature()])
})

onMounted(async () => {
  if (hasQualityQuestionsAccess.value) {
    await loadQueue()
    return
  }
  await Promise.all([loadQueue(), loadPlaybookTemplates(), loadQuestionPlaybook(), loadSignature()])
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
