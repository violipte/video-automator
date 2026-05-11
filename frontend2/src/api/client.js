// HTTP client centralizado pra falar com o FastAPI
// Em dev: vite proxy /api → http://localhost:8500
// Em prod: servido pelo FastAPI no mesmo origin

const BASE = '' // empty = same origin

async function request(method, path, body) {
  const url = BASE + path
  const opts = {
    method,
    headers: {},
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(url, opts)
  let data
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    data = await res.json()
  } else {
    data = await res.text()
  }
  if (!res.ok) {
    const msg = (data && (data.erro || data.detail || data.message)) || `HTTP ${res.status}`
    const err = new Error(msg)
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export const api = {
  get:    (path)        => request('GET', path),
  post:   (path, body)  => request('POST', path, body || {}),
  put:    (path, body)  => request('PUT', path, body || {}),
  delete: (path)        => request('DELETE', path),
}
