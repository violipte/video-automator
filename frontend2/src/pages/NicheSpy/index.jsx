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

/* -------- Tabela de resultados (compartilhada pelas abas Buscar e Similares) -------- */
function Resultados({ canais, mostrarSimilaridade }) {
  const [salvos, setSalvos] = useState({})   // channel_id -> 'salvando' | 'ok' | 'erro'

  async function salvar(c, tier) {
    setSalvos((s) => ({ ...s, [c.channel_id]: 'salvando' }))
    try {
      await api.post('/api/niche-spy/channels', {
        url: c.url, titulo: c.titulo, tier,
        origem: mostrarSimilaridade ? 'similar' : 'busca',
      })
      setSalvos((s) => ({ ...s, [c.channel_id]: 'ok' }))
    } catch {
      setSalvos((s) => ({ ...s, [c.channel_id]: 'erro' }))
    }
  }

  if (!canais?.length) return null
  return (
    <table className="ns-table ns-res">
      <thead>
        <tr>
          <th></th><th>Canal</th>
          {mostrarSimilaridade && <th>Sim.</th>}
          <th>Inscritos</th><th>Views/vídeo</th><th>V/sub</th><th>Vídeos</th><th>Salvar como</th>
        </tr>
      </thead>
      <tbody>
        {canais.map((c) => (
          <tr key={c.channel_id}>
            <td>{c.thumb_url && <img className="ns-thumb" src={c.thumb_url} alt="" />}</td>
            <td>
              <a href={c.url} target="_blank" rel="noreferrer" className="ns-link"><strong>{c.titulo}</strong></a>
              {c.motivo && <div className="ns-dim ns-motivo">{c.motivo}</div>}
              {c.videos_exemplo?.length > 0 && (
                <div className="ns-dim ns-motivo">{c.videos_exemplo[0].titulo}</div>
              )}
            </td>
            {mostrarSimilaridade && (
              <td><span className={`ns-sim ${(c.similaridade || 0) >= 85 ? 'alta' : ''}`}>{c.similaridade ?? '—'}</span></td>
            )}
            <td>{fmt(c.subs)}</td>
            <td>{fmt(c.views_por_video)}</td>
            <td title="views por inscrito — acima de 100 costuma indicar alcance além da base">{c.views_por_sub ?? '—'}</td>
            <td>{fmt(c.videos_count)}</td>
            <td>
              {salvos[c.channel_id] === 'ok'
                ? <span className="ns-ok">✓ salvo</span>
                : salvos[c.channel_id] === 'salvando'
                  ? <span className="ns-dim">…</span>
                  : TIERS.map((t) => (
                    <button key={t} className={`ns-tierbtn t${t}`} onClick={() => salvar(c, t)} title={`Salvar como tier ${t}`}>{t}</button>
                  ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ---------------- Sub-aba 2: Buscar Nichos ---------------- */
const CRIT_VAZIO = { q: '', min_subs: '', max_subs: '', min_views_video: '', dias: '', idioma: 'en', order: 'viewCount' }

function BuscarNichos({ semKey }) {
  const [crit, setCrit] = useState(CRIT_VAZIO)
  const [tpls, setTpls] = useState([])
  const [res, setRes] = useState(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')

  const carregarTpls = () => api.get('/api/niche-spy/templates').then((r) => setTpls(r.templates || [])).catch(() => {})
  useEffect(() => { carregarTpls() }, [])

  const set = (k) => (e) => setCrit({ ...crit, [k]: e.target.value })

  async function buscar(forcar = false) {
    if (!crit.q.trim()) { setErro('Informe uma palavra-chave'); return }
    setCarregando(true); setErro(''); setRes(null)
    try {
      const limpos = Object.fromEntries(Object.entries(crit).filter(([, v]) => v !== '' && v != null))
      const r = await api.post('/api/niche-spy/search', { criterios: limpos, forcar })
      if (!r.ok) setErro(r.erro || 'Falha na busca'); else setRes(r)
    } catch (e) { setErro(e.message) } finally { setCarregando(false) }
  }

  async function salvarTpl() {
    const nome = prompt('Nome do template de pesquisa:')
    if (!nome?.trim()) return
    const limpos = Object.fromEntries(Object.entries(crit).filter(([, v]) => v !== '' && v != null))
    try { await api.post('/api/niche-spy/templates', { nome: nome.trim(), criterios: limpos }); carregarTpls() }
    catch (e) { setErro(e.message) }
  }

  async function removerTpl(id) {
    if (!confirm('Remover este template?')) return
    try { await api.delete(`/api/niche-spy/templates/${id}`); carregarTpls() } catch (e) { setErro(e.message) }
  }

  return (
    <>
      {semKey && <div className="ns-aviso"><strong>🔑 Sem YouTube API key.</strong>
        <p>Adicione em <code>config.json → youtube_api_keys</code> para ativar a busca.</p></div>}

      <div className="ns-form ns-busca">
        <input className="ns-input ns-q" placeholder="Palavra-chave (ex: unsolved mysteries documentary)"
               value={crit.q} onChange={set('q')} onKeyDown={(e) => e.key === 'Enter' && buscar()} />
        <input className="ns-input" type="number" placeholder="Inscritos mín." value={crit.min_subs} onChange={set('min_subs')} />
        <input className="ns-input" type="number" placeholder="Inscritos máx." value={crit.max_subs} onChange={set('max_subs')} />
        <input className="ns-input" type="number" placeholder="Views/vídeo mín." value={crit.min_views_video} onChange={set('min_views_video')} />
        <select className="ns-input" value={crit.dias} onChange={set('dias')}>
          <option value="">Qualquer período</option>
          <option value="30">Últimos 30 dias</option>
          <option value="90">Últimos 90 dias</option>
          <option value="180">Últimos 6 meses</option>
          <option value="365">Último ano</option>
        </select>
        <select className="ns-input" value={crit.idioma} onChange={set('idioma')}>
          <option value="">Qualquer idioma</option>
          <option value="en">Inglês</option><option value="pt">Português</option>
          <option value="es">Espanhol</option><option value="de">Alemão</option>
        </select>
        <select className="ns-input" value={crit.order} onChange={set('order')}>
          <option value="viewCount">Mais vistos</option>
          <option value="relevance">Relevância</option>
          <option value="date">Mais recentes</option>
        </select>
        <button className="btn-primary" onClick={() => buscar(false)} disabled={carregando || semKey}>
          {carregando ? 'Buscando…' : '🔎 Buscar'}
        </button>
        <button className="btn-ghost" onClick={salvarTpl} title="Salvar estes critérios como template">💾 Template</button>
      </div>

      {tpls.length > 0 && (
        <div className="ns-tplbar">
          <span className="ns-dim">Templates:</span>
          {tpls.map((t) => (
            <span key={t.id} className="ns-chip">
              <button onClick={() => setCrit({ ...CRIT_VAZIO, ...(t.criterios || {}) })}>{t.nome}</button>
              <button className="ns-x" onClick={() => removerTpl(t.id)} title="Remover">×</button>
            </span>
          ))}
        </div>
      )}

      {erro && <div className="ns-erro">{erro}</div>}
      {res && (
        <>
          <div className="ns-resumo">
            <strong>{res.canais.length}</strong> canais no filtro <span className="ns-dim">(de {res.total_bruto} encontrados)</span>
            {res.cache && <span className="ns-cache" title="Resultado veio do cache (12h) — não gastou quota">⚡ cache</span>}
            {res.cache && <button className="btn-ghost ns-mini" onClick={() => buscar(true)}>refazer (gasta 100 un.)</button>}
          </div>
          <Resultados canais={res.canais} />
        </>
      )}
    </>
  )
}

/* ---------------- Sub-aba 3: Canais Similares (finder por referência) ---------------- */
function CanaisSimilares({ semKey }) {
  const [url, setUrl] = useState('')
  const [nBuscas, setNBuscas] = useState(3)
  const [res, setRes] = useState(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState('')

  async function buscar() {
    if (!url.trim()) { setErro('Cole o link do canal de referência'); return }
    setCarregando(true); setErro(''); setRes(null)
    try {
      const r = await api.post('/api/niche-spy/similar', { url: url.trim(), n_buscas: Number(nBuscas) })
      if (!r.ok) setErro(r.erro || 'Falha na busca'); else setRes(r)
    } catch (e) { setErro(e.message) } finally { setCarregando(false) }
  }

  return (
    <>
      {semKey && <div className="ns-aviso"><strong>🔑 Sem YouTube API key.</strong>
        <p>Adicione em <code>config.json → youtube_api_keys</code> para ativar.</p></div>}

      <div className="ns-form">
        <input className="ns-input ns-q" placeholder="Canal de referência (link ou @handle)"
               value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && buscar()} />
        <select className="ns-input" value={nBuscas} onChange={(e) => setNBuscas(e.target.value)}
                title="Cada busca custa 100 unidades de quota">
          <option value="2">2 buscas (200 un.)</option>
          <option value="3">3 buscas (300 un.)</option>
          <option value="4">4 buscas (400 un.)</option>
        </select>
        <button className="btn-primary" onClick={buscar} disabled={carregando || semKey}>
          {carregando ? 'Analisando…' : '🧬 Achar similares'}
        </button>
      </div>
      {carregando && <p className="ns-dim">Lendo os vídeos do canal, extraindo os temas e cruzando com a busca — leva ~30s.</p>}
      {erro && <div className="ns-erro">{erro}</div>}

      {res && (
        <>
          <div className="ns-resumo">
            Referência: <strong>{res.referencia?.titulo}</strong> <span className="ns-dim">({fmt(res.referencia?.subs)} inscritos)</span>
            {res.queries?.length > 0 && <span className="ns-dim"> · temas: {res.queries.map((q) => `“${q}”`).join(', ')}</span>}
          </div>
          <Resultados canais={res.canais} mostrarSimilaridade />
        </>
      )}
    </>
  )
}
