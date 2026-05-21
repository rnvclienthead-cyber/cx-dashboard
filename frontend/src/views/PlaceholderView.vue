<script setup>
import { Star } from 'lucide-vue-next'
</script>

<template>
  <div class="p-6 w-full mx-auto min-h-screen bg-slate-50 flex flex-col items-center justify-center">
    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm p-12 max-w-md text-center flex flex-col items-center animate-fade-in">
      <div class="p-4 bg-amber-100 text-amber-500 rounded-2xl mb-4">
        <Star class="w-12 h-12 animate-pulse" />
      </div>
      
      <h2 class="text-xl font-black text-slate-800 mb-2">Рейтинг товаров</h2>
      <p class="text-sm text-slate-500 mb-6 leading-relaxed">
        Этот операционный блок находится в процессе миграции со Streamlit. Совсем скоро здесь появится интерактивная тепловая матрица и индивидуальные графики состояния SKU!
      </p>
      
      <div class="text-xs font-mono bg-slate-50 text-slate-400 px-3 py-1.5 rounded-lg border border-slate-100">
        src/views/PlaceholderView.vue
      </div>
    </div>
  </div>
</template>