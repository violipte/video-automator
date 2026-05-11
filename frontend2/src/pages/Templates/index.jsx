import { useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Modal } from '../../components/Common/Modal'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'
import { TabsRender } from './TabRender'
import { TabRoteiro } from './TabRoteiro'
import { TabNarracao } from './TabNarracao'
import { TabThumbnail } from './TabThumbnail'
import { TabLinkTracker } from './TabLinkTracker'
import './Templates.css'

const TABS = [
  { id: 'render',     label: 'Render',       comp: TabsRender },
  { id: 'roteiro',    label: 'Roteiro',      comp: TabRoteiro },
  { id: 'narracao',   label: 'Narração',     comp: TabNarracao },
  { id: 'thumbnail',  label: 'Thumbnail',    comp: TabThumbnail },
  { id: 'link',       label: 'Link Tracker', comp: TabLinkTracker },
]

export function Templates() {
  const [templates, setTemplates] = useState([])
  const [temas, setTemas] = useState(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const [ts, t] = await Promise.all([
        api.get('/api/templates'),
        api.get('/api/temas'),
      ])
      setTemplates(ts.templates || ts || [])
      setTemas(t)
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  if (loading) {
    return (
      <>
        <PageHeader title="Templates" />
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={24} /></div>
      </>
    )
  }

  // Cruza colunas do grid Temas com templates pra mostrar todos canais
  const colunas = (temas?.colunas || []).filter(c => c.tipo !== 'coringa')
  const cards = colunas.map(col => {
    const tmpl = templates.find(t => t.id === col.template_id || t.tag === col.nome)
    return { canal: col.nome, template: tmpl, template_id: col.template_id, pipeline_id: col.pipeline_id, voice_id: col.voice_id }
  })

  return (
    <>
      <PageHeader
        title="Templates"
        subtitle="Configuração completa por canal: render, roteiro, narração, thumbnail, link tracker."
        actions={<Button variant="ghost" onClick={load}>↻ Atualizar</Button>}
      />

      <div className="templates-grid">
        {cards.map(c => (
          <ChannelCard key={c.canal} info={c} onClick={() => setEditing(c)} />
        ))}
        {!cards.length && (
          <Card padding="lg" style={{ gridColumn: '1 / -1' }}>
            <div style={{ textAlign: 'center', color: 'var(--text-sec)' }}>
              Nenhum canal cadastrado em <code>temas.json</code>
            </div>
          </Card>
        )}
      </div>

      {editing && (
        <ChannelEditor
          info={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { load(); }}
        />
      )}
    </>
  )
}

function ChannelCard({ info, onClick }) {
  const { canal, template, pipeline_id, voice_id } = info
  const status = !template ? 'erro' : (pipeline_id && voice_id) ? 'ok' : 'parcial'
  const variantMap = { ok: 'success', parcial: 'warning', erro: 'danger' }
  const labelMap = { ok: 'configurado', parcial: 'incompleto', erro: 'sem template' }

  return (
    <Card padding="md" interactive onClick={onClick} className="channel-card">
      <div className="channel-card-thumb">
        <span className="channel-card-tag">{canal}</span>
      </div>
      <div className="channel-card-body">
        <div className="channel-card-header">
          <strong>{canal}</strong>
          <Badge variant={variantMap[status]} dot size="sm">{labelMap[status]}</Badge>
        </div>
        <div className="channel-card-meta">
          {template?.nome || 'sem template'} · {template?.idioma || '?'}
        </div>
      </div>
    </Card>
  )
}

function ChannelEditor({ info, onClose, onSaved }) {
  const [tab, setTab] = useState('render')
  const Comp = TABS.find(t => t.id === tab)?.comp

  return (
    <Modal
      open={!!info}
      onClose={onClose}
      title={`${info.canal} — Configuração`}
      size="xl"
      footer={<Button variant="secondary" onClick={onClose}>Fechar</Button>}
    >
      <div className="modal-tabs">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`modal-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >{t.label}</button>
        ))}
      </div>
      <div className="modal-tab-content">
        {Comp && <Comp info={info} onSaved={onSaved} />}
      </div>
    </Modal>
  )
}
