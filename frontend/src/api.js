const J = { 'Content-Type': 'application/json' }

async function req(url, opts) {
  const r = await fetch(url, opts)
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail || msg } catch { /* noop */ }
    throw new Error(msg)
  }
  return r.json()
}

export const api = {
  health: () => req('/api/health'),
  graph: () => req('/api/graph'),
  voices: () => req('/api/voices'),
  createJob: (body) => req('/api/jobs', { method: 'POST', headers: J, body: JSON.stringify(body) }),
  getJob: (id) => req(`/api/jobs/${id}`),
  chooseClip: (id, decision) =>
    req(`/api/jobs/${id}/clip`, { method: 'POST', headers: J, body: JSON.stringify(decision) }),
  cancel: (id) => req(`/api/jobs/${id}/cancel`, { method: 'POST' }),
}

export function openStream(jobId, onEvent) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`)
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)) } catch { /* noop */ }
  }
  es.onerror = () => { /* 서버가 스트림을 닫으면 무시 */ }
  return es
}

export const fmt = (s) => {
  s = Math.max(0, Number(s) || 0)
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return `${m}:${r.toFixed(1).padStart(4, '0')}`
}
