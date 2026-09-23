import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { listSites, type ClientSite } from '@/api/sites'

export const useSiteStore = defineStore('site', () => {
  const sites = ref<ClientSite[]>([])
  const currentSiteId = ref<number | null>(null)
  const loading = ref(false)

  const currentSite = computed(() =>
    sites.value.find((s) => s.id === currentSiteId.value) ?? null,
  )

  async function loadSites() {
    loading.value = true
    try {
      sites.value = await listSites()
      if (
        currentSiteId.value != null &&
        !sites.value.some((s) => s.id === currentSiteId.value)
      ) {
        currentSiteId.value = null
      }
    } finally {
      loading.value = false
    }
  }

  function setCurrentSite(siteId: number | null) {
    currentSiteId.value = siteId
  }

  function clear() {
    currentSiteId.value = null
    sites.value = []
  }

  return {
    sites,
    currentSiteId,
    currentSite,
    loading,
    loadSites,
    setCurrentSite,
    clear,
  }
})
