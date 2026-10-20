<script setup>
defineProps({
  how: { type: String, required: true },
  why: { type: String, required: true },
})
</script>

<template>
  <div class="space-y-1">
    <p class="text-[11px] text-slate-400 leading-snug">
      <span class="font-bold text-slate-500">Из чего.</span> {{ how }}
    </p>
    <p class="text-[11px] text-slate-400 leading-snug">
      <span class="font-bold text-slate-500">Зачем.</span> {{ why }}
    </p>
  </div>
</template>
