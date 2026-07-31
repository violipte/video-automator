// Cliente do Painel Youtube: mesmo origin do Automator + X-Painel-Key.
// A chave fica no localStorage (pedida 1x na primeira visita; 401 re-pede).
// Segredos NUNCA voltam do backend (write-only) — ver painel_yt.py.

const KEY_STORAGE = 'v2:painelYtKey'

export function getKey() {
  return localStorage.getItem(KEY_STORAGE) || ''
}

export function setKey(k) {
  localStorage.setItem(KEY_STORAGE, k || '')
}

export async function pfetch(path, { method = 'GET', body } = {}) {
  const headers = { 'X-Painel-Key': getKey() }
  const opts = { method, headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  let data = {}
  try { data = await res.json() } catch { /* corpo vazio */ }
  if (!res.ok) {
    const err = new Error(data.detail || data.erro || `HTTP ${res.status}`)
    err.status = res.status
    throw err
  }
  return data
}
