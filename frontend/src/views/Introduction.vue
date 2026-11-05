<template>
  <div class="introduction-page">
    <header class="intro-header">
      <router-link to="/" class="brand" aria-label="PaperAgent">
        <img src="/logo.png" alt="" width="34" height="40" />
        <span class="paperagent-wordmark">PaperAgent</span>
      </router-link>
      <nav :aria-label="t('introduction.navigation')">
        <a
          href="https://github.com/songhahaha66/PaperAgent"
          target="_blank"
          rel="noopener noreferrer"
        >
          <t-icon name="logo-github" /> <span>GitHub</span>
        </a>
        <router-link to="/login"
          >{{ t('introduction.signIn') }} <t-icon name="arrow-right"
        /></router-link>
      </nav>
    </header>

    <main class="intro-main">
      <section class="hero" aria-labelledby="intro-title">
        <h1 id="intro-title">
          {{ t('introduction.title') }}
          <span>{{ t('introduction.titleEnd') }}</span>
        </h1>
        <p class="hero-description">{{ t('introduction.subtitle') }}</p>
        <div class="rotating-line">
          <span>{{ t('introduction.automate') }}</span>
          <span class="rotating-text-wrapper">
            <Transition name="rotate" mode="out-in">
              <span :key="activeStep" class="rotating-text">{{
                t('introduction.' + stepKeys[activeStep] + 'Action')
              }}</span>
            </Transition>
          </span>
        </div>
        <div class="cta-buttons">
          <router-link class="primary-button" to="/home">
            {{ t('introduction.start') }} <t-icon name="arrow-right" />
          </router-link>
          <a class="paper-link" :href="paperUrl" target="_blank" rel="noopener noreferrer">
            {{ t('introduction.viewPaper') }} <t-icon name="arrow-top-right" />
          </a>
        </div>
      </section>

      <section class="case-demo" :aria-label="t('introduction.demoLabel')">
        <header class="demo-header">
          <div>
            <p>{{ t('introduction.caseLabel') }}</p>
            <h2>{{ t('introduction.caseTitle') }}</h2>
          </div>
          <button
            class="play-button"
            :aria-label="playing ? t('introduction.pause') : t('introduction.play')"
            @click="togglePlayback"
          >
            <t-icon :name="playing ? 'pause' : 'play'" />
          </button>
        </header>

        <div
          id="case-stage"
          class="demo-stage"
          role="tabpanel"
          :aria-labelledby="'step-' + activeStep"
        >
          <Transition name="scene" mode="out-in">
            <div :key="activeStep" class="scene">
              <template v-if="activeStep === 0">
                <div class="question-block">
                  <t-icon name="chat" />
                  <p>{{ t('introduction.question') }}</p>
                </div>
                <div class="attachments">
                  <span><t-icon name="file-1" />A题.pdf</span>
                  <span><t-icon name="file-1" />附件1.xlsx</span>
                  <span><t-icon name="file-1" />附件2.xlsx</span>
                </div>
                <div class="plan">
                  <p class="scene-label">{{ t('introduction.planTitle') }}</p>
                  <ul>
                    <li
                      v-for="item in ['temperature', 'moisture', 'duration', 'shrinkage']"
                      :key="item"
                    >
                      <t-icon name="check" />{{ t('introduction.' + item) }}
                    </li>
                  </ul>
                </div>
              </template>
              <template v-else-if="activeStep === 1">
                <div class="code-heading">
                  <span><t-icon name="code" />drying_model.py</span>
                  <a :href="asset('code/drying_model.py')" download
                    >{{ t('introduction.viewCode') }} <t-icon name="download"
                  /></a>
                </div>
                <pre class="code-preview"><code>{{ codeExcerpt }}</code></pre>
                <div class="method-note">
                  <t-icon name="code" />
                  <p>{{ t('introduction.method') }}</p>
                </div>
              </template>
              <template v-else-if="activeStep === 2">
                <figure>
                  <img
                    :src="asset('figures/fig3_q23_field.png')"
                    :alt="t('introduction.solveCaption')"
                    width="968"
                    height="660"
                  />
                  <figcaption>{{ t('introduction.solveCaption') }}</figcaption>
                </figure>
              </template>
              <template v-else-if="activeStep === 3">
                <figure>
                  <img
                    :src="asset('figures/fig6_validation.png')"
                    :alt="t('introduction.verifyCaption')"
                  />
                  <figcaption>{{ t('introduction.verifyCaption') }}</figcaption>
                </figure>
                <div class="validation-note">{{ t('introduction.validation') }}</div>
              </template>
              <template v-else>
                <a
                  class="paper-preview"
                  :href="paperUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  :aria-label="t('introduction.viewPaper')"
                >
                  <img
                    :src="asset('paper-preview.png')"
                    :alt="t('introduction.paperAlt')"
                    width="849"
                    height="1200"
                  />
                </a>
                <a class="paper-result" :href="paperUrl" target="_blank" rel="noopener noreferrer">
                  <span><t-icon name="file-1" />{{ t('introduction.paperResult') }}</span>
                  <t-icon name="arrow-top-right" />
                </a>
              </template>
            </div>
          </Transition>
        </div>

        <div class="demo-steps" role="tablist" :aria-label="t('introduction.steps')">
          <button
            v-for="(step, index) in stepKeys"
            :id="'step-' + index"
            :key="step"
            role="tab"
            :aria-selected="activeStep === index"
            aria-controls="case-stage"
            :tabindex="activeStep === index ? 0 : -1"
            :class="{ active: activeStep === index, passed: activeStep > index }"
            @click="selectStep(index)"
            @keydown.right.prevent="focusStep((index + 1) % stepKeys.length)"
            @keydown.left.prevent="focusStep((index + stepKeys.length - 1) % stepKeys.length)"
            @keydown.home.prevent="focusStep(0)"
            @keydown.end.prevent="focusStep(stepKeys.length - 1)"
          >
            <span class="step-track"
              ><span
                :style="{
                  width: activeStep > index ? '100%' : activeStep === index ? progress + '%' : '0%',
                }"
            /></span>
            {{ t('introduction.' + step + 'Label') }}
          </button>
        </div>
      </section>
    </main>
    <footer class="intro-footer">
      <span>{{ t('introduction.copyright') }}</span>
      <span>PDF · Word · Markdown</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const router = useRouter()
const authStore = useAuthStore()
const paperUrl = import.meta.env.BASE_URL + 'main.pdf'
const stepKeys = ['read', 'model', 'solve', 'verify', 'write'] as const
const activeStep = ref(0)
const progress = ref(0)
const playing = ref(false)
const asset = (file: string) => `${import.meta.env.BASE_URL}examples/drying/${file}`
const codeExcerpt = `class PropsQ23:
    """附录3 (problems 2, 3)."""
    h, hm = 25.0, 8.0e-7

    def rho(self, C):
        return 650.0 + 128.0 * C

    def D(self, C, T):
        return (2.4e-3 * np.exp(-0.45 / np.maximum(C, 1e-8))
                * np.exp(-3850.0 / (T + 273.15)))`

let timer: ReturnType<typeof setInterval> | undefined
let lastTick = 0
let motionPreference: MediaQueryList | undefined

function selectStep(index: number) {
  activeStep.value = index
  progress.value = 0
  playing.value = false
}
async function focusStep(index: number) {
  selectStep(index)
  await nextTick()
  document.getElementById('step-' + index)?.focus()
}
function togglePlayback() {
  if (!playing.value && activeStep.value === stepKeys.length - 1) {
    activeStep.value = 0
    progress.value = 0
  }
  lastTick = performance.now()
  playing.value = !playing.value
}
function respectMotionPreference() {
  if (motionPreference?.matches) playing.value = false
}

onMounted(() => {
  if (authStore.isAuthenticated) {
    void router.push('/home')
    return
  }
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  playing.value = !motionPreference.matches
  motionPreference.addEventListener('change', respectMotionPreference)
  lastTick = performance.now()
  timer = setInterval(() => {
    const now = performance.now()
    const elapsed = now - lastTick
    lastTick = now
    if (!playing.value || document.hidden) return
    progress.value = Math.min(100, progress.value + elapsed / 40)
    if (progress.value < 100) return
    if (activeStep.value === stepKeys.length - 1) {
      playing.value = false
      return
    }
    activeStep.value += 1
    progress.value = 0
  }, 100)
})
onUnmounted(() => {
  clearInterval(timer)
  motionPreference?.removeEventListener('change', respectMotionPreference)
})
</script>

<style scoped>
.introduction-page {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--pa-page);
  color: var(--pa-ink);
}
.intro-header,
.intro-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  width: min(1320px, calc(100% - 96px));
  margin: 0 auto;
}
.intro-header {
  min-height: 100px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 13px;
  color: var(--pa-ink);
  font-size: 24px;
  text-decoration: none;
}
.brand img {
  object-fit: contain;
  filter: var(--pa-logo-filter);
}
nav,
nav a {
  display: flex;
  align-items: center;
  gap: 9px;
}
nav {
  gap: 32px;
}
nav a,
.paper-link {
  color: var(--pa-muted);
  text-decoration: none;
  font-size: 14px;
}
nav a:hover,
.paper-link:hover {
  color: var(--pa-accent);
}
.intro-main {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 0.84fr) minmax(0, 1.16fr);
  align-items: center;
  gap: clamp(32px, 5vw, 80px);
  width: min(1320px, calc(100% - 96px));
  margin: 0 auto;
  padding: 48px 0 72px;
}
.hero h1 {
  margin: 0;
  font-size: clamp(38px, 4vw, 58px);
  line-height: 1.22;
  font-weight: 600;
  letter-spacing: -1.8px;
  text-wrap: balance;
}
.hero h1 > span {
  display: block;
  margin-top: 8px;
}
.hero-description {
  max-width: 410px;
  color: var(--pa-muted);
  font-size: 17px;
  line-height: 1.9;
  margin: 26px 0 30px;
}
.rotating-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 9px;
  font-size: 19px;
  line-height: 1.8;
}
.rotating-text-wrapper {
  display: inline-grid;
  overflow: hidden;
  min-width: 180px;
  height: 1.8em;
}
.rotating-text {
  grid-area: 1 / 1;
  color: var(--pa-accent);
  font-weight: 600;
}
.rotate-enter-active,
.rotate-leave-active {
  transition:
    transform 220ms ease,
    opacity 220ms ease;
}
.rotate-enter-from {
  opacity: 0;
  transform: translateY(100%);
}
.rotate-leave-to {
  opacity: 0;
  transform: translateY(-100%);
}
.cta-buttons {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 26px;
  margin-top: 40px;
}
.primary-button,
.paper-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}
.primary-button {
  padding: 14px 24px;
  border-radius: var(--pa-radius-control);
  background: var(--pa-accent);
  color: var(--pa-on-accent);
  font-size: 16px;
  text-decoration: none;
  transition: background 160ms;
}
.primary-button:hover {
  background: var(--pa-accent-hover);
}
.case-demo {
  min-width: 0;
  background: var(--pa-surface);
  border: 1px solid var(--pa-line-strong);
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 18px 60px rgb(29 51 39 / 7%);
}
.demo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 21px 26px;
  border-bottom: 1px solid var(--pa-line);
}
.demo-header p {
  margin: 0 0 5px;
  font-size: 12px;
  color: var(--pa-muted);
}
.demo-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.play-button {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border: 1px solid var(--pa-line);
  border-radius: 50%;
  color: var(--pa-ink);
  background: var(--pa-surface);
  cursor: pointer;
}
.play-button:hover {
  background: var(--pa-surface-hover);
}
.demo-stage {
  height: 382px;
  padding: 24px 26px;
  overflow: hidden;
  background: var(--pa-surface-muted);
}
.scene {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.scene-enter-active,
.scene-leave-active {
  transition:
    opacity 160ms ease,
    transform 160ms ease;
}
.scene-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.scene-leave-to {
  opacity: 0;
}
.question-block {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.question-block > .t-icon {
  color: var(--pa-accent);
  flex-shrink: 0;
  margin-top: 5px;
}
.question-block p {
  margin: 0;
  font-size: 15px;
  line-height: 1.8;
}
.attachments {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 18px 0 22px;
}
.attachments > span {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  border: 1px solid var(--pa-line);
  border-radius: 5px;
  padding: 6px 8px;
  background: var(--pa-surface);
  color: var(--pa-muted);
  font-size: 12px;
}
.scene-label {
  margin: 0 0 12px;
  color: var(--pa-muted);
  font-size: 12px;
}
.plan {
  padding-top: 18px;
  border-top: 1px solid var(--pa-line);
}
.plan ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 12px;
  font-size: 14px;
}
.plan li {
  display: flex;
  gap: 10px;
  align-items: center;
}
.plan .t-icon {
  color: var(--pa-accent);
}
.code-heading,
.code-heading > span {
  display: flex;
  align-items: center;
  gap: 7px;
}
.code-heading {
  justify-content: space-between;
  font-size: 12px;
}
.code-heading a {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  color: var(--pa-muted);
  text-decoration: none;
}
.code-preview {
  background: var(--pa-surface);
  color: var(--pa-ink);
  border: 1px solid var(--pa-line);
  border-radius: 7px;
  padding: 16px;
  font-size: 12px;
  line-height: 1.8;
  overflow: auto;
  margin: 14px 0;
  flex: 1;
}
.method-note {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--pa-muted);
  font-size: 12px;
}
.method-note p {
  margin: 0;
  line-height: 1.7;
}
figure {
  margin: 0;
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
figure img {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
  object-fit: contain;
  border-radius: 4px;
  background: white;
}
figcaption {
  font-size: 12px;
  line-height: 1.5;
  color: var(--pa-muted);
  text-align: center;
}
.validation-note {
  margin-top: 14px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--pa-muted);
  text-align: center;
}
.paper-preview {
  display: block;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.paper-preview img {
  display: block;
  width: min(100%, 370px);
  height: auto;
  margin: 0 auto;
  box-shadow: var(--pa-shadow-card);
}
.paper-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  font-size: 13px;
  color: var(--pa-accent);
  text-decoration: none;
}
.paper-result > span {
  display: flex;
  align-items: center;
  gap: 7px;
}
.demo-steps {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  padding: 17px 22px 20px;
}
.demo-steps button {
  cursor: pointer;
  padding: 0 3px;
  border: 0;
  background: transparent;
  color: var(--pa-muted);
  font-size: 12px;
  white-space: nowrap;
}
.demo-steps button.active {
  color: var(--pa-accent);
  font-weight: 600;
}
.step-track {
  display: block;
  height: 3px;
  background: var(--pa-line);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 11px;
}
.step-track > span {
  display: block;
  height: 100%;
  background: var(--pa-accent);
  transition: width 100ms linear;
}
.intro-footer {
  padding: 22px 0;
  border-top: 1px solid var(--pa-line);
  color: var(--pa-placeholder);
  font-size: 12px;
}
@media (max-width: 1000px) {
  .intro-main {
    gap: 32px;
    width: calc(100% - 48px);
  }
  .intro-header,
  .intro-footer {
    width: calc(100% - 48px);
  }
  .hero h1 {
    font-size: 38px;
  }
  .demo-stage {
    padding: 20px;
  }
}
@media (max-width: 780px) {
  .intro-header {
    min-height: 80px;
  }
  .brand {
    font-size: 20px;
    gap: 9px;
  }
  .brand img {
    width: 28px;
    height: 32px;
  }
  nav {
    gap: 20px;
  }
  nav a {
    font-size: 13px;
  }
  .intro-main {
    grid-template-columns: 1fr;
    width: min(600px, calc(100% - 40px));
    gap: 40px;
    padding: 32px 0 40px;
  }
  .hero h1 {
    font-size: clamp(34px, 7vw, 48px);
    letter-spacing: -1px;
  }
  .hero-description {
    margin: 20px 0;
    font-size: 15px;
    max-width: 100%;
  }
  .rotating-line {
    font-size: 17px;
  }
  .cta-buttons {
    margin-top: 26px;
  }
  .demo-header {
    padding: 18px 20px;
  }
  .demo-stage {
    height: 360px;
  }
  .intro-footer {
    flex-wrap: wrap;
    gap: 10px;
  }
}
@media (max-width: 380px) {
  nav a:first-child > span {
    display: none;
  }
  .demo-steps {
    padding: 16px 12px;
    gap: 3px;
  }
  .demo-stage {
    height: 400px;
    padding: 18px 16px;
  }
  .question-block p {
    font-size: 14px;
  }
  .attachments {
    gap: 5px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .rotate-enter-active,
  .rotate-leave-active,
  .scene-enter-active,
  .scene-leave-active,
  .step-track > span {
    transition: none;
  }
}
</style>
