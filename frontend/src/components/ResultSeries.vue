<template>
  <figure class="series-chart">
    <figcaption>{{ label }}</figcaption>
    <svg viewBox="0 0 640 230" role="img" :aria-label="label + '，' + axisLabel">
      <path d="M50 15V190H620" fill="none" stroke="currentColor" />
      <polyline :points="points" fill="none" stroke="var(--pa-accent)" stroke-width="2.5" />
      <polyline
        v-if="comparison?.length"
        :points="comparisonPoints"
        fill="none"
        stroke="var(--pa-warning)"
        stroke-width="2"
        stroke-dasharray="5 3"
      />
      <text x="46" y="16" text-anchor="end">{{ maximum.toPrecision(4) }}</text>
      <text x="46" y="190" text-anchor="end">{{ minimum.toPrecision(4) }}</text>
      <text x="50" y="210">1</text>
      <text x="620" y="210" text-anchor="end">{{ plotted.length }}</text>
      <text x="335" y="229" text-anchor="middle">{{ axisLabel }}</text>
    </svg>
    <p v-if="values.length > 1000">
      {{
        isZh
          ? '图中显示前 1000 个登记值；完整序列保留在原始记录中。'
          : 'Showing the first 1000 recorded values; the complete series is retained in the raw record.'
      }}
    </p>
    <small v-if="comparisonLabel"
      >{{ label }}<br />{{ comparisonLabel }} ({{ isZh ? '虚线' : 'dashed' }})</small
    >
  </figure>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useStudioCopy } from '@/lang/studio'
const { isZh } = useStudioCopy()
const props = defineProps<{
  values: number[]
  label: string
  axisLabel: string
  comparison?: number[]
  comparisonLabel?: string
}>()
const plotted = computed(() => props.values.slice(0, 1000))
const compared = computed(() => (props.comparison || []).slice(0, 1000))
const maximum = computed(() => Math.max(...plotted.value, ...compared.value))
const minimum = computed(() => Math.min(...plotted.value, ...compared.value))
const coordinates = (values: number[]) => {
  const span = maximum.value - minimum.value || 1
  return values
    .map(
      (value, index) =>
        `${50 + (index * 570) / Math.max(1, values.length - 1)},${190 - ((value - minimum.value) * 175) / span}`,
    )
    .join(' ')
}
const points = computed(() => coordinates(plotted.value))
const comparisonPoints = computed(() => coordinates(compared.value))
</script>
<style scoped>
.series-chart {
  margin: 14px 0;
  padding: 12px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
}
svg {
  width: 100%;
  max-height: 300px;
  color: var(--pa-muted);
}
text {
  font-size: 13px;
  fill: currentColor;
}
figcaption {
  font-size: 13px;
  margin-bottom: 8px;
}
</style>
