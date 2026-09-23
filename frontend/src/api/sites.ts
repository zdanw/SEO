import http from './http'

export type SiteStatus = 'active' | 'paused' | 'archived'
export type SiteRole = 'admin' | 'operator' | 'client_viewer'

export interface ClientSite {
  id: number
  owner_user_id: number
  name: string
  domain: string
  status: SiteStatus
  industry?: string | null
  cms_type: string
  cms_api_url?: string | null
  sitemap_url?: string | null
  notes?: string | null
  role?: SiteRole | null
  created_at: string
  updated_at: string
}

export interface ClientSiteCreate {
  name: string
  domain: string
  industry?: string | null
  notes?: string | null
}

export interface ClientSiteUpdate {
  name?: string
  domain?: string
  status?: SiteStatus
  industry?: string | null
  notes?: string | null
}

export function listSites() {
  return http.get('/sites') as Promise<ClientSite[]>
}

export function createSite(payload: ClientSiteCreate) {
  return http.post('/sites', payload) as Promise<ClientSite>
}

export function updateSite(siteId: number, payload: ClientSiteUpdate) {
  return http.patch(`/sites/${siteId}`, payload) as Promise<ClientSite>
}

export function deleteSite(siteId: number) {
  return http.delete(`/sites/${siteId}`) as Promise<void>
}
