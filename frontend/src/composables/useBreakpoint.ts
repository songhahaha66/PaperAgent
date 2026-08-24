import { computed, readonly, ref } from 'vue'

const MOBILE_QUERY = '(max-width: 768px)'
const TABLET_QUERY = '(min-width: 769px) and (max-width: 1024px)'

const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1280)
const isMobile = ref(typeof window !== 'undefined' ? window.matchMedia(MOBILE_QUERY).matches : false)
const isTablet = ref(typeof window !== 'undefined' ? window.matchMedia(TABLET_QUERY).matches : false)

let started = false
let mobileMql: MediaQueryList | null = null
let tabletMql: MediaQueryList | null = null

const sync = () => {
  if (typeof window === 'undefined') return
  width.value = window.innerWidth
  isMobile.value = window.matchMedia(MOBILE_QUERY).matches
  isTablet.value = window.matchMedia(TABLET_QUERY).matches
}

const startListening = () => {
  if (started || typeof window === 'undefined') return
  started = true
  mobileMql = window.matchMedia(MOBILE_QUERY)
  tabletMql = window.matchMedia(TABLET_QUERY)
  mobileMql.addEventListener('change', sync)
  tabletMql.addEventListener('change', sync)
  window.addEventListener('resize', sync)
  sync()
}

export function useBreakpoint() {
  startListening()

  return {
    width: readonly(width),
    isMobile: readonly(isMobile),
    isTablet: readonly(isTablet),
    isDesktop: computed(() => !isMobile.value),
  }
}
