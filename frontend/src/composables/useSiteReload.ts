import { watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useSiteStore } from '@/stores/site'

/** 当前站点变化时重新拉取业务数据（首次挂载不重复触发）。 */
export function useSiteReload(reload: () => void | Promise<void>) {
  const { currentSiteId } = storeToRefs(useSiteStore())
  watch(currentSiteId, () => {
    void reload()
  })
}
