<script setup lang="ts">
// 课程内部子页签容器：用法 <CourseTabs :tabs="[...]" v-model="active">...</CourseTabs>
// 内容通过具名插槽 tab-<key> 提供
defineProps<{ tabs: { key: string; label: string }[] }>()
const active = defineModel<string>({ required: true })
</script>

<template>
  <div>
    <div class="subtabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="subtab"
        :class="{ active: active === t.key }"
        @click="active = t.key"
      >
        {{ t.label }}
      </button>
      <span class="slot-right"><slot name="right" /></span>
    </div>
    <div v-for="t in tabs" :key="t.key" v-show="active === t.key">
      <slot :name="'tab-' + t.key" />
    </div>
  </div>
</template>

<style scoped>
.slot-right { margin-left: auto; display: flex; align-items: center; }
</style>
