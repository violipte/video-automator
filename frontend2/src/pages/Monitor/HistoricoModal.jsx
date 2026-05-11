import { useEffect, useMemo, useState } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'
import { Spinner } from '../../components/Common/Spinner'
import { Badge } from '../../components/Common/Badge'
import { api } from '../../api/client'

function fmtDur(s) {
  if (s == null || s < 0) return '—'
  s = Math.round(s)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

function statusBadge(status) {
  if (status === 'ok') return <Badge variant="success">✓ ok</Badge>
  if (status === 'erro') return <Badge variant="danger">erro</Badge>
  if (status === 'rodando') return <Badge variant="info" dot>rodando</Badge>
  if (status === 'pendente') return <Badge variant="default">—</Badge>
  return <Badge variant="default">{status || '—'}</Badge>
}

export function HistoricoModal({ open, onClose }) {
  const [datas, setDatas] = useState([])
  const [dataSel, setDataSel] = useState('')
  const [canais, setCanais] = useState([])
  const [carregando, setCarregando] = useState(false)

  // Carrega lista de datas disponíveis
  useEffect(() => {
    if (!open) return
    api.get('/api/production-log/historico').then(r => {
      const ds = r?.datas || []
      setDatas(ds)
      if (ds.length && !dataSel) setDataSel(ds[0])
    }).catch(() => {})
  }, [open])

  // Carrega canais quando muda dataSel
  useEffect(() => {
    if (!open || !dataSel) return
    setCarregando(true)
    api.get(`/api/production-log/historico?data=${encodeURIComponent(dataSel)}`)
      .then(r => setCanais(r?.canais || []))
      .catch(() => setCanais([]))
      .finally(() => setCarregando(false))
  }, [open, dataSel])

  const totais = useMemo(() => {
    if (!canais.length) return null
    const sumTotal = canais.reduce((s, c) => s + (c.total_s || 0), 0)
    const okRender = canais.filter(c => c.render?.status === 'ok').length
    const fbNarr = canais.filter(c => c.narracao?.fallback).length
    const fbRot  = canais.filter(c => c.roteiro?.fallback).length
    return { sumTotal, okRender, fbNarr, fbRot, total: canais.length }
  }, [canais])

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="📊 Histórico de produção"
      size="xl"
      footer={<Button variant="ghost" onClick={onClose}>Fechar</Button>}
    >
      {/* Selector de data */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <label style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>Data:</label>
        <select
          value={dataSel}
          onChange={(e) => setDataSel(e.target.value)}
          style={{
            background: 'var(--input-bg)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)', padding: '6px 10px',
            color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
          }}
        >
          {datas.length === 0 && <option value="">(sem datas)</option>}
          {datas.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        {totais && (
          <div style={{ marginLeft: 'auto', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            {totais.okRender}/{totais.total} renders OK · {totais.fbNarr} narr fallback · {totais.fbRot} roteiro fallback · total {fmtDur(totais.sumTotal)}
          </div>
        )}
      </div>

      {carregando && <div style={{ textAlign: 'center', padding: 30 }}><Spinner size={20} /></div>}

      {!carregando && canais.length > 0 && (
        <div className="historico-table-wrap">
          <table className="historico-table">
            <thead>
              <tr>
                <th>Canal</th>
                <th>Roteiro</th>
                <th>Narração</th>
                <th>Render</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {canais.map((c, i) => (
                <tr key={i}>
                  <td className="historico-canal">{c.canal}</td>
                  <td>
                    <div className="historico-cell">
                      {statusBadge(c.roteiro?.status)}
                      <div className="historico-meta">
                        <code>{c.roteiro?.provider || '—'}</code>
                        {c.roteiro?.fallback && <span className="historico-fb-tag">fallback</span>}
                        <span className="historico-time">{fmtDur(c.roteiro?.duracao_s)}</span>
                      </div>
                      {c.roteiro?.chars && <div className="historico-info">{c.roteiro.chars.toLocaleString()} chars</div>}
                    </div>
                  </td>
                  <td>
                    <div className="historico-cell">
                      {statusBadge(c.narracao?.status)}
                      <div className="historico-meta">
                        <code>{c.narracao?.provider || '—'}</code>
                        {c.narracao?.fallback && <span className="historico-fb-tag">fallback Inworld</span>}
                        <span className="historico-time">{fmtDur(c.narracao?.duracao_s)}</span>
                      </div>
                      {c.narracao?.chunks > 0 && <div className="historico-info">{c.narracao.chunks} chunks</div>}
                    </div>
                  </td>
                  <td>
                    <div className="historico-cell">
                      {statusBadge(c.render?.status)}
                      <div className="historico-meta">
                        <span className="historico-time">{fmtDur(c.render?.duracao_s)}</span>
                      </div>
                      {c.render?.tamanho_mb > 0 && <div className="historico-info">{c.render.tamanho_mb.toFixed(0)}MB</div>}
                    </div>
                  </td>
                  <td className="historico-total">{fmtDur(c.total_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!carregando && canais.length === 0 && dataSel && (
        <div style={{ textAlign: 'center', padding: 30, color: 'var(--text-muted)' }}>
          Sem dados pra essa data.
        </div>
      )}
    </Modal>
  )
}
