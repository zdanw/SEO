import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { listSites, type ClientSite } from '@/api/sites'

const STORAGE_KEY = 'current_site_id'

export const useSiteStore = defineStore('site', () => {
  const sites = ref<ClientSite[]>([])
  const currentSiteId = ref<number | null>(
    localStorage.getItem(STORAGE_KEY) ? Number(localStorage.getItem(STORAGE_KEY)) : null,
  )
  const loading = ref(false)

  const currentSite = computed(() =>
    sites.value.find((s) => s.id === currentSiteId.value) ?? null,
  )

  const isReadOnly = computed(() => currentSite.value?.role === 'client_viewer')

  async function fetchSites(): Promise<void> {
    loading.value = true
    try {
      sites.value = await listSites()
      if (!currentSiteId.value && sites.value.length > 0) {
        setCurrentSite(sites.value[0].id)
      } else if (
        currentSiteId.value &&
        !sites.value.some((s) => s.id === currentSiteId.value)
      ) {
        setCurrentSite(sites.value[0]?.id ?? null)
      }
    } finally {
      loading.value = false
    }
  }

  function setCurrentSite(id: number | null): void {
    currentSiteId.value = id
    if (id) {
      localStorage.setItem(STORAGE_KEY, String(id))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  return {
    sites,
    currentSiteId,
    currentSite,
    isReadOnly,
    loading,
    fetchSites,
    setCurrentSite,
  }
})
