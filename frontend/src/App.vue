<script setup>
import './assets/main.css'
import { computed, defineAsyncComponent } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { skipDashboardShell } from './returnKiosk'

const route = useRoute()
const isBarePublic = computed(() => skipDashboardShell(route.path))
const DashboardShell = defineAsyncComponent(() => import('./layouts/DashboardShell.vue'))
</script>

<template>
  <RouterView v-if="isBarePublic" />
  <DashboardShell v-else />
</template>
