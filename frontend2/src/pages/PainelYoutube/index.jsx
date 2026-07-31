// Painel Youtube NATIVO — reconstrução do youtube-painel dentro do Automator
// (pedido Piter 31/07; handoff Notion 3ae770cbb8de81b29ab1cae708e0933e).
// Backend: painel_yt.py (bridge Supabase, X-Painel-Key, segredos write-only).
// O painel Vercel segue existindo (colaborador do aquecimento usa lá).
import { useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card } from '../../components/Common/Card'
import { toast } from '../../components/Common/Toast'
import { pfetch, getKey, setKey } from './api'
import { Canais } from './Canais'
import './PainelYoutube.css'

const ABAS = ['Dashboard', 'Canais', 'Grade', 'Runs', 'Vídeos', 'Aquecimento', 'Alerts', 'Config']

export function PainelYoutube() {
  const [aba, setAba] = useState('Dashboard')
  // Piter 31/07: sem senha. O gate SO aparece se o backend devolver 401
  // (i.e., se uma painel_key for configurada de novo na VPS no futuro).
  const [gate, setGate] = useState(null)   // null=verificando · false=livre · true=pede chave
  useEffect(() => {
    pfetch('/api/painel-yt/canais')
      .then(() => setGate(false))
      .catch(e => setGate(e.status === 401))
  }, [])

  if (gate === null) return (
    <><PageHeader title="Painel Youtube" /><div className="py-vazio">conectando…</div></>
  )
  if (gate) return <KeyGate onOk={() => setGate(false)} />

  return (
    <>
      <PageHeader title="Painel Youtube"
                  subtitle="Rede de canais (drive-to-youtube) — canais, runs, vídeos, aquecimento" />
      <div className="py-abas">
        {ABAS.map(a => (
          <button key={a} className={`py-aba ${aba === a ? 'py-aba-on' : ''}`}
                  onClick={() => setAba(a)}>{a}</button>
        ))}
      </div>
      <Card padding="md">
        {aba === 'Dashboard' && <Dashboard />}
        {aba === 'Canais' && <Canais />}
        {aba === 'Runs' && <Runs />}
        {aba === 'Vídeos' && <Videos />}
        {aba === 'Alerts' && <Alerts />}
        {aba === 'Config' && <ConfigGlobal />}
        {aba === 'Grade' && <EmBreve nome="Grade (matriz datas×vídeos)" />}
        {aba === 'Aquecimento' && <EmBreve nome="Aquecimento (matriz 12 dias + creds + proxies)" />}
      </Card>
    </>
  )
}

function KeyGate({ onOk }) {
  const [v, setV] = useState('')
  const testar = async () => {
    setKey(v.trim())
    try {
      await pfetch('/api/painel-yt/canais')
      onOk()
    } catch (e) {
      toast.error(e.status === 401 ? 'Chave inválida' : e.message)
    }
  }
  return (
    <>
      <PageHeader title="Painel Youtube" subtitle="Acesso protegido" />
      <Card padding="md">
        <div className="py-gate">
          <p>Este painel expõe a operação da rede de canais. Informe a <strong>chave do painel</strong>
            {' '}(está em <code>painel_yt_config.json</code> na VPS — pede pro Claude se não tiver).</p>
          <div className="py-gate-linha">
            <input type="password" value={v} placeholder="X-Painel-Key"
                   onChange={e => setV(e.target.value)}
                   onKeyDown={e => e.key === 'Enter' && testar()} />
            <button className="py-btn py-btn-primario" onClick={testar}>Entrar</button>
          </div>
        </div>
      </Card>
    </>
  )
}

function EmBreve({ nome }) {
  return <div className="py-vazio">🚧 {nome} — próxima fase da migração. Por enquanto, usar o painel Vercel.</div>
}

// ============================================================ Dashboard
const ST_RUN = { running: 'py-info', success: 'py-ok', partial: 'py-warn', failed: 'py-err', killed: 'py-err' }

function Dashboard() {
  const [d, setD] = useState(null)
  useEffect(() => {
    const tick = () => pfetch('/api/painel-yt/dashboard').then(setD).catch(e => toast.error(e.message))
    tick()
    const id = setInterval(tick, 30000)
    return () => clearInterval(id)
  }, [])
  if (!d) return <div className="py-vazio">carregando…</div>

  const ativos = d.canais.filter(c => c.status === 'ativo')
  const semToken = ativos.filter(c => !c.token_yt_set)
  const semProxy = ativos.filter(c => !c.proxy_socks5?.host)
  const healthRuim = (d.health || []).filter(h => h.status !== 'ok')

  return (
    <div>
      <div className="py-cards">
        <Stat n={ativos.length} rot={`canais ativos (${d.canais.length} total)`} />
        <Stat n={semToken.length} rot="sem token OAuth" ruim={semToken.length > 0} />
        <Stat n={semProxy.length} rot="sem proxy" ruim={semProxy.length > 0} />
        <Stat n={(d.alerts || []).length} rot="alerts não lidos" ruim={d.alerts?.length > 0} />
        <Stat n={healthRuim.length} rot="health com problema" ruim={healthRuim.length > 0} />
      </div>

      {d.alerts?.length > 0 && (
        <>
          <h4 className="py-secao">⚠ Alerts abertos</h4>
          {d.alerts.slice(0, 5).map(a => (
            <div key={a.id} className={`py-alert py-alert-${a.severity}`}>
              <strong>{a.title}</strong> {a.message && <span className="py-mut">— {a.message}</span>}
            </div>
          ))}
        </>
      )}

      <h4 className="py-secao">Últimos runs</h4>
      <TabelaRuns runs={d.runs} />

      <h4 className="py-secao">Últimos vídeos</h4>
      <TabelaVideos videos={d.videos} />
    </div>
  )
}

function Stat({ n, rot, ruim }) {
  return (
    <div className={`py-stat ${ruim ? 'py-stat-ruim' : ''}`}>
      <div className="py-stat-n">{n}</div>
      <div className="py-stat-rot">{rot}</div>
    </div>
  )
}

// ============================================================ Runs / Vídeos
function fmtData(s) {
  if (!s) return '—'
  return s.replace('T', ' ').slice(0, 16)
}

function resumoVideosRun(r) {
  // "qual video e' aquele": data de publicacao + titulo (Piter 31/07 — run so
  // com canal+inicio nao diz nada). 1 video = caso normal; >1 = "+N".
  const vs = r.videos || []
  if (!vs.length) return <span className="py-mut">—</span>
  const v = vs[0]
  const dataPub = v.publish_at_utc ? v.publish_at_utc.slice(0, 10).split('-').reverse().join('/') : '?'
  return (
    <span title={vs.map(x => `${x.publish_at_utc?.slice(0, 10) || '?'} · ${x.titulo || ''}`).join('\n')}>
      <strong>{dataPub}</strong>
      <span className="py-mut"> · {(v.titulo || '').slice(0, 42)}{vs.length > 1 ? ` +${vs.length - 1}` : ''}</span>
    </span>
  )
}

function TabelaRuns({ runs }) {
  if (!runs?.length) return <div className="py-vazio">nenhum run</div>
  return (
    <div className="py-tabela-wrap">
      <table className="py-tabela">
        <thead><tr><th>Canal</th><th>Vídeo (data · título)</th><th>Início</th><th>Status</th><th>OK</th><th>Falhas</th><th>Máquina</th></tr></thead>
        <tbody>
          {runs.map(r => (
            <tr key={r.id}>
              <td><strong>{r.canais_yt?.alias || r.canal_id?.slice(0, 8)}</strong></td>
              <td className="py-titulo-cel">{resumoVideosRun(r)}</td>
              <td>{fmtData(r.started_at)}</td>
              <td><span className={`py-chip ${ST_RUN[r.status] || ''}`}>{r.status}</span></td>
              <td>{r.videos_completed}/{r.videos_planned}</td><td>{r.videos_failed}</td>
              <td className="py-mut">{r.machine_hostname || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const ST_VIDEO = {
  done: 'py-ok', scheduled: 'py-ok', pinned: 'py-ok', failed: 'py-err',
  skipped: 'py-mut', pending: 'py-mut',
}

function TabelaVideos({ videos }) {
  if (!videos?.length) return <div className="py-vazio">nenhum vídeo</div>
  return (
    <div className="py-tabela-wrap">
      <table className="py-tabela">
        <thead><tr><th>Canal</th><th>Título</th><th>Stage</th><th>Publica</th><th>Thumb</th><th>Pin</th><th>Link</th></tr></thead>
        <tbody>
          {videos.map(v => (
            <tr key={v.id}>
              <td><strong>{v.canais_yt?.alias || '—'}</strong></td>
              <td className="py-titulo-cel" title={v.titulo}>{(v.titulo || '—').slice(0, 55)}</td>
              <td><span className={`py-chip ${ST_VIDEO[v.stage] || 'py-info'}`}>{v.stage}</span></td>
              <td>{fmtData(v.publish_at_utc)}</td>
              <td>{v.has_thumbnail ? '✓' : '—'}</td>
              <td>{v.has_pinned_comment ? '📌' : '—'}</td>
              <td>{v.video_id_youtube && (
                <a href={`https://youtu.be/${v.video_id_youtube}`} target="_blank" rel="noreferrer">▶</a>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Runs() {
  const [runs, setRuns] = useState(null)
  useEffect(() => {
    pfetch('/api/painel-yt/runs?limit=100').then(d => setRuns(d.runs)).catch(e => toast.error(e.message))
  }, [])
  if (!runs) return <div className="py-vazio">carregando…</div>
  return <TabelaRuns runs={runs} />
}

function Videos() {
  const [videos, setVideos] = useState(null)
  const [canal, setCanal] = useState('')
  useEffect(() => {
    pfetch(`/api/painel-yt/videos?limit=150${canal ? `&canal=${canal}` : ''}`)
      .then(d => setVideos(d.videos)).catch(e => toast.error(e.message))
  }, [canal])
  return (
    <div>
      <div className="py-barra">
        <input placeholder="filtrar por alias do canal…" value={canal}
               onChange={e => setCanal(e.target.value.trim())} />
      </div>
      {videos ? <TabelaVideos videos={videos} /> : <div className="py-vazio">carregando…</div>}
    </div>
  )
}

// ============================================================ Alerts / Config
function Alerts() {
  const [alerts, setAlerts] = useState(null)
  const load = () => pfetch('/api/painel-yt/alerts').then(d => setAlerts(d.alerts)).catch(e => toast.error(e.message))
  useEffect(() => { load() }, [])
  if (!alerts) return <div className="py-vazio">carregando…</div>
  const ack = async (id) => {
    await pfetch(`/api/painel-yt/alerts/${id}/ack`, { method: 'POST' })
    load()
  }
  return (
    <div>
      {alerts.length === 0 && <div className="py-vazio">nenhum alert 🎉</div>}
      {alerts.map(a => (
        <div key={a.id} className={`py-alert py-alert-${a.severity} ${a.acknowledged ? 'py-alert-lido' : ''}`}>
          <div>
            <strong>{a.title}</strong> {a.message && <span className="py-mut">— {a.message}</span>}
            <div className="py-mut">{fmtData(a.created_at)}</div>
          </div>
          {!a.acknowledged && <button className="py-btn" onClick={() => ack(a.id)}>OK</button>}
        </div>
      ))}
    </div>
  )
}

function ConfigGlobal() {
  const [cfg, setCfg] = useState(null)
  useEffect(() => {
    pfetch('/api/painel-yt/config').then(d => setCfg(d.config)).catch(e => toast.error(e.message))
  }, [])
  if (!cfg) return <div className="py-vazio">carregando…</div>
  return (
    <div className="py-tabela-wrap">
      <table className="py-tabela">
        <thead><tr><th>Chave</th><th>Valor</th><th>Descrição</th></tr></thead>
        <tbody>
          {cfg.map(c => (
            <tr key={c.key}>
              <td><code>{c.key}</code></td>
              <td>{c.is_secret ? '🔐 •••' : <code>{JSON.stringify(c.value)}</code>}</td>
              <td className="py-mut">{c.description || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
