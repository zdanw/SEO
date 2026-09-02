import http from './http'

export interface Competitor {
  id: number
  user_id: number
  domain: string
  name?: string | null
  created_at: string
}

export interface CompetitorCreate {
  domain: string
  name?: string
}

export interface CompetitorRank {
  time: string
  keyword_id: number
  keyword: string
  domain: string
  rank?: number | null
  target_url?: string | null
}

export function listCompetitors() {
  return http.get<any, Competitor[]>('/competitors')
}

export function createCompetitor(payload: CompetitorCreate) {
  return http.post<any, Competitor>('/competitors', payload)
}

export function deleteCompetitor(id: number) {
  return http.delete(`/competitors/${id}`)
}

export function getCompetitorRanks(competitor_id: number, days = 30) {
  return http.get<any, CompetitorRank[]>(`/competitors/${competitor_id}/ranks`, {
    params: { days },
  })
}
