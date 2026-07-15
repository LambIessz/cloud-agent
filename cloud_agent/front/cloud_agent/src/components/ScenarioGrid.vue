<template>
  <div class="scenario-container">
    <el-row :gutter="20">
      <el-col
        v-for="scenario in scenarios"
        :key="scenario.id"
        :span="12"
        class="scenario-col"
      >
        <div class="scenario-card">
          <div class="card-header">
            <el-icon>
              <component :is="scenarioIconComponents[scenario.icon]" />
            </el-icon>
            <span>{{ scenario.title }}</span>
          </div>
          <div class="scenario-list">
            <div
              v-for="item in scenario.items"
              :key="item.query"
              class="scenario-item"
              @click="emit('select-query', item.query)"
            >
              {{ item.label }}
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import { ElCol, ElIcon, ElRow } from 'element-plus'
import { DataLine, List, Monitor, Share } from '@element-plus/icons-vue'
import type { ScenarioGroup, ScenarioIconName } from '../data/scenarios.js'

defineProps<{
  scenarios: ScenarioGroup[]
}>()

const emit = defineEmits<{
  'select-query': [query: string]
}>()

const scenarioIconComponents: Record<ScenarioIconName, Component> = {
  Monitor,
  List,
  DataLine,
  Share,
}
</script>
