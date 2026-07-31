// Canais — CRUD do canais_yt (espelho do youtube-painel /canais).
// Segredos sao WRITE-ONLY: o backend so devolve *_set booleans; os inputs de
// segredo gravam quando preenchidos e "•••• (gravado)" quando ja existem.
//
// 31/07 — FUSAO com a aba "Cadastro Canal" (decisao Piter): a config de PRODUCAO
// (nicho, motor, tier, hook, voz, visual, trilha, CTA, personagem) virou a aba
// "Producao" DESTE modal, gravando em canais_yt.producao (JSONB). Um canal = um
// lugar. Os campos que o cadastro duplicava morreram: nome/sigla/idioma/email/
// proxy/playlist/oauth ja tem dono canonico aqui.
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

  const semProducao = canais.filter(c => !Object.keys(c.producao || {}).length).length

  return (
    <div>
      <div className="py-barra">
        <span>{canais.length} canais{semProducao ? ` · ${semProducao} sem config de produção` : ''}</span>
        <button className="py-btn py-btn-primario" onClick={() => setEditando({})}>+ Novo canal</button>
      </div>
      <div className="py-tabela-wrap">
        <table className="py-tabela">
          <thead>
            <tr>
              <th>#</th><th>Alias</th><th>#Order</th><th>Nome YouTube</th><th>Status</th>
              <th>Produção</th><th>Proxy</th><th>Token</th><th>AdsPower</th><th>TZ</th><th>Slots</th>
            </tr>
          </thead>
          <tbody>
            {canais.map(c => (
              <tr key={c.id} onClick={() => setEditando(c)} className="py-linha-click">
                <td>{c.ordem ?? '—'}</td>
                <td><strong>{c.alias}</strong></td>
                <td>{c.pedido_num || <span className="py-mut">—</span>}</td>
                <td>{c.nome_youtube}</td>
                <td><span className={`py-chip ${STATUS_CORES[c.status] || ''}`}>{c.status}</span></td>
                <td>{c.producao?.motor
                  ? <span className="py-chip py-ok">{String(c.producao.motor).split(' ')[0]}{c.producao.tier ? ` · ${String(c.producao.tier).split(' ')[0]}` : ''}</span>
                  : <span className="py-mut">—</span>}
                </td>
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
  ['alias', 'Alias / sigla do canal (vira a coluna do grid)'], ['nome_youtube', 'Nome do canal no YouTube'],
  ['channel_id_youtube', 'Channel ID (UC…)'], ['email_google', 'Email Google'],
  ['email_recuperacao', 'Email de recuperação'], ['google_cloud_project', 'Projeto Google Cloud'],
  ['adspower_profile_id', 'Profile AdsPower'], ['playlist_id', 'Playlist ID'],
  ['drive_folder_id', 'Pasta do Drive'], ['timezone', 'Timezone (ex America/Phoenix)'],
  ['yt_lang', 'Idioma (ex en)'], ['category_id', 'Categoria (ex 22)'],
  ['telefone', 'Telefone'], ['pedido_num', '#Order (nº pedido gmail/canal comprado)'],
]

// ---- PRODUCAO (fusao do Cadastro Canal) — grava em canais_yt.producao ----
// t: text | textarea | select | toggle. Cada campo alimenta template/pipeline/style_card.
const PRODUCAO = [
  { titulo: 'Identidade & Nicho', campos: [
    { k: 'handle', l: 'Handle/@ do canal', t: 'text' },
    { k: 'nicho', l: 'Nicho principal', t: 'text' },
    { k: 'tipo_a', l: 'Nicho de PRODUTO/MODELO (tipo A — carros, bikes, tênis)? B-roll travado no modelo exato', t: 'toggle' },
  ] },
  { titulo: 'Formato do vídeo', campos: [
    { k: 'motor', l: 'Motor de edição', t: 'select', ops: ['simples (imagens+zoom)', 'vidmator (edição dinâmica)', 'híbrido'] },
    { k: 'estilo', l: 'Estilo (se vidmator/híbrido)', t: 'select', ops: ['v1 (limpo, aprovado)', 'v2 (trilha por momento + SFX + overlays)'] },
    { k: 'tier', l: 'Tier de footage', t: 'select', ops: ['T1 — só stock', 'T2 — stock + CC/domínio público', 'T3 — web completo'] },
    { k: 'roteiro_chars', l: 'Tamanho do roteiro', t: 'select', ops: ['~8k chars (8-10min)', '~13k (12-15min)', '~23-26k (20-30min)', 'outro'] },
    { k: 'hook_tempo', l: 'Tempo do hook', t: 'select', ops: ['15s', '30s', '1min', '2min', 'custom'] },
    { k: 'abertura', l: 'Estilo de abertura', t: 'select', ops: ['com frase (cold-open quote + typewriter)', 'sem frase'] },
  ] },
  { titulo: 'Voz & narração', campos: [
    { k: 'voz_modo', l: 'Voz', t: 'select', ops: ['clonar voz nova (mandar ref 1-3min)', 'usar voz existente'] },
    { k: 'voz_nome', l: 'Nome/ID da voz (ex.: George, Brian)', t: 'text' },
    { k: 'voz_provider', l: 'Provider', t: 'select', ops: ['chatterbox (local, pool)', 'minimax_clone', 'inworld', 'ai33'] },
    { k: 'voz_speed', l: 'Speed (default 0.95)', t: 'text' },
  ] },
  { titulo: 'Visual', campos: [
    { k: 'paleta', l: 'Paleta (2 cores hex ou mood — ex.: dourado/pedra)', t: 'text' },
    { k: 'fonte', l: 'Fonte-tema', t: 'select', ops: ['default do motor', 'serif (clássico/história)', 'sans (moderno/tech)'] },
    { k: 'fundo', l: 'Fundo (motor simples): pasta de imagens OU vídeo loop; n/a se vidmator', t: 'text' },
    { k: 'legenda', l: 'Legenda queimada', t: 'select', ops: ['não', 'sim — estilo 1', 'sim — estilo 2', 'sim — estilo 3', 'sim — estilo 4', 'sim — estilo 5'] },
    { k: 'moldura', l: 'Marca d’água/moldura (caminho)', t: 'text' },
    { k: 'personagem', l: 'Canal terá PERSONAGEM dinâmico (mascote nos beats)?', t: 'toggle' },
    { k: 'personagem_pasta', l: 'Pasta com as imagens do personagem (poses)', t: 'text' },
    { k: 'thumb', l: 'Estilo de thumb (referência ou descrição)', t: 'textarea' },
  ] },
  { titulo: 'Áudio', campos: [
    { k: 'trilha_modo', l: 'Trilha', t: 'select', ops: ['pasta local (por momento, v2 automático)', 'pasta fixa no Drive', 'arquivo único'] },
    { k: 'trilha_detalhe', l: 'Pasta/arquivo da trilha (se fixa)', t: 'text' },
    { k: 'trilha_volume', l: 'Volume da trilha (default 0.08 v2)', t: 'text' },
    { k: 'sfx', l: 'SFX/whoosh/transitions (v2)', t: 'select', ops: ['sim (default)', 'reduzido', 'não'] },
  ] },
  { titulo: 'Produto & CTA', campos: [
    { k: 'tem_produto', l: 'Tem produto/eBook pra pitch? (se sim, pitch OBRIGATÓRIO em todo vídeo — 3 gates)', t: 'toggle' },
    { k: 'produto_nome', l: 'Nome do produto', t: 'text' },
    { k: 'link_destino', l: 'link_destino (URL do produto)', t: 'text' },
    { k: 'cta_overlay', l: 'CTA subscribe overlay', t: 'select', ops: ['default (30s, 8s, a cada 300s)', 'custom', 'não'] },
    { k: 'descricao_canal', l: 'Descrição do canal', t: 'textarea' },
  ] },
]

// Colunas REAIS do Supabase que o cadastro duplicava — editadas junto da produção
const CAMPOS_PUB = [
  ['default_description', 'Descrição default dos vídeos (todo upload usa)'],
  ['comment_template', 'Comentário fixado padrão (CTA)'],
]

function CampoProd({ c, valor, onChange }) {
  return (
    <label className={`py-campo ${c.t === 'textarea' ? 'py-campo-full' : ''}`}>
      <span>{c.l}</span>
      {c.t === 'text' && <input value={valor ?? ''} onChange={e => onChange(e.target.value)} />}
      {c.t === 'textarea' && <textarea rows={3} value={valor ?? ''} onChange={e => onChange(e.target.value)} />}
      {c.t === 'select' && (
        <select value={valor ?? ''} onChange={e => onChange(e.target.value)}>
          <option value="">— escolher —</option>
          {c.ops.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )}
      {c.t === 'toggle' && (
        <div className="py-toggle">
          <button type="button" className={valor === true ? 'on' : ''} onClick={() => onChange(true)}>sim</button>
          <button type="button" className={valor === false ? 'on' : ''} onClick={() => onChange(false)}>não</button>
        </div>
      )}
    </label>
  )
}

function CanalModal({ canal, onClose, onSaved }) {
  const novo = !canal.alias
  const [tab, setTab] = useState('Infra')
  const [f, setF] = useState(() => ({
    status: 'ativo', timezone: 'UTC', ...canal,
    proxy_host: canal.proxy_socks5?.host || '',
    proxy_port: canal.proxy_socks5?.port || '',
    proxy_user: canal.proxy_socks5?.user || '',
    proxy_pass: '',
    token_yt_texto: '', autenticador_2fa: '', backup_codes: '',
  }))
  const [prod, setProd] = useState(() => ({ ...(canal.producao || {}) }))
  const [salvando, setSalvando] = useState(false)
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))
  const setP = (k, v) => setProd(p => ({ ...p, [k]: v }))

  const salvar = async () => {
    setSalvando(true)
    try {
      const body = {}
      for (const [k] of CAMPOS_TEXTO) body[k] = f[k] ?? null
      for (const [k] of CAMPOS_PUB) body[k] = f[k] ?? null
      body.notes = f.notes ?? null
      body.raw_info = f.raw_info ?? null
      body.status = f.status
      body.ordem = f.ordem === '' || f.ordem == null ? null : Number(f.ordem)
      body.enable_pin_rpa = !!f.enable_pin_rpa
      body.producao = prod
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

        <div className="py-abas py-abas-modal">
          {['Infra', 'Produção', 'Segredos'].map(t => (
            <button key={t} className={`py-aba ${tab === t ? 'py-aba-on' : ''}`}
                    onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        <div className="py-modal-corpo">
          {tab === 'Infra' && <>
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

            <h4 className="py-secao">Notas & Raw</h4>
            <div className="py-grid2">
              <label className="py-campo py-campo-full"><span>Obs / Notas</span>
                <textarea rows={4} value={f.notes ?? ''}
                          onChange={e => set('notes', e.target.value)} /></label>
              <label className="py-campo py-campo-full">
                <span>Informações RAW (dump do pedido: emails, senhas antigas, o que vier)</span>
                <textarea rows={6} value={f.raw_info ?? ''}
                          onChange={e => set('raw_info', e.target.value)} /></label>
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
          </>}

          {tab === 'Produção' && <>
            <p className="py-nota">
              Config que alimenta template, pipeline e style_card do VidMator.
              {canal.producao_local && ' ⚠ gravando em JSON local — a coluna canais_yt.producao ainda não existe.'}
            </p>
            {PRODUCAO.map(sec => (
              <div key={sec.titulo}>
                <h4 className="py-secao">{sec.titulo}</h4>
                <div className="py-grid2">
                  {sec.campos.map(c => (
                    <CampoProd key={c.k} c={c} valor={prod[c.k]} onChange={v => setP(c.k, v)} />
                  ))}
                </div>
              </div>
            ))}
            <h4 className="py-secao">Publicação (colunas do painel)</h4>
            <div className="py-grid2">
              {CAMPOS_PUB.map(([k, label]) => (
                <label key={k} className="py-campo py-campo-full"><span>{label}</span>
                  <textarea rows={3} value={f[k] ?? ''} onChange={e => set(k, e.target.value)} /></label>
              ))}
            </div>
          </>}

          {tab === 'Segredos' && <>
            <p className="py-nota">Write-only — para VER um segredo, use o painel Vercel (HTTPS).</p>
            <div className="py-grid2">
              <label className="py-campo"><span>token_yt_json {canal.token_yt_set && '(🔐 gravado — vazio mantém)'}</span>
                <textarea rows={3} value={f.token_yt_texto} placeholder='{"refresh_token": "..."}'
                          onChange={e => set('token_yt_texto', e.target.value)} /></label>
              <label className="py-campo"><span>Chave 2FA {canal.autenticador_2fa_set && '(🔐 gravada)'}</span>
                <input value={f.autenticador_2fa} onChange={e => set('autenticador_2fa', e.target.value)} /></label>
              <label className="py-campo"><span>Backup codes {canal.backup_codes_set && '(🔐 gravados)'}</span>
                <textarea rows={2} value={f.backup_codes} onChange={e => set('backup_codes', e.target.value)} /></label>
            </div>
          </>}
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
