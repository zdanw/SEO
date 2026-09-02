import http from './http'

export interface ClientSite {
  id: number
  owner_user_id: number
  name: string
  domain: string
  status: 'active' | 'paused' | 'archived'
  industry?: string | null
  cms_type: 'none' | 'wordpress' | 'shopify' | 'custom'
  cms_api_url?: string | null
  sitemap_url?: string | null
  notes?: string | null
  created_at: string
  updated_at: string
  role?: 'admin' | 'operator' | 'client_viewer' | null
}

export interface ClientSiteCreate {
  name: string
  domain: string
  industry?: string
  cms_type?: ClientSite['cms_type']
  cms_api_url?: string
  cms_api_key?: string
  sitemap_url?: string
  notes?: string
}

export function listSites() {
  return http.get<any, ClientSite[]>('/sites')
}

export function createSite(data: ClientSiteCreate) {
  return http.post<any, ClientSite>('/sites', data)
}

export function updateSite(id: number, data: Partial<ClientSiteCreate> & { status?: string }) {
  return http.patch<any, ClientSite>(`/sites/${id}`, data)
}

export function deleteSite(id: number) {
  return http.delete(`/sites/${id}`)
}

export function addSiteMember(siteId: number, userId: number, role: string) {
  return http.post(`/sites/${siteId}/members`, { user_id: userId, role })
}
