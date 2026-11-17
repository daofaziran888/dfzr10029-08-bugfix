<template>
  <section class="page" data-module="dataset">
    <header class="page-head">
      <div>
        <h2>数据集管理管理</h2>
        <p class="page-desc">维护分析数据集，围绕数据集编号、数据集名称、来源类型、字段数量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记分析数据集</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据集管理清单</button>
        <button class="btn" data-testid="review-contract" type="button" @click="reviewContract">字段契约复核</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`ingest-${row.id}`" @click="openIngest(row)">接入处理</button>
            <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">版本与报告</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据集管理数据，可先登记分析数据集</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记分析数据集</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showIngest" class="modal-backdrop" data-testid="ingest-modal">
      <form class="modal" @submit.prevent="submitIngest">
        <h3>接入处理：{{ ingestRow?.['数据集编号'] }}</h3>
        <p class="page-desc">
          解析已有接入输入，按来源字段清单补齐缺失字段并生成可用数据集版本；
          字段缺失说明随版本保存，相同输入重复提交不会生成第二份版本。
        </p>
        <label class="create-field">
          <span>来源字段清单（顿号或逗号分隔）</span>
          <input v-model="ingestValues.来源字段清单" data-testid="ingest-来源字段清单" required />
        </label>
        <label class="create-field">
          <span>接入字段（解析自既有输入）</span>
          <input v-model="ingestValues.接入字段" data-testid="ingest-接入字段" />
        </label>
        <label class="create-field">
          <span>处理策略</span>
          <select v-model="ingestValues.策略" data-testid="ingest-策略">
            <option>补齐空字段</option>
            <option>剔除缺失字段</option>
          </select>
        </label>
        <label class="create-field">
          <span>字段缺失说明（随数据集版本保存）</span>
          <input v-model="ingestValues.字段缺失说明" data-testid="ingest-字段缺失说明" placeholder="不填则按策略生成默认说明" />
        </label>
        <p v-if="ingestMessage" class="error-text" data-testid="ingest-message">{{ ingestMessage }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showIngest = false">取消</button>
          <button class="btn primary" data-testid="submit-ingest" type="submit" :disabled="ingesting">
            {{ ingesting ? '处理中…' : '确认接入处理' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showDetail" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>缺失字段与最终版本：{{ detailRow?.['数据集编号'] }}</h3>
        <template v-if="finalVersion">
          <p data-testid="final-version">
            最终版本 v{{ finalVersion.版本号 }}（{{ finalVersion.可用 ? '可用' : '不可用' }}，{{ finalVersion.策略 }}），
            字段数量 {{ finalFields.length }}
          </p>
          <p data-testid="missing-fields">
            缺失字段：<template v-if="missingFields.length">{{ missingFields.join('、') }}</template><template v-else>无</template>
          </p>
          <ul v-if="missingNoteList.length" data-testid="missing-notes">
            <li v-for="note in missingNoteList" :key="note">{{ note }}</li>
          </ul>
        </template>
        <p v-else data-testid="final-version-empty">尚未生成可用数据集版本，请先执行接入处理。</p>

        <h4>数据集历史版本</h4>
        <table class="data-table" data-testid="versions-table">
          <thead>
            <tr><th>版本号</th><th>策略</th><th>字段数量</th><th>缺失字段</th><th>可用</th><th>生成时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="version in detailVersions" :key="String(version.版本号)">
              <td>v{{ version.版本号 }}</td>
              <td>{{ version.策略 }}</td>
              <td>{{ (version.字段清单 || []).length }}</td>
              <td>{{ (version.缺失字段 || []).join('、') || '—' }}</td>
              <td>{{ version.可用 ? '可用' : '不可用' }}</td>
              <td>{{ version.生成时间 }}</td>
            </tr>
            <tr v-if="!detailVersions.length">
              <td colspan="6" class="empty-state">暂无数据集版本</td>
            </tr>
          </tbody>
        </table>

        <h4>字段完整性报告</h4>
        <table class="data-table" data-testid="report-table">
          <thead>
            <tr><th>类型</th><th>字段</th><th>丢失原因</th><th>复核状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in detailReport" :key="index">
              <td>{{ item.类型 }}</td>
              <td>{{ item.字段 }}</td>
              <td>{{ item.丢失原因 }}</td>
              <td>{{ item.复核状态 }}</td>
            </tr>
            <tr v-if="!detailReport.length">
              <td colspan="4" class="empty-state">暂无字段完整性报告</td>
            </tr>
          </tbody>
        </table>

        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-detail" @click="showDetail = false">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集管理记录</span>
      <span v-if="contractMessage" data-testid="contract-message">{{ contractMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/dataset'
const columns = ["数据集编号", "数据集名称", "来源类型", "字段数量", "行数估算", "刷新周期", "负责人", "数据集状态"]
const actions = ["完成接入", "启用数据集", "归档数据集"]
const statuses = ["待接入", "已接入", "使用中", "已归档"]
const metricNames = ["使用中数据集", "待接入数据集", "已归档数据集"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const contractMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const showIngest = ref(false)
const ingesting = ref(false)
const ingestRow = ref<Row | null>(null)
const ingestMessage = ref('')
const ingestValues = ref({ 来源字段清单: '', 接入字段: '', 策略: '补齐空字段', 字段缺失说明: '' })
const showDetail = ref(false)
const detailRow = ref<Row | null>(null)
const detailVersions = ref<Row[]>([])
const detailReport = ref<Row[]>([])

const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])
const finalVersion = computed<Row | null>(() => {
  const usable = detailVersions.value.filter((version) => version.可用)
  const candidates = usable.length ? usable : detailVersions.value
  return candidates.length ? candidates[candidates.length - 1] : null
})
const finalFields = computed(() => (finalVersion.value?.字段清单 as string[] | undefined) ?? [])
const missingFields = computed(() => (finalVersion.value?.缺失字段 as string[] | undefined) ?? [])
const missingNoteList = computed(() => {
  const notes = (finalVersion.value?.字段缺失说明 ?? {}) as Record<string, string>
  return Object.entries(notes).map(([field, reason]) => `${field}：${reason}`)
})

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'dataset.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据集管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理操作失败'
  }
}

function openIngest(row: Row) {
  ingestRow.value = row
  ingestValues.value = {
    来源字段清单: ((row['来源字段清单'] as string[] | undefined) ?? []).join('、'),
    接入字段: ((row['接入字段'] as string[] | undefined) ?? []).join('、'),
    策略: '补齐空字段',
    字段缺失说明: '',
  }
  ingestMessage.value = ''
  showIngest.value = true
}

async function submitIngest() {
  if (!ingestRow.value) return
  ingesting.value = true
  ingestMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${ingestRow.value.id}/ingest`, {
      method: 'POST',
      body: JSON.stringify({
        来源字段清单: ingestValues.value.来源字段清单,
        接入字段: ingestValues.value.接入字段,
        策略: ingestValues.value.策略,
        字段缺失说明: ingestValues.value.字段缺失说明,
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '接入处理失败')
    showIngest.value = false
    await reload()
    // 提交后再打开字段完整性报告，对照既有数据
    await openDetail(ingestRow.value)
  } catch (error) {
    ingestMessage.value = error instanceof Error ? error.message : '接入处理失败'
  } finally {
    ingesting.value = false
  }
}

async function openDetail(row: Row) {
  // 每次打开都重新拉取，详情不保留字段完整性报告旧残留
  errorMessage.value = ''
  try {
    const [entryResp, versionsResp, reportResp] = await Promise.all([
      request(`${ENDPOINT}/${row.id}`),
      request(`${ENDPOINT}/${row.id}/versions`),
      request(`${ENDPOINT}/${row.id}/integrity-report`),
    ])
    if (!entryResp.ok || !versionsResp.ok || !reportResp.ok) throw new Error('数据集详情读取失败')
    detailRow.value = await entryResp.json()
    detailVersions.value = (await versionsResp.json()).items ?? []
    detailReport.value = (await reportResp.json()).items ?? []
    showDetail.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集详情读取失败'
  }
}

async function reviewContract() {
  errorMessage.value = ''
  contractMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/field-contract/review`, {
      method: 'POST', body: JSON.stringify({}),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '字段契约复核失败')
    contractMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段契约复核失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('分析数据集列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理列表读取失败'
  }
}

onMounted(reload)
</script>
