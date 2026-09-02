import http from './http'

export interface MonthlyReport {
  site: { id: number; name: string; domain: string }
  period_days: number
  generated_at: string
  summary: Record<string, number>
  markdown: string
}

export function getMonthlyReport(days = 30) {
  return http.get<any, MonthlyReport>('/reports/monthly', { params: { days } })
}

export async function exportMonthlyReport(days = 30): Promise<string> {
  const res = await http.get('/reports/monthly/export', {
    params: { days },
    responseType: 'text',
  })
  return typeof res === 'string' ? res : String(res)
}
