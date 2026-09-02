import type { ECharts } from 'echarts'

/** 排名趋势图 ECharts 配置辅助 */

export interface RankTrendSeriesInput {
  name: string
  points: Array<{ time: string; rank: number | null }>
}

/** 图例/tooltip：关键词 + 落地页路径，便于区分同词不同 URL */
export function formatKeywordTrendLabel(keyword: string, targetUrl?: string | null): string {
  const text = keyword.trim()
  const url = targetUrl?.trim()
  if (!url) return text

  try {
    const path = new URL(url).pathname || '/'
    const displayPath = path === '/' ? '/' : path.replace(/\/$/, '') || '/'
    return `${text} (${displayPath})`
  } catch {
    return `${text} (${url})`
  }
}

export interface RankTrendChartMeta {
  showDataZoom: boolean
  pointCount: number
}

function toTimestamp(time: string): number {
  const ts = new Date(time).getTime()
  return Number.isNaN(ts) ? 0 : ts
}

/** 构建排名趋势图 option；数据点不足时自动隐藏缩放条 */
export function buildRankTrendChartOption(
  seriesInput: RankTrendSeriesInput[],
  options?: { periodDays?: number; yAxisName?: string; gridTop?: number },
): { option: Record<string, unknown>; meta: RankTrendChartMeta } {
  const periodDays = options?.periodDays ?? 30
  const now = Date.now()
  const periodStart = now - periodDays * 24 * 3600 * 1000

  const allTimestamps = seriesInput
    .flatMap((s) => s.points.filter((p) => p.rank != null).map((p) => toTimestamp(p.time)))
    .filter((t) => t > 0)

  const timeSpan =
    allTimestamps.length >= 2
      ? Math.max(...allTimestamps) - Math.min(...allTimestamps)
      : 0

  // 至少 2 个时间点且跨度 > 1 小时，缩放条才有意义
  const showDataZoom = allTimestamps.length >= 2 && timeSpan > 3600 * 1000

  const series = seriesInput.map((s) => ({
    name: s.name,
    type: 'line',
    data: s.points
      .filter((p) => p.rank != null)
      .map((p) => [toTimestamp(p.time), p.rank]),
    connectNulls: true,
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
  }))

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      type: 'scroll',
      bottom: showDataZoom ? 32 : 4,
    },
    grid: {
      left: 55,
      right: 24,
      top: options?.gridTop ?? 24,
      bottom: showDataZoom ? 78 : 48,
    },
    xAxis: {
      type: 'time',
      min: periodStart,
      max: now,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      inverse: true,
      min: 1,
      max: 100,
      minInterval: 1,
      name: options?.yAxisName ?? '排名',
    },
    dataZoom: showDataZoom
      ? [
          { type: 'inside', xAxisIndex: 0, filterMode: 'filter' },
          {
            type: 'slider',
            xAxisIndex: 0,
            filterMode: 'filter',
            bottom: 6,
            height: 22,
            handleSize: '80%',
          },
        ]
      : [],
    series,
  }

  return {
    option,
    meta: { showDataZoom, pointCount: allTimestamps.length },
  }
}

export function applyRankTrendChart(
  chart: ECharts | null,
  seriesInput: RankTrendSeriesInput[],
  options?: Parameters<typeof buildRankTrendChartOption>[1],
): RankTrendChartMeta {
  const { option, meta } = buildRankTrendChartOption(seriesInput, options)
  chart?.setOption(option, { notMerge: true })
  return meta
}
