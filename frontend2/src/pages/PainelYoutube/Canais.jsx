// Canais — CRUD do canais_yt (espelho do youtube-painel /canais).
// Segredos sao WRITE-ONLY: o backend so devolve *_set booleans; os inputs de
// segredo gravam quando preenchidos e "•••• (gravado)" quando ja existem.
import { useEffect, useState } from 'react'
import { toast } from '../../components/Common/Toast'
import { pfetch } from './api'

const STATUS_CORES = { ativo: 'py-ok', pausado: 'py-warn', arquivado: 'py-mut' }

export function Canais() {
  const [canais, setCanais] = useState(null)
  const [editando, setEditando] = useState(null)   // canal (edicao) | {} (novo)

  const load = async () => {
    const d = await pfetch('/api/painel-yt/canais')
    setCanais(d.canais || [])
  }
  useEffect(() => { load().catch(e => toast.error(e.message)) }, [])

  if (canais === null) return <div className="py-vazio">carregando…</div>

  return (
    <div>
      <div className="py-barra">
        <span>{canais.length} canais</span>
        <button className="py-btn py-btn-primario" onClick={() => setEditando({})}>+ Novo canal</button>
      </div>
      <div className="py-tabela-wrap">
        <table className="py-tabela">
          <thead>
            <tr>
              <th>#</th><th>Alias</th><th>Nome YouTube</th><th>Status</th>
              <th>Proxy</th><th>Token</th><th>AdsPower</th><th>TZ</th><th>Slots</th>
            </tr>
          </thead>
          <tbody>
            {canais.map(c => (
              <tr key={c.id} onClick={() => setEditando(c)} className="py-linha-click">
                <td>{c.ordem ?? '—'}</td>
                <td><strong>{c.alias}</strong></td>
                <td>{c.nome_youtube}</td>
                <td><span className={`py-chip ${STATUS_CORES[c.status] || ''}`}>{c.status}</span></td>
                <td>{c.proxy_socks5?.host
                  ? <span title={`${c.proxy_socks5.host}:${c.proxy_socks5.port}`}>
                      {c.proxy_socks5.host}{c.proxy_socks5.pass_set ? ' 🔐' : ' ⚠'}
                    </span>
                  : <span className="py-mut">sem proxy</span>}
                </td>
                <td>{c.token_yt_set ? '🔐' : <span className="py-warn-txt">falta</span>}</td>
                <td>{c.adspower_profile_id || <span className="py-mut">—</span>}</td>
                <td>{c.timezone}</td>
                <td>{(c.publish_slots || []).map(s =>
                  `${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`).join(' · ') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editando !== null && (
        <CanalModal canal={editando} onClose={() => setEditando(null)}
                    onSaved={() => { setEditando(null); load() }} />
      )}
    </div>
  )
}

const CAMPOS_TEXTO = [
  ['alias', 'Alias (interno)'], ['nome_youtube', 'Nome do canal no YouTube'],
  ['channel_id_youtube', 'Channel ID (UC…)'], ['email_google', 'Email Google'],
  ['email_recuperacao', 'Email de recuperação'], ['google_cloud_project', 'Projeto Google Cloud'],
  ['adspower_profile_id', 'Profile AdsPower'], ['playlist_id', 'Playlist ID'],
  ['drive_folder_id', 'Pasta do Drive'], ['timezone', 'Timezone (ex America/Phoenix)'],
  ['yt_lang', 'Idioma (ex en)'], ['category_id', 'Categoria (ex 22)'],
  ['telefone', 'Telefone'], ['notes', 'Notas'],
]

function CanalModal({ canal, onClose, onSaved }) {
  const novo = !canal.alias
  const [f, setF] = useState(() => ({
    status: 'ativo', timezone: 'UTC', ...canal,
    proxy_host: canal.proxy_socks5?.host || '',
    proxy_port: canal.proxy_socks5?.port || '',
    proxy_user: canal.proxy_socks5?.user || '',
    proxy_pass: '',
    token_yt_texto: '', autenticador_2fa: '', backup_codes: '',
  }))
  const [salvando, setSalvando] = useState(false)
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))

  const salvar = async () => {
    setSalvando(true)
    try {
      const body = {}
      for (const [k] of CAMPOS_TEXTO) body[k] = f[k] ?? null
      body.status = f.status
      body.ordem = f.ordem === '' || f.ordem == null ? null : Number(f.ordem)
      body.enable_pin_rpa = !!f.enable_pin_rpa
      if (f.proxy_host) {
        body.proxy_socks5 = { host: f.proxy_host, port: Number(f.proxy_port) || null,
                              user: f.proxy_user, pass: f.proxy_pass }  // pass vazio = mantém
      }
      if (f.token_yt_texto.trim()) {
        try { body.token_yt_json = JSON.parse(f.token_yt_texto) }
        catch { toast.error('token_yt_json não é JSON válido'); setSalvando(false); return }
      }
      if (f.autenticador_2fa.trim()) body.autenticador_2fa = f.autenticador_2fa.trim()
      if (f.backup_codes.trim()) body.backup_codes = f.backup_codes.trim()
      if (novo) await pfetch('/api/painel-yt/canais', { method: 'POST', body })
      else await pfetch(`/api/painel-yt/canais/${canal.alias}`, { method: 'PATCH', body })
      toast.success(novo ? 'Canal criado' : 'Canal salvo')
      onSaved()
    } catch (e) { toast.error(e.message) }
    setSalvando(false)
  }

  return (
    <div className="py-modal-fundo" onClick={onClose}>
      <div className="py-modal" onClick={e => e.stopPropagation()}>
        <div className="py-modal-cab">
          <h3>{novo ? 'Novo canal' : `Canal ${canal.alias}`}</h3>
          <button className="py-btn" onClick={onClose}>✕</button>
        </div>
        <div className="py-modal-corpo">
          <div className="py-grid2">
            {CAMPOS_TEXTO.map(([k, label]) => (
              <label key={k} className="py-campo">
                <span>{label}</span>
                <input value={f[k] ?? ''} onChange={e => set(k, e.target.value)} />
              </label>
            ))}
            <label className="py-campo"><span>Ordem</span>
              <input type="number" value={f.ordem ?? ''} onChange={e => set('ordem', e.target.value)} />
            </label>
            <label className="py-campo"><span>Status</span>
              <select value={f.status} onChange={e => set('status', e.target.value)}>
                <option>ativo</option><option>pausado</option><option>arquivado</option>
              </select>
            </label>
            <label className="py-campo py-check">
              <input type="checkbox" checked={!!f.enable_pin_rpa}
                     onChange={e => set('enable_pin_rpa', e.target.checked)} />
              <span>Pin via RPA (AdsPower)</span>
            </label>
          </div>

          <h4 className="py-secao">Proxy SOCKS5</h4>
          <div className="py-grid2">
            <label className="py-campo"><span>Host</span>
              <input value={f.proxy_host} onChange={e => set('proxy_host', e.target.value)} /></label>
            <label className="py-campo"><span>Porta</span>
              <input value={f.proxy_port} onChange={e => set('proxy_port', e.target.value)} /></label>
            <label className="py-campo"><span>Usuário</span>
              <input value={f.proxy_user} onChange={e => set('proxy_user', e.target.value)} /></label>
            <label className="py-campo"><span>Senha {canal.proxy_socks5?.pass_set && '(•••• gravada — vazio mantém)'}</span>
              <input type="password" value={f.proxy_pass}
                     placeholder={canal.proxy_socks5?.pass_set ? '••••••••' : ''}
                     onChange={e => set('proxy_pass', e.target.value)} /></label>
          </div>

          <h4 className="py-secao">Segredos (write-only — para VER, use o painel Vercel/HTTPS)</h4>
          <div className="py-grid2">
            <label className="py-campo"><span>token_yt_json {canal.token_yt_set && '(🔐 gravado — vazio mantém)'}</span>
              <textarea rows={3} value={f.token_yt_texto} placeholder='{"refresh_token": "..."}'
                        onChange={e => set('token_yt_texto', e.target.value)} /></label>
            <label className="py-campo"><span>Chave 2FA {canal.autenticador_2fa_set && '(🔐 gravada)'}</span>
              <input value={f.autenticador_2fa} onChange={e => set('autenticador_2fa', e.target.value)} /></label>
            <label className="py-campo"><span>Backup codes {canal.backup_codes_set && '(🔐 gravados)'}</span>
              <textarea rows={2} value={f.backup_codes} onChange={e => set('backup_codes', e.target.value)} /></label>
          </div>
        </div>
        <div className="py-modal-pe">
          <button className="py-btn" onClick={onClose}>Cancelar</button>
          <button className="py-btn py-btn-primario" disabled={salvando} onClick={salvar}>
            {salvando ? 'salvando…' : 'Salvar'}
          </button>
        </div>
      </div>
    </div>
  )
}
