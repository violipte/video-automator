import { useState, useEffect } from 'react'
import { Input, Select, Textarea } from '../../components/Common/Input'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Modal } from '../../components/Common/Modal'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

const TIPOS = [
  { value: 'llm', label: 'LLM (chamada API)' },
  { value: 'texto', label: 'Texto (substituição)' },
  { value: 'code', label: 'Code (Python exec)' },
]

export function TabRoteiro({ info }) {
  const [pipelines, setPipelines] = useState([])
  const [creds, setCreds] = useState([])
  const [pipelineId, setPipelineId] = useState(info.pipeline_id || '')
  const [pipeline, setPipeline] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testModal, setTestModal] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const [pi, cr] = await Promise.all([
        api.get('/api/pipelines'),
        api.get('/api/credenciais'),
      ])
      setPipelines(pi.pipelines || pi || [])
      setCreds(cr.credenciais || cr || [])
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])
  useEffect(() => { setPipelineId(info.pipeline_id || '') }, [info])

  useEffect(() => {
    if (!pipelineId) { setPipeline(null); return }
    const p = pipelines.find(x => x.id === pipelineId)
    setPipeline(p ? JSON.parse(JSON.stringify(p)) : null)
  }, [pipelineId, pipelines])

  async function savePipeline() {
    if (!pipeline) return
    setSaving(true)
    try {
      await api.put(`/api/pipelines/${pipeline.id}`, pipeline)
      toast.success('Pipeline salva')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setSaving(false) }
  }

  async function novaPipeline() {
    const nome = prompt('Nome da nova pipeline:')
    if (!nome) return
    try {
      const r = await api.post('/api/pipelines', {
        nome,
        etapas: [],
      })
      toast.success('Pipeline criada')
      const id = r.id || r.pipeline?.id
      await load()
      if (id) setPipelineId(id)
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  async function deletePipeline() {
    if (!pipeline) return
    if (!confirm(`Deletar pipeline "${pipeline.nome}"?\n\nIsso é irreversível.`)) return
    try {
      await api.delete(`/api/pipelines/${pipeline.id}`)
      toast.success('Pipeline removida')
      setPipelineId('')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  function addEtapa() {
    setPipeline(p => ({ ...p, etapas: [...(p.etapas || []), { nome: 'Nova etapa', tipo: 'llm', system_message: '', prompt: '' }] }))
  }

  function updateEtapa(i, key, value) {
    setPipeline(p => {
      const etapas = [...(p.etapas || [])]
      etapas[i] = { ...etapas[i], [key]: value }
      return { ...p, etapas }
    })
  }

  function removeEtapa(i) {
    if (!confirm('Remover essa etapa?')) return
    setPipeline(p => ({ ...p, etapas: p.etapas.filter((_, idx) => idx !== i) }))
  }

  function moveEtapa(i, dir) {
    setPipeline(p => {
      const etapas = [...(p.etapas || [])]
      const j = i + dir
      if (j < 0 || j >= etapas.length) return p
      ;[etapas[i], etapas[j]] = [etapas[j], etapas[i]]
      return { ...p, etapas }
    })
  }

  if (loading) return <div style={{ padding: 20, textAlign: 'center' }}><Spinner size={20} /></div>

  return (
    <div>
      <div className="form-section">
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 14 }}>
          <Select
            label="Pipeline associada"
            value={pipelineId}
            onChange={(e) => setPipelineId(e.target.value)}
            options={[
              { value: '', label: '— sem pipeline —' },
              ...pipelines.map(p => ({ value: p.id, label: `${p.nome || p.id} (${(p.etapas || []).length} etapas)` })),
            ]}
            style={{ flex: 1 }}
          />
          <Button variant="ghost" size="sm" onClick={novaPipeline}>+ Nova</Button>
          <Button variant="ghost" size="sm" onClick={load}>↻</Button>
        </div>

        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          Para mudar a pipeline associada ao canal, edite <code>temas.json</code> na coluna <strong>{info.canal}</strong>.
          Aqui você edita o conteúdo da pipeline em si.
        </p>
      </div>

      {pipeline && (
        <>
          <div className="form-section">
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <Input
                label="Nome da pipeline"
                value={pipeline.nome || ''}
                onChange={(e) => setPipeline(p => ({ ...p, nome: e.target.value }))}
                style={{ flex: 1 }}
              />
              <Button variant="danger" size="sm" onClick={deletePipeline}>Deletar pipeline</Button>
            </div>
          </div>

          <div className="form-section">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <div className="form-section-title" style={{ marginBottom: 0 }}>Etapas ({(pipeline.etapas || []).length})</div>
              <Button size="sm" variant="primary" onClick={addEtapa}>+ Adicionar etapa</Button>
            </div>

            {(!pipeline.etapas || !pipeline.etapas.length) && (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)', border: '1px dashed var(--border)', borderRadius: 'var(--radius-sm)' }}>
                Nenhuma etapa. Click em "+ Adicionar etapa".
              </div>
            )}

            {(pipeline.etapas || []).map((et, i) => (
              <EtapaEditor
                key={i}
                etapa={et}
                index={i}
                total={pipeline.etapas.length}
                creds={creds}
                onChange={(k, v) => updateEtapa(i, k, v)}
                onRemove={() => removeEtapa(i)}
                onMove={(dir) => moveEtapa(i, dir)}
                onTest={() => setTestModal({ pipeline_id: pipeline.id, etapa_idx: i })}
              />
            ))}
          </div>

          <div className="form-footer">
            <Button variant="primary" onClick={savePipeline} loading={saving}>Salvar pipeline</Button>
          </div>
        </>
      )}

      <TestModal
        info={testModal}
        onClose={() => setTestModal(null)}
      />
    </div>
  )
}

function EtapaEditor({ etapa, index, total, creds, onChange, onRemove, onMove, onTest }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="etapa-card">
      <div className="etapa-card-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button className="etapa-action" onClick={() => setOpen(!open)} style={{ background: 'transparent', border: 'none' }}>
            {open ? '▼' : '▶'}
          </button>
          <strong>{index + 1}. {etapa.nome || 'sem nome'}</strong>
          <Badge variant={etapa.tipo === 'llm' ? 'info' : etapa.tipo === 'code' ? 'warning' : 'default'} size="sm">
            {etapa.tipo || 'llm'}
          </Badge>
        </div>
        <div className="etapa-actions">
          <button className="etapa-action" onClick={() => onMove(-1)} disabled={index === 0} title="Subir">↑</button>
          <button className="etapa-action" onClick={() => onMove(1)} disabled={index === total - 1} title="Descer">↓</button>
          <button className="etapa-action" onClick={onTest} title="Testar etapa">🧪</button>
          <button className="etapa-action danger" onClick={onRemove} title="Remover">×</button>
        </div>
      </div>

      {open && (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="form-grid">
            <Input
              label="Nome"
              value={etapa.nome || ''}
              onChange={(e) => onChange('nome', e.target.value)}
            />
            <Select
              label="Tipo"
              value={etapa.tipo || 'llm'}
              onChange={(e) => onChange('tipo', e.target.value)}
              options={TIPOS}
            />
          </div>

          {etapa.tipo === 'llm' && (
            <div className="form-grid">
              <Select
                label="Credencial"
                value={etapa.credencial || ''}
                onChange={(e) => onChange('credencial', e.target.value)}
                options={[
                  { value: '', label: '—' },
                  ...creds.map(c => ({ value: c.id, label: `${c.nome || c.id} (${c.provedor})` })),
                ]}
              />
              <Input
                label="Modelo"
                value={etapa.modelo || ''}
                onChange={(e) => onChange('modelo', e.target.value)}
                placeholder="claude-sonnet-4-6 | gpt-5.2 | gemini-2.5-flash"
              />
            </div>
          )}

          {etapa.tipo === 'llm' && (
            <Textarea
              label="System message"
              value={etapa.system_message || ''}
              onChange={(e) => onChange('system_message', e.target.value)}
              rows={6}
              hint="Instruções fixas pra LLM (use {{tema}} {{canal}} {{saida_anterior}} pra interpolar)"
            />
          )}

          <Textarea
            label={etapa.tipo === 'code' ? 'Código Python (set resultado=...)' : etapa.tipo === 'texto' ? 'Texto (template c/ vars)' : 'Prompt do usuário'}
            value={etapa.prompt || ''}
            onChange={(e) => onChange('prompt', e.target.value)}
            rows={6}
            style={{ fontFamily: etapa.tipo === 'code' ? 'var(--font-mono)' : 'inherit' }}
          />
        </div>
      )}
    </div>
  )
}

function TestModal({ info, onClose }) {
  const [entrada, setEntrada] = useState('')
  const [resultado, setResultado] = useState('')
  const [loading, setLoading] = useState(false)
  const [modo, setModo] = useState('etapa')  // etapa | cadeia

  if (!info) return null

  async function rodar() {
    setLoading(true); setResultado('')
    try {
      const url = modo === 'cadeia' ? '/api/pipelines/testar-cadeia' : '/api/pipelines/testar-etapa'
      const r = await api.post(url, {
        pipeline_id: info.pipeline_id,
        etapa_idx: info.etapa_idx,
        entrada,
      })
      setResultado(typeof r === 'string' ? r : JSON.stringify(r, null, 2))
    } catch (e) {
      setResultado('Erro: ' + e.message)
    } finally { setLoading(false) }
  }

  return (
    <Modal
      open={!!info}
      onClose={onClose}
      title={`Testar etapa ${info.etapa_idx + 1}`}
      size="lg"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Fechar</Button>
        <Button variant="primary" onClick={rodar} loading={loading}>Executar</Button>
      </>}
    >
      <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
        <Select
          label="Modo"
          value={modo}
          onChange={(e) => setModo(e.target.value)}
          options={[
            { value: 'etapa', label: 'Apenas esta etapa' },
            { value: 'cadeia', label: 'Cadeia até esta etapa' },
          ]}
        />
      </div>
      <Textarea
        label="Entrada (tema)"
        value={entrada}
        onChange={(e) => setEntrada(e.target.value)}
        rows={3}
        placeholder="Ex: chosen ones rest message"
      />
      {resultado && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-sec)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 'var(--track-wide)' }}>Resultado</div>
          <pre style={{ background: 'var(--panel-2)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 12, fontSize: 12, fontFamily: 'var(--font-mono)', overflowX: 'auto', maxHeight: 400, overflowY: 'auto', whiteSpace: 'pre-wrap', color: 'var(--text)' }}>
            {resultado}
          </pre>
        </div>
      )}
    </Modal>
  )
}
