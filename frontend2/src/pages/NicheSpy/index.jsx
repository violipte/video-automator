import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import './NicheSpy.css'

const TABS = [
  { id: 'salvos', icon: '⭐', label: 'Canais Salvos' },
  { id: 'buscar', icon: '🔎', label: 'Buscar Nichos' },
  { id: 'similar', icon: '🧬', label: 'Canais Similares' },
]

const TIERS = ['S', 'A', 'B']
const fmt = (n) => (n == null ? '—' : Intl.NumberFormat('pt-BR', { notation: 'compact' }).format(n))

export function NicheSpy() {
  const [tab, setTab] = useState('salvos')
  const [status, setStatus] = useState(null)

  useEffect(() => {
    api.get('/api/niche-spy/status').then(setStatus).catch(() => setStatus({ ok: false }))
  }, [])

  const semKey = status && (!status.youtube_keys || status.youtube_keys.length === 0)

  return (
    <div className="page nichespy">
      <header className="page-header">
        <div>
          <h1>Niche Spy</h1>
          <p className="page-sub">Pesquisa de tendências e canais de referência no YouTube</p>
        </div>
        {status && !status.ok && <span className="ns-warn">⚠ Supabase não configurado</span>}
      </header>

      <div className="ns-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`ns-tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            <span>{t.icon}</span> {t.label}
          </button>
        ))}
      </div>

      {tab === 'salvos' && <CanaisSalvos />}
      {tab === 'buscar' && <BuscarNichos semKey={semKey} />}
      {tab === 'similar' && <CanaisSimilares semKey={semKey} />}
    </div>
  )
}

/* ---------------- Sub-aba 1: Canais Salvos (o "spy") ---------------- */
function CanaisSalvos() {
  const [canais, setCanais] = useState([])
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState('')
  const [fTier, setFTier] = useState('')
  const [fNicho, setFNicho] = useState('')
  const [soFav, setSoFav] = useState(false)
  // form
  const [url, setUrl] = useState('')
  const [nicho, setNicho] = useState('')
  const [tier, setTier] = useState('A')
  const [notas, setNotas] = useState('')
  const [salvando, setSalvando] = useState(false)

  const carregar = async () => {
    setCarregando(true)
    try {
      const r = await api.get('/api/niche-spy/channels')
      setCanais(r.canais || [])
      setErro(r.ok === false ? r.erro : '')
    } catch (e) { setErro(e.message) } finally { setCarregando(false) }
  }
  useEffect(() => { carregar() }, [])

  const adicionar = async (e) => {
    e.preventDefault()
    if (!url.trim()) return
    setSalvando(true); setErro('')
    try {
      const r = await api.post('/api/niche-spy/channels', { url: url.trim(), nicho: nicho.trim() || null, tier, notas: notas.trim() || null })
      if (r.ok) { setUrl(''); setNotas(''); carregar() } else { setErro(r.erro) }
    } catch (e) { setErro(e.message) } finally { setSalvando(false) }
  }

  const patch = async (id, campos) => {
    try { await api.put(`/api/niche-spy/channels/${id}`, campos); carregar() } catch (e) { setErro(e.message) }
  }
  const remover = async (id, nome) => {
    if (!confirm(`Remover "${nome}" dos canais salvos?`)) return
    try { await api.delete(`/api/niche-spy/channels/${id}`); carregar() } catch (e) { setErro(e.message) }
  }

  const nichos = [...new Set(canais.map((c) => c.nicho).filter(Boolean))]
  const lista = canais.filter((c) =>
    (!fTier || c.tier === fTier) && (!fNicho || c.nicho === fNicho) && (!soFav || c.favorito))

  return (
    <>
      <form className="ns-add" onSubmit={adicionar}>
        <input className="ns-input grow" placeholder="Cole o link do canal (youtube.com/@canal) ou @handle"
               value={url} onChange={(e) => setUrl(e.target.value)} />
        <input className="ns-input" placeholder="Nicho" value={nicho} onChange={(e) => setNicho(e.target.value)} />
        <select className="ns-input tier-sel" value={tier} onChange={(e) => setTier(e.target.value)}>
          {TIERS.map((t) => <option key={t} value={t}>Tier {t}</option>)}
        </select>
        <input className="ns-input" placeholder="Notas (opcional)" value={notas} onChange={(e) => setNotas(e.target.value)} />
        <button className="ns-btn primary" disabled={salvando}>{salvando ? 'Salvando…' : '+ Salvar canal'}</button>
      </form>

      {erro && <div className="ns-erro">⚠ {erro}</div>}

      <div className="ns-filtros">
        <select className="ns-input" value={fTier} onChange={(e) => setFTier(e.target.value)}>
          <option value="">Todos os tiers</option>
          {TIERS.map((t) => <option key={t} value={t}>Tier {t}</option>)}
        </select>
        <select className="ns-input" value={fNicho} onChange={(e) => setFNicho(e.target.value)}>
          <option value="">Todos os nichos</option>
          {nichos.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <label className="ns-check">
          <input type="checkbox" checked={soFav} onChange={(e) => setSoFav(e.target.checked)} /> só favoritos
        </label>
        <span className="ns-count">{lista.length} canal(is)</span>
        <button className="ns-btn" onClick={carregar}>↻</button>
      </div>

      {carregando ? <div className="ns-vazio">carregando…</div>
        : lista.length === 0 ? <div className="ns-vazio">Nenhum canal salvo ainda. Cole o link de um canal acima ☝️</div>
        : (
        <table className="ns-table">
          <thead>
            <tr><th></th><th>Canal</th><th>Nicho</th><th>Tier</th><th>Inscritos</th><th>Views</th><th>Vídeos</th><th>Notas</th><th></th></tr>
          </thead>
          <tbody>
            {lista.map((c) => (
              <tr key={c.id}>
                <td>
                  <button className={`ns-fav ${c.favorito ? 'on' : ''}`} title="Favoritar"
                          onClick={() => patch(c.id, { favorito: !c.favorito })}>★</button>
                </td>
                <td className="ns-canal">
                  {c.thumb_url && <img src={c.thumb_url} alt="" />}
                  <div>
                    <strong>{c.titulo || c.channel_id}</strong>
                    {c.handle && <span className="ns-handle">{c.handle}</span>}
                  </div>
                </td>
                <td>{c.nicho || <span className="ns-dim">—</span>}</td>
                <td>
                  <select className={`ns-tier t${c.tier || ''}`} value={c.tier || ''}
                          onChange={(e) => patch(c.id, { tier: e.target.value })}>
                    <option value="">—</option>
                    {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td>{fmt(c.subs)}</td>
                <td>{fmt(c.views_total)}</td>
                <td>{fmt(c.videos_count)}</td>
                <td className="ns-notas">{c.notas || <span className="ns-dim">—</span>}</td>
                <td className="ns-acoes">
                  <a className="link-pill" href={c.url || `https://youtube.com/channel/${c.channel_id}`}
                     target="_blank" rel="noopener">▶ Abrir</a>
                  <button className="ns-btn danger" onClick={() => remover(c.id, c.titulo)}>×</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

/* ---------------- Sub-aba 2: Buscar Nichos (precisa de YouTube API key) ---------------- */
function BuscarNichos({ semKey }) {
  const [tpls, setTpls] = useState([])
  useEffect(() => { api.get('/api/niche-spy/templates').then((r) => setTpls(r.templates || [])).catch(() => {}) }, [])

  return (
    <>
      {semKey && (
        <div className="ns-aviso">
          <strong>🔑 Aguardando YouTube Data API key.</strong>
          <p>A busca por critérios precisa da API do YouTube (pedida ao Claude do Link Tracker).
             Assim que a key entrar em <code>config.json → youtube_api_keys</code>, esta aba fica ativa.</p>
          <p className="ns-dim">Nota de quota: <code>search.list</code> custa 100 unidades (limite 10.000/dia por projeto),
             então as buscas ficam em cache no Supabase pra não desperdiçar.</p>
        </div>
      )}
      <div className="ns-placeholder">
        <h3>Critérios de busca</h3>
        <p className="ns-dim">Palavra-chave · faixa de inscritos · faixa de views · período · idioma/país · ordenação.
           Resultados viram cards com métricas e botão “salvar como canal espionado”.</p>
        <h3>Templates de pesquisa {tpls.length > 0 && <span className="ns-count">{tpls.length}</span>}</h3>
        {tpls.length === 0
          ? <p className="ns-dim">Nenhum template salvo ainda. Você poderá salvar combinações de critérios e reutilizar.</p>
          : <ul className="ns-tpls">{tpls.map((t) => <li key={t.id}><strong>{t.nome}</strong> <span className="ns-dim">{t.descricao}</span></li>)}</ul>}
      </div>
    </>
  )
}

/* ---------------- Sub-aba 3: Canais Similares (finder por referência) ---------------- */
function CanaisSimilares({ semKey }) {
  return (
    <>
      {semKey && (
        <div className="ns-aviso">
          <strong>🔑 Aguardando YouTube Data API key.</strong>
          <p>O finder por canal de referência também depende da API.</p>
        </div>
      )}
      <div className="ns-placeholder">
        <h3>Como vai funcionar</h3>
        <ol className="ns-passos">
          <li>Você cola um <strong>canal de referência</strong> (um que você curtiu).</li>
          <li>O sistema lê os vídeos recentes dele e <strong>extrai os temas/padrões</strong> (via LLM).</li>
          <li>Busca no YouTube por esses termos e <strong>agrega os canais</strong> que aparecem.</li>
          <li>Filtra por métricas e <strong>pontua a semelhança</strong> com o canal-ref.</li>
          <li>Você favorita / salva com tier os que valerem a pena.</li>
        </ol>
        <p className="ns-dim">⚠ O YouTube não tem endpoint oficial de “canais relacionados”
           (o <code>relatedToVideoId</code> foi descontinuado em 2023) — por isso a abordagem é heurística.</p>
      </div>
    </>
  )
}
