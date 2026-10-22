<script setup lang="ts">
// 轻量内联 SVG 图标集（避免引入图标库依赖）
const paths: Record<string, string> = {
  play: 'M8 5v14l11-7z',
  pause: 'M6 5h4v14H6zM14 5h4v14h-4z',
  prev: 'M6 6h2v12H6zm3.5 6 8.5 6V6z',
  next: 'M16 6h2v12h-2zM6 18l8.5-6L6 6z',
  reset: 'M12 5V2L7 6l5 4V7c3.3 0 6 2.7 6 6s-2.7 6-6 6-6-2.7-6-6H4c0 4.4 3.6 8 8 8s8-3.6 8-8-3.6-8-8-8z',
  down: 'M12 4v12m0 0 5-5m-5 5-5-5M5 20h14',
  up: 'M12 20V8m0 0 5 5m-5-5-5 5M5 4h14',
  bolt: 'M11 21 13 13 7 13 12 3 11 11 17 11z',
  info: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z',
  book: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zm0 16a2 2 0 0 1 2-2h13',
  code: 'm8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16',
  flask: 'M9 3h6v2l5 11a2 2 0 0 1-1.8 3H5.8A2 2 0 0 1 4 16L9 5zm3 6v6m-3-3h6',
  split: 'M12 3v6m0 0a4 4 0 0 0 4 4h5m-9-4a4 4 0 0 1-4 4H3m18 4-3 3m3-3-3-3M6 14l-3 3m3-3-3-3',
  radio: 'M3 10a9 9 0 0 1 18 0M7 10a5 5 0 0 1 10 0m-7.5 0a2.5 2.5 0 0 1 5 0M12 14v7',
  copy: 'M8 8h12v12H8zm-4 8V4h12',
  check: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  server: 'M4 4h16v6H4zm0 10h16v6H4zm3-3h.01M7 17h.01',
  monitor: 'M3 4h18v12H3zm6 16h6m-3-4v4',
  shield: 'M12 2 4 6v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6z',
  layers: 'm12 3 9 5-9 5-9-5zm-9 9 9 5 9-5m-18 4 9 5 9-5',
  brain: 'M12 3a4 4 0 0 0-4 4 4 4 0 0 0-2 7 4 4 0 0 0 6 3.5V3zM12 3a4 4 0 0 1 4 4 4 4 0 0 1 2 7 4 4 0 0 1-6 3.5',
}
defineProps<{ name: string; size?: number }>()
</script>

<template>
  <svg
    :width="size ?? 16"
    :height="size ?? 16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] ?? paths.info" fill="currentColor" stroke="none" v-if="['play','pause','prev','next','down','up','bolt'].includes(name)" />
    <path :d="paths[name] ?? paths.info" v-else />
  </svg>
</template>
