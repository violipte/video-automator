import React, { useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card, CardHeader, CardBody } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Modal } from '../../components/Common/Modal'
import { Select, Input } from '../../components/Common/Input'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'
import { HistoricoModal } from './HistoricoModal'
import './Monitor.css'

const ETAPA_BADGE = {
  aguardando: { variant: 'default', label: 'aguardando' },
  roteiro:    { variant: 'info', label: 'roteiro', dot: true },
  narracao:   { variant: 'info', label: 'narração', dot: true },
  video:      { variant: 'info', label: 'rendering', dot: true },
  concluido:  { variant: 'success', label: '✓ concluído' },
  erro:       { variant: 'danger', label: 'erro' },
  pulado:     { variant: 'default', label: 'pulado' },
}

export function Monitor() {
  const [health, setHealth] = useState(null)
  const [state, setState] = useState(null)
  const [telem, setTelem] = useState({workers: []})
  const [datasOpen, setDatasOpen] = useState(false)
  const [historicoOpen, setHistoricoOpen] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())

  async function refresh() {
    try {
      const [h, s, t] = await Promise.all([
        api.get('/api/health'),
        api.get('/api/production-log'),
        api.get('/api/system-telemetry').catch(() => ({workers: []})),
      ])
      setHealth(h); setState(s); setTelem(t || {workers: []})
    } catch (e) { /* silent */ }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [])

  // Tick local (1s) só pra fazer o "tempo decorrido" dos canais ativos atualizar smoothly
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  async function cancelar() {
    if (!confirm('Cancelar produção em curso?')) return
    try {
      await api.post('/api/producao-completa/cancelar')
      toast.success('Cancelado')
      refresh()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  async function reset() {
    if (!confirm('RESET completo: para tudo e zera estados.\n\nIsso vai INTERROMPER a produção em curso. Continuar?')) return
    try {
      await api.post('/api/producao-completa/reset')
      toast.success('Reset concluído')
      refresh()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  if (!health) {
    return (
      <>
        <PageHeader title="Monitor" />
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={24} /></div>
      </>
    )
  }

  const rq = health.render_queue || {}
  const isProd = !!health.producao_ativa
  const canais = state?.canais || []
  // Filtra logs LIFECYCLE (pods RunPod) - operacao agora eh local-only
  const log = (state?.log || []).filter(l => !(l?.msg || '').startsWith('LIFECYCLE')).slice(-30).reverse()

  return (
    <>
      <PageHeader
        title="Monitor"
        subtitle="Acompanhamento ao vivo da produção. Pods sobem/descem automaticamente."
        actions={
          <>
            {isProd ? (
              <>
                <Button variant="danger" onClick={cancelar}>Cancelar</Button>
                <Button variant="ghost" onClick={reset}>Reset</Button>
              </>
            ) : (
              <Button variant="primary" onClick={() => setDatasOpen(true)}>▶ Produzir Tudo</Button>
            )}
            <Button variant="ghost" onClick={() => setHistoricoOpen(true)}>📊 Histórico</Button>
            <Button variant="ghost" size="sm" onClick={refresh}>↻</Button>
          </>
        }
      />

      <div className="monitor-stats">
        <StatusCard
          title="Produção"
          value={isProd ? 'em andamento' : 'idle'}
          accent={isProd}
          detail={(() => {
            if (isProd && state?.inicio) {
              const decorrido = Math.max(0, (nowMs/1000) - state.inicio)
              return `${state.data_ref || ''} · ⏱ ${fmtDur(decorrido)}`
            }
            if (state?.data_ref && state?.duracao_seg) {
              return `${state.data_ref} · ✓ ${fmtDur(state.duracao_seg)}`
            }
            return `pid ${health.pid}`
          })()}
        />
        <StatusCard
          title="Render Queue"
          value={rq.ativo ? `${rq.fila_tamanho} jobs · 1 ▶` : `${rq.fila_tamanho || 0} jobs`}
          accent={rq.ativo}
          detail={rq.job_atual || 'nenhum job ativo'}
        />
        {telem.workers && telem.workers.length > 0 && telem.workers.slice(0, 1).map((w, idx) => {
          const gpuTemp = w.gpu_temp_c
          const gpuUtil = w.gpu_util_pct
          const gpuMemPct = w.gpu_mem_total_mb ? Math.round(100 * w.gpu_mem_used_mb / w.gpu_mem_total_mb) : 0
          const cpuUtil = w.cpu_util_pct
          const cpuTemp = w.cpu_temp_c
          const moboTemp = w.motherboard_temp_c
          const nvmeTemp = w.nvme_temp_c
          const ramUsed = w.ram_used_gb
          const ramTotal = w.ram_total_gb
          const _tcolor = (t) => t == null ? 'inherit' : (t >= 85 ? '#ff6464' : t >= 75 ? '#ffaa44' : 'inherit')
          return (
            <React.Fragment key={w.worker_id || idx}>
              <StatusCard
                title="GPU (5070 Ti)"
                value={
                  <span>
                    <span style={{color: _tcolor(gpuTemp)}}>{gpuTemp != null ? `${gpuTemp}°C` : '—'}</span>
                    {' · '}
                    <span style={{fontSize:'0.85em'}}>{gpuUtil != null ? `${gpuUtil}%` : '—'}</span>
                  </span>
                }
                accent={gpuUtil > 50}
                detail={<span style={{fontSize:'11px'}}>VRAM {gpuMemPct}% ({w.gpu_mem_used_mb||0}/{w.gpu_mem_total_mb||0} MB)</span>}
              />
              <StatusCard
                title="CPU"
                value={
                  <span>
                    <span style={{color: _tcolor(cpuTemp)}}>{cpuTemp != null ? `${cpuTemp}°C` : '—'}</span>
                    {' · '}
                    <span style={{fontSize:'0.85em'}}>{cpuUtil != null ? `${cpuUtil}%` : '—'}</span>
                  </span>
                }
                accent={cpuUtil > 50}
                detail={
                  <span style={{fontSize:'11px'}}>
                    {moboTemp != null && `Mobo ${moboTemp}°C · `}
                    {nvmeTemp != null && `NVMe ${nvmeTemp}°C · `}
                    {ramUsed != null && `RAM ${ramUsed}/${ramTotal}GB`}
                  </span>
                }
              />
            </React.Fragment>
          )
        })}
      </div>

      <div className="monitor-grid">
        <Card padding="none">
          <CardHeader>Canais ({canais.length})</CardHeader>
          {!canais.length && (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-sec)' }}>
              Nenhum canal em produção. Clique em "▶ Produzir Tudo" pra começar.
            </div>
          )}
          {canais.map((c, i) => <ChannelRow key={i} canal={c} nowMs={nowMs} />)}
        </Card>

        <Card padding="none">
          <CardHeader>Log live</CardHeader>
          <div className="monitor-log">
            {!log.length && (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                Sem entradas ainda
              </div>
            )}
            {log.map((entry, i) => (
              <div key={i} className="log-entry">
                <span className="log-ts">{(entry.ts || '').slice(11, 19)}</span>
                <span className="log-msg">{entry.msg}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <ProduzirTudoModal
        open={datasOpen}
        onClose={() => setDatasOpen(false)}
        onStarted={refresh}
      />

      <HistoricoModal
        open={historicoOpen}
        onClose={() => setHistoricoOpen(false)}
      />
    </>
  )
}

function StatusCard({ title, value, detail, accent }) {
  return (
    <Card padding="md" className={`stat-card ${accent ? 'stat-active' : ''}`}>
      <div className="stat-title">
        <span className={`stat-dot ${accent ? 'on' : ''}`} />
        {title}
      </div>
      <div className="stat-value">{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </Card>
  )
}

// Formata duração em ms ou epoch-seconds → "MmSSs" / "HhMm" curto
function fmtDur(secs) {
  if (!secs || secs < 0) return ''
  const s = Math.floor(secs)
  if (s < 60) return `${s}s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const r = s % 60
    return `${m}m${String(r).padStart(2, '0')}s`
  }
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h${String(m).padStart(2, '0')}m`
}

// canal.inicio/fim podem vir como ISO string ou epoch float (depende da versão)
function parseTs(t) {
  if (!t) return null
  if (typeof t === 'number') return t * 1000  // epoch seconds → ms
  const d = new Date(t)
  return isNaN(d) ? null : d.getTime()
}

function durationOf(c, nowMs) {
  const ini = parseTs(c.inicio)
  if (!ini) return null
  const fim = parseTs(c.fim) || nowMs
  return (fim - ini) / 1000
}

function ChannelRow({ canal: c, nowMs }) {
  const conf = ETAPA_BADGE[c.etapa] || { variant: 'default', label: c.etapa || '—' }
  const progresso = c.progresso || 0
  const dur = durationOf(c, nowMs)
  const finalizado = c.etapa === 'concluido' || c.etapa === 'erro' || c.etapa === 'pulado'
  return (
    <div className="channel-row">
      <div className="channel-tag">{c.tag}</div>
      <Badge variant={conf.variant} dot={conf.dot}>{conf.label}</Badge>
      {progresso > 0 && progresso < 100 && (
        <div className="channel-progress">
          <div
            className={`channel-progress-bar ${c.etapa === 'narracao' ? 'channel-progress-bar-narracao' : ''}`}
            style={{ width: `${progresso}%` }}
          />
          <span className="channel-progress-text">{progresso}%</span>
        </div>
      )}
      <div className="channel-detail">
        {c.etapa_detalhe || (c.video_path && c.video_path.split('/').pop()) || ''}
      </div>
      {dur != null && (
        <span className={`channel-time ${finalizado ? 'channel-time-final' : 'channel-time-live'}`} title={finalizado ? 'Tempo total' : 'Tempo decorrido (live)'}>
          ⏱ {fmtDur(dur)}
        </span>
      )}
      {c.erro && <span className="channel-erro" title={c.erro}>⚠ erro</span>}
    </div>
  )
}

function ProduzirTudoModal({ open, onClose, onStarted }) {
  const [linhas, setLinhas] = useState([])
  const [dataIdx, setDataIdx] = useState(null)
  const [loop, setLoop] = useState(true)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    if (!open) return
    api.get('/api/temas').then(d => {
      const ls = d.linhas || []
      setLinhas(ls)
      // Default: primeira data sem produção (heurística simples = última linha)
      if (ls.length) setDataIdx(Math.max(0, ls.length - 1))
    }).catch(() => {})
  }, [open])

  async function start() {
    if (dataIdx === null) { toast.warning('Escolha uma data'); return }
    setStarting(true)
    try {
      const r = await api.post('/api/producao-completa/iniciar', {
        data_idx: dataIdx,
        loop,
      })
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast.success(`Produção iniciada (${loop ? 'loop' : 'single'})`)
      onClose()
      onStarted?.()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setStarting(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="▶ Produzir Tudo"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" onClick={start} loading={starting}>Iniciar</Button>
        </>
      }
    >
      <Select
        label="Data inicial"
        value={dataIdx ?? ''}
        onChange={(e) => setDataIdx(parseInt(e.target.value))}
        options={linhas.map((l, i) => ({ value: i, label: `idx ${i} — ${l.data}` }))}
        hint="Sistema produz dessa data em diante"
      />
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, cursor: 'pointer' }}>
        <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
        <span style={{ fontSize: 'var(--text-sm)' }}>
          <strong>Modo loop</strong> — após terminar a data, avança pra próxima até o fim
        </span>
      </label>
    </Modal>
  )
}
