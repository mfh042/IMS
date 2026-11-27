import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 60000
})

http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const detail = error.response?.data?.detail || error.message || '请求失败'
    ElMessage.error(`请求失败：${detail}`)
    return Promise.reject(error)
  }
)

export const getHealth = () => http.get('/health')
export const getSettings = () => http.get('/settings')
export const getGraph = () => http.get('/graph')

export const listAgents = () => http.get('/agents')
export const getAgent = (code) => http.get(`/agents/${code}`)
export const runAgent = (code, event, incidentType = null) =>
  http.post(`/agents/${code}/run`, { event, incident_type: incidentType })

export const listTools = () => http.get('/tools')

export const listSamples = () => http.get('/samples')
export const getSample = (key) => http.get(`/samples/${key}`)

export const runEvent = (event, threadId = null) =>
  http.post('/events', { event, thread_id: threadId })

export const resumeConfirm = (threadId, payload) =>
  http.post(`/confirms/${threadId}`, payload)

export const listPendingConfirms = () => http.get('/confirms/pending')
export const listOpenIncidents = () => http.get('/incidents/open')
export const getAudit = (traceId) => http.get(`/audit/${traceId}`)