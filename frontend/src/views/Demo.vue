<template>
  <div class="demo-container">
    <div class="demo-header">
      <h1>PaperAgent Demo</h1>
      <p>AI论文生成系统演示</p>
    </div>

    <div class="demo-content">
      <!-- 左侧：问题输入和状态 -->
      <div class="demo-left">
        <div class="input-section">
          <h3>问题描述</h3>
          <textarea
            v-model="problemInput"
            placeholder="请输入您要分析的问题..."
            :disabled="isRunning"
            rows="8"
          ></textarea>

          <div class="model-selection">
            <label>选择模型：</label>
            <select v-model="selectedModel" :disabled="isRunning">
              <option value="gemini/gemini-2.0-flash">Gemini 2.0 Flash</option>
              <option value="gpt-4o">GPT-4o</option>
              <option value="claude-3-5-sonnet">Claude 3.5 Sonnet</option>
            </select>
          </div>

          <div class="action-buttons">
            <button @click="runDemo" :disabled="isRunning || !problemInput.trim()" class="run-btn">
              {{ isRunning ? '运行中...' : '开始分析' }}
            </button>
            <button @click="clearOutput" :disabled="isRunning" class="clear-btn">清空输出</button>
          </div>
        </div>

        <div class="status-section">
          <h3>系统状态</h3>
          <div class="status-item">
            <span>后端状态：</span>
            <span :class="['status', backendStatus]">{{ backendStatusText }}</span>
          </div>
          <div class="status-item">
            <span>运行状态：</span>
            <span :class="['status', isRunning ? 'running' : 'idle']">
              {{ isRunning ? '运行中' : '空闲' }}
            </span>
          </div>
        </div>
      </div>

      <!-- 中间：聊天对话窗口 -->
      <div class="demo-center">
        <div class="chat-section">
          <div class="chat-header">
            <h3>实时对话</h3>
            <div class="chat-actions">
              <button @click="clearChat" :disabled="isRunning" class="clear-chat-btn">清空对话</button>
              <button @click="toggleView" class="toggle-view-btn">
                {{ showRawOutput ? '聊天视图' : '原始输出' }}
              </button>
            </div>
          </div>
          <div class="chat-content">
            <XmlChatRenderer 
              v-if="!showRawOutput"
              :messages="chatMessages" 
              :is-loading="isRunning"
            />
            <div v-else class="raw-output-content" ref="outputContent">
              <pre v-if="outputText">{{ outputText }}</pre>
              <div v-else class="empty-output">输出将在这里显示...</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：文件管理 -->
      <div class="demo-right">
        <div class="files-section">
          <div class="files-header">
            <h3>生成文件</h3>
            <button @click="refreshFiles" class="refresh-btn">刷新</button>
          </div>
          <div class="files-content">
            <div v-if="files.length === 0" class="empty-files">暂无生成文件</div>
            <div v-else class="file-list">
              <div
                v-for="file in files"
                :key="file.name"
                class="file-item"
                @click="downloadFile(file)"
              >
                <div class="file-icon">📄</div>
                <div class="file-info">
                  <div class="file-name">{{ file.name }}</div>
                  <div class="file-size">{{ formatFileSize(file.size) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import XmlChatRenderer from '@/components/XmlChatRenderer.vue'

// 响应式数据
const problemInput = ref('')
const selectedModel = ref('gemini/gemini-2.0-flash')
const isRunning = ref(false)
const outputText = ref('')
const files = ref<any[]>([])
const backendStatus = ref('checking')
const backendStatusText = ref('检查中...')

// 聊天相关数据
const chatMessages = ref<any[]>([])
const showRawOutput = ref(false)

// DOM引用
const outputContent = ref<HTMLElement>()

// 示例问题
const exampleProblems = [
  `问题：考虑地球自转与复杂大气环境的多级火箭高精度弹道建模

1. 背景介绍
在标准的物理问题中，抛体运动通常被简化为只受恒定重力作用的二次曲线运动。然而，对于远程火箭或洲际导弹而言，这种简化模型会带来巨大的误差。为了精确预测火箭的飞行轨迹和落点，必须考虑一系列复杂的物理因素，包括但不限于：地球的球形形状与引力变化、大气阻力、随高度变化的大气密度、高空风场，以及由地球自转产生的科里奥利力（Coriolis force）和离心力。

本项目要求你建立一个高精度的三维弹道模型，用于描述一枚从地球某纬度发射的两级火箭的完整飞行过程（从点火升空到最终落地）。

2. 物理模型与数学假设
a) 坐标系与地球模型：

建立一个随地球自转的“发射中心坐标系”(East-North-Up, ENU)。原点O设在发射点，x轴指向正东，y轴指向正北，z轴垂直于当地地平面向上。

地球被视为一个完美的球体，半径 RE=6371 km。

地球自转角速度 ω，其大小为 ω=2π/(24×3600) ≈7.27×10⁻⁵ rad/s。在发射点（纬度为 λ），该角速度矢量可以分解为：
ω = (0, ωcosλ, ωsinλ)
在我们的ENU坐标系中。

b) 火箭基本参数：

第一级火箭：
初始总质量（含燃料）: M1=150,000 kg
结构质量（燃料耗尽后）: m1=15,000 kg
发动机恒定推力: F1=2.0×10⁶ N
燃料消耗率 (恒定): k1 (可由总燃料质量和燃烧时间计算)
第一级燃烧时间: t1=120 s

第二级火箭：
初始总质量（含燃料）: M2=30,000 kg
结构质量（燃料耗尽后）: m2=3,000 kg
发动机恒定推力: F2=5.0×10⁵ N
燃料消耗率 (恒定): k2
第二级燃烧时间: t2=180 s

火箭横截面积（用于计算风阻）: A=10 m²
空气动力学阻力系数 (简化为常数): Cd=0.5

c) 作用力分析：
火箭在飞行中受到以下几个力的作用，需要全部在三维矢量空间中进行分析：

推力 (Thrust) FT:
推力方向始终沿着火箭速度方向的反方向的姿态方向。为简化，本模型假设推力方向始终与火箭瞬时速度矢量 v 平行（即姿态完美跟随速度矢量）。
|FT|=F1 (第一级工作时), |FT|=F2 (第二级工作时), |FT|=0 (无动力滑翔时)。

引力 (Gravity) Fg:
引力大小随火箭距离地心的距离变化。设 h 为火箭距离地面的高度。
Fg(h)=−m(t)·g0·(RE/(RE+h))²
其中 g0=9.81 m/s² 是地面重力加速度, m(t) 是火箭瞬时总质量, r 是火箭的位置矢量。为简化，可以近似认为引力始终指向 −z 方向，但大小可变。

大气阻力 (Drag) FD:
阻力大小与速度的平方成正比，方向与速度矢量相反。
FD=−½·ρ(h)·Cd·A·|v|²·(v/|v|)
大气密度 ρ(h) 随高度 h 指数衰减，采用简化模型：
ρ(h)=ρ0·e^(−h/H)
其中 ρ0=1.225 kg/m³ 是海平面大气密度, H=8500 m 是大气标高。

地球自转引起的惯性力：
科里奥利力 (Coriolis Force) FC:
FC=−2m(t)(ω×v)
离心力 (Centrifugal Force): 这个力通常很小，并且可以部分地被认为是重力 g 的修正。为使问题更完整，可以包含它。
FCF = -m(t)[ω × (ω × r)]
其中 r 是火箭在地球中心坐标系下的位置矢量。为简化，可以先忽略此项，或将其对 g 的影响视为常数修正。

3. 建模任务
建立运动微分方程组：
根据牛顿第二定律（在非惯性系下），写出火箭的矢量运动方程：
m(t)·dv/dt = FT + Fg + FD + FC
将此矢量方程分解到 ENU 坐标系的 x, y, z 三个方向上，得到一个包含三个二阶常微分方程的方程组。同时，别忘了 dr/dt = v。最终你会得到一个由6个一阶常微分方程组成的方程组。

定义初始条件与阶段转换：

初始条件 (t=0):
位置: r(0)=(0,0,0)
速度: v(0)。火箭从发射架垂直升空，但为了通用性，我们设一个初始发射角。假设从正北方向发射，仰角为 θ0，方位角为 0。初始速度大小为 v_launch (可以设一个较小的值，如 10 m/s)。
发射点纬度: λ=30°N。

阶段转换：
t=t1: 第一级燃料耗尽，瞬间抛弃第一级结构质量 m1。火箭总质量从 m1 突变为 M2。第二级发动机点火。
t=t1+t2: 第二级燃料耗尽，火箭总质量变为 m2。之后火箭进入无动力滑翔阶段。
h<0: 火箭落地，仿真结束。

数值求解：
这个复杂的非线性常微分方程组没有解析解。你需要使用数值方法求解，例如四阶龙格-库塔法 (RK4)。编写程序（如使用 Python 的 scipy.integrate.solve_ivp 或 MATLAB 的 ode45）来模拟从 t=0 开始的整个弹道。

4. 分析与可视化要求
你需要通过仿真，生成以下一系列图表来分析和展示你的模型结果：

核心弹道图：
图1：三维空间弹道图 (x-y-z)，清晰地展示火箭的整个飞行轨迹。
图2：地面投影轨迹图 (x-y平面)，重点观察科里奥利力导致的弹道向东的偏移。

飞行参数随时间变化图：
图3：高度 vs. 时间 (h-t) 图。
图4：速度大小 vs. 时间 (|v|-t) 图。
图5：加速度大小 vs. 时间 (|a|-t) 图 (在此图上应能清晰看到各级发动机点火、关机和分离时的突变)。
图6：质量 vs. 时间 (m-t) 图，展示火箭质量的阶梯式下降。

对比分析图（模型的精髓）：
图7：科里奥利力效应分析。在同一张地面投影图上，绘制包含科里奥利力的弹道和不包含科里奥利力的弹道，用以量化其影响。
图8：大气阻力效应分析。在同一张高度-射程图上，绘制有大气阻力和无大气阻力（真空）的弹道。
图9：不同发射纬度对比。保持其他参数不变，分别模拟在赤道 (λ=0°)、中纬度 (λ=45°) 和高纬度 (λ=75°) 发射时，地面轨迹的偏移情况。

参数敏感性分析：
图10：发射角对射程的影响。保持初速度不变，模拟不同发射仰角 θ0 (例如, 从 30° 到 60° 变化) 对火箭最大射程和最大高度的影响，并绘制射程-角度关系曲线。
图11：落点散布图。假设发射角度有微小的随机扰动（例如，θ0 在 45°±0.1° 范围内呈正态分布），进行蒙特卡洛模拟（例如，模拟100次），绘制最终落点的散布图 (x-y平面)。

思考与挑战：
- 如何精确处理级间分离时的状态变量（质量、速度、位置）的瞬间变化？
- 在编程实现中，如何设计一个能够灵活处理不同飞行阶段（第一级燃烧、第二级燃烧、滑翔）的循环或状态机？
- 如果考虑更复杂的模型，例如，推力方向并不总是和速度方向一致（姿态控制），或者风场模型是一个随高度变化的矢量场 v_wind(h)，你的模型该如何扩展？
`,
]

// 方法
const checkBackendStatus = async () => {
  try {
    const response = await fetch('http://localhost:8001/health')
    const data = await response.json()

    if (data.status === 'healthy') {
      backendStatus.value = 'healthy'
      backendStatusText.value = '正常'
    } else {
      backendStatus.value = 'initializing'
      backendStatusText.value = '初始化中'
    }
  } catch (error) {
    backendStatus.value = 'error'
    backendStatusText.value = '连接失败'
  }
}

const runDemo = async () => {
  if (!problemInput.value.trim()) return

  isRunning.value = true
  outputText.value = ''
  
  // 清空聊天消息并添加用户问题
  chatMessages.value = []
  chatMessages.value.push({
    type: 'user',
    content: problemInput.value,
    timestamp: new Date()
  })

  try {
    const response = await fetch('http://localhost:8001/demo/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        problem: problemInput.value,
        model: selectedModel.value,
      }),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('无法获取响应流')
    }

    const decoder = new TextDecoder()
    let currentAiMessage = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value)
      const lines = chunk.split('\n')

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'content') {
              outputText.value += data.data + '\n'
              currentAiMessage += data.data
              
              // 更新或创建AI消息
              const lastMessage = chatMessages.value[chatMessages.value.length - 1]
              if (lastMessage && lastMessage.type === 'ai') {
                lastMessage.content = currentAiMessage
              } else {
                chatMessages.value.push({
                  type: 'ai',
                  content: currentAiMessage,
                  timestamp: new Date()
                })
              }
              
              // 自动滚动到底部
              await nextTick()
              if (outputContent.value) {
                outputContent.value.scrollTop = outputContent.value.scrollHeight
              }
            } else if (data.type === 'complete') {
              console.log('Demo执行完成')
              await refreshFiles()
            } else if (data.type === 'error') {
              console.error('Demo执行出错:', data.data)
              outputText.value += `\n[错误] ${data.data}\n`
              chatMessages.value.push({
                type: 'system',
                content: `[错误] ${data.data}`,
                timestamp: new Date()
              })
            }
          } catch (e) {
            console.error('解析响应数据失败:', e)
          }
        }
      }
    }
  } catch (error) {
    console.error('运行Demo失败:', error)
    outputText.value += `\n[错误] 运行失败: ${error}\n`
    chatMessages.value.push({
      type: 'system',
      content: `[错误] 运行失败: ${error}`,
      timestamp: new Date()
    })
  } finally {
    isRunning.value = false
  }
}

const clearOutput = () => {
  outputText.value = ''
}

const clearChat = () => {
  chatMessages.value = []
  outputText.value = ''
}

const toggleView = () => {
  showRawOutput.value = !showRawOutput.value
}

const copyOutput = async () => {
  if (!outputText.value) return
  
  try {
    await navigator.clipboard.writeText(outputText.value)
    alert('输出已复制到剪贴板')
  } catch (error) {
    console.error('复制失败:', error)
    alert('复制失败')
  }
}

const refreshFiles = async () => {
  try {
    const response = await fetch('http://localhost:8001/demo/workspace')
    const data = await response.json()
    files.value = data.files || []
  } catch (error) {
    console.error('获取文件列表失败:', error)
    files.value = []
  }
}

const downloadFile = async (file: any) => {
  try {
    const response = await fetch(`http://localhost:8001/demo/workspace/${file.name}`)
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file.name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载文件失败:', error)
    alert('下载文件失败')
  }
}

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 生命周期
onMounted(() => {
  checkBackendStatus()
  refreshFiles()

  // 设置示例问题
  problemInput.value = exampleProblems[0]

  // 定期检查后端状态
  setInterval(checkBackendStatus, 10000)
})
</script>

<style scoped>
.demo-container {
  max-width: 1400px;
  margin: 0 auto;
  padding: 20px;
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.demo-header {
  text-align: center;
  margin-bottom: 30px;
}

.demo-header h1 {
  color: #2c3e50;
  margin-bottom: 10px;
}

.demo-header p {
  color: #7f8c8d;
  font-size: 16px;
}

.demo-content {
  display: flex;
  gap: 20px;
  flex: 1;
  min-height: 0;
}

.demo-left {
  flex: 0 0 350px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.demo-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.demo-right {
  flex: 0 0 300px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 0;
}

.input-section,
.status-section,
.output-section,
.files-section,
.chat-section {
  background: white;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.input-section h3,
.status-section h3,
.output-section h3,
.files-section h3,
.chat-section h3 {
  margin: 0 0 15px 0;
  color: #2c3e50;
  font-size: 18px;
}

textarea {
  width: 100%;
  border: 1px solid #ddd;
  border-radius: 4px;
  padding: 12px;
  font-size: 14px;
  resize: vertical;
  font-family: inherit;
}

textarea:focus {
  outline: none;
  border-color: #3498db;
}

.model-selection {
  margin: 15px 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.model-selection label {
  font-weight: 500;
  color: #2c3e50;
}

.model-selection select {
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: white;
}

.action-buttons {
  display: flex;
  gap: 10px;
}

.run-btn,
.clear-btn,
.copy-btn,
.refresh-btn {
  padding: 10px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s;
}

.run-btn {
  background: #3498db;
  color: white;
  flex: 1;
}

.run-btn:hover:not(:disabled) {
  background: #2980b9;
}

.run-btn:disabled {
  background: #bdc3c7;
  cursor: not-allowed;
}

.clear-btn {
  background: #e74c3c;
  color: white;
}

.clear-btn:hover:not(:disabled) {
  background: #c0392b;
}

.status-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.status {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.status.healthy {
  background: #d5f4e6;
  color: #27ae60;
}

.status.initializing {
  background: #fef9e7;
  color: #f39c12;
}

.status.error {
  background: #fadbd8;
  color: #e74c3c;
}

.status.running {
  background: #d6eaf8;
  color: #3498db;
}

.status.idle {
  background: #f8f9fa;
  color: #6c757d;
}

.output-header,
.files-header,
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.copy-btn,
.refresh-btn,
.clear-chat-btn,
.toggle-view-btn {
  background: #95a5a6;
  color: white;
  padding: 6px 12px;
  font-size: 12px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
}

.copy-btn:hover,
.refresh-btn:hover,
.clear-chat-btn:hover,
.toggle-view-btn:hover {
  background: #7f8c8d;
}

.chat-actions {
  display: flex;
  gap: 8px;
}

.output-content,
.chat-content,
.raw-output-content {
  background: #f8f9fa;
  border: 1px solid #ddd;
  border-radius: 4px;
  padding: 15px;
  height: 500px;
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.4;
}

.output-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.empty-output {
  color: #7f8c8d;
  text-align: center;
  padding: 20px;
}

.files-content {
  max-height: 200px;
  overflow-y: auto;
}

.empty-files {
  color: #7f8c8d;
  text-align: center;
  padding: 20px;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.file-item:hover {
  background: #f8f9fa;
  border-color: #3498db;
}

.file-icon {
  font-size: 20px;
}

.file-info {
  flex: 1;
}

.file-name {
  font-weight: 500;
  color: #2c3e50;
  margin-bottom: 2px;
}

.file-size {
  font-size: 12px;
  color: #7f8c8d;
}

@media (max-width: 768px) {
  .demo-content {
    flex-direction: column;
  }

  .demo-left {
    flex: none;
  }

  .demo-center {
    flex: 1;
  }

  .demo-right {
    flex: none;
  }
}
</style>
