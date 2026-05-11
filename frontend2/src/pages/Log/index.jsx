import { useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Input, Select } from '../../components/Common/Input'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'
import './Log.css'

const MODO_ICON = {
  'prompt-mixer': '🎨',
  'agente':       '🤖',
  'imagem_fixa':  '🖼️',
  'cached':       '💾',
}

export function Log() {
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [filtroData, setFiltroData] = useState('')
  const [filtroCanal, setFiltroCanal] = useState('')
  const [errosOnly, setErrosOnly] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (filtroData) qs.set('data', filtroData)
      if (filtroCanal) qs.set('canal', filtroCanal)
      if (errosOnly) qs.set('erros_only', 'true')
      const d = await api.get('/api/video-log?' + qs)
      setVideos(d.videos || [])
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [filtroData, filtroCanal, errosOnly])

  return (
    <>
      <PageHeader
        title="Log"
        subtitle="Histórico de produção. Roteiros, narrações, renders e thumbnails por canal × data."
        actions={<Button variant="ghost" onClick={load}>↻ Atualizar</Button>}
      />

      <Card padding="md" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <Input label="Filtrar data (YYYY-MM-DD)" value={filtroData}
            onChange={(e) => setFiltroData(e.target.value)}
            placeholder="2026-05-12" style={{ flex: 1, minWidth: 180 }} />
          <Input label="Filtrar canal" value={filtroCanal}
            onChange={(e) => setFiltroCanal(e.target.value)}
            placeholder="EN, DE, ..." style={{ flex: 1, minWidth: 140 }} />
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 9, fontSize: 'var(--text-sm)', cursor: 'pointer' }}>
            <input type="checkbox" checked={errosOnly} onChange={(e) => setErrosOnly(e.target.checked)} />
            Só erros
          </label>
        </div>
      </Card>

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={20} /></div>
      )}

      {!loading && !videos.length && (
        <Card padding="lg">
          <div style={{ textAlign: 'center', color: 'var(--text-sec)' }}>
            Nenhum registro no log.
          </div>
        </Card>
      )}

      {!loading && videos.length > 0 && (
        <Card padding="none" style={{ overflowX: 'auto' }}>
          <table className="log-table">
            <thead>
              <tr>
                <th>Data</th>
                <th>Canal</th>
                <th>Status</th>
                <th>Roteiro</th>
                <th>Render</th>
                <th>Thumb</th>
                <th>Detalhes</th>
              </tr>
            </thead>
            <tbody>
              {videos.map((v, i) => <LogRow key={i} v={v} />)}
            </tbody>
          </table>
        </Card>
      )}
    </>
  )
}

function LogRow({ v }) {
  const overall = v.render_status === 'erro' || v.roteiro_status === 'erro' ? 'erro' :
                  v.render_status === 'ok' ? 'ok' : 'partial'

  return (
    <tr>
      <td><span style={{ fontFamily: 'var(--font-mono)' }}>{v.data || '—'}</span></td>
      <td><strong>{v.canal || v.tag}</strong></td>
      <td>
        <Badge variant={overall === 'erro' ? 'danger' : overall === 'ok' ? 'success' : 'warning'} dot size="sm">
          {overall === 'erro' ? 'erro' : overall === 'ok' ? 'OK' : 'parcial'}
        </Badge>
      </td>
      <td>
        {v.roteiro_chars ? <span style={{ fontFamily: 'var(--font-mono)' }}>{(v.roteiro_chars / 1000).toFixed(1)}k</span> : '—'}
      </td>
      <td>
        {v.render_path ? <span title={v.render_path} style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{v.render_path.split('/').pop()}</span> : '—'}
      </td>
      <td>
        {v.thumb_path ? (
          <span title={v.thumb_path} style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {MODO_ICON[v.thumb_modo] || '🖼️'} {v.thumb_modo || 'gerada'}
          </span>
        ) : '—'}
      </td>
      <td style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v.erro || ''}>
        {v.erro || ''}
      </td>
    </tr>
  )
}
