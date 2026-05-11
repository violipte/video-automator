import { useEffect, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card, CardHeader, CardBody } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Input } from '../../components/Common/Input'
import { Modal } from '../../components/Common/Modal'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'
import './Config.css'

export function Config() {
  const [config, setConfig] = useState(null)
  const [creds, setCreds] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingKey, setEditingKey] = useState(null)
  const [editingCred, setEditingCred] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const [c, cr] = await Promise.all([
        api.get('/api/config'),
        api.get('/api/credenciais'),
      ])
      setConfig(c)
      setCreds(cr.credenciais || cr || [])
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  async function saveKey(key, value) {
    try {
      await api.put('/api/config', { [key]: value })
      toast.success(`${key} salva`)
      load()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  async function saveCred(cred) {
    try {
      if (cred.id) {
        await api.put(`/api/credenciais/${cred.id}`, cred)
      } else {
        await api.post('/api/credenciais', cred)
      }
      toast.success('Credencial salva')
      setEditingCred(null)
      load()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  async function deleteCred(id) {
    if (!confirm('Remover credencial?')) return
    try {
      await api.delete(`/api/credenciais/${id}`)
      toast.success('Removida')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  async function testCred(id) {
    try {
      const r = await api.post(`/api/credenciais/${id}/refresh`)
      if (r.ok) toast.success(`OK · ${(r.modelos || []).length} modelos`)
      else toast.error(r.erro || 'Erro')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  if (loading || !config) {
    return (
      <>
        <PageHeader title="Config" />
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={20} /></div>
      </>
    )
  }

  // Apenas chaves relevantes (filtra Sync etc removidos)
  const KEYS = [
    { id: 'ai33_api_key', label: 'ai33.pro (TTS + Image Gen)' },
    { id: 'inworld_api_key', label: 'Inworld TTS (fallback)' },
    { id: 'render_worker_token', label: 'Render Worker token (Bearer)' },
    { id: 'tracker_url', label: 'Link Tracker URL', plain: true },
    { id: 'tracker_auth', label: 'Link Tracker auth' },
  ]

  // Agrupar credenciais por provedor
  const byProvider = creds.reduce((acc, c) => {
    const p = c.provedor || 'outros'
    if (!acc[p]) acc[p] = []
    acc[p].push(c)
    return acc
  }, {})

  return (
    <>
      <PageHeader
        title="Config"
        subtitle="API keys e credenciais. Sync de Supabase/Sheets foi removido (servidor já é fonte da verdade)."
        actions={<Button variant="ghost" onClick={load}>↻</Button>}
      />

      <Card padding="none" style={{ marginBottom: 20 }}>
        <CardHeader>API Keys</CardHeader>
        <div className="config-list">
          {KEYS.map(k => {
            const value = config[k.id]
            const masked = value && value.startsWith('...')
            return (
              <div key={k.id} className="config-row">
                <div className="config-row-label">{k.label}</div>
                <div className="config-row-value">
                  {value ? (
                    <code>{k.plain ? value : (masked ? value : `••• ${value.slice(-6)}`)}</code>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>não configurado</span>
                  )}
                </div>
                <Button size="sm" variant="ghost" onClick={() => setEditingKey(k)}>
                  {value ? 'editar' : 'configurar'}
                </Button>
              </div>
            )
          })}
        </div>
      </Card>

      <Card padding="none">
        <CardHeader action={<Button size="sm" variant="primary" onClick={() => setEditingCred({ provedor: 'gemini', nome: '', api_key: '' })}>+ Adicionar credencial</Button>}>
          Credenciais LLM ({creds.length})
        </CardHeader>
        <div className="config-list">
          {Object.entries(byProvider).map(([provider, list]) => (
            <div key={provider}>
              <div className="config-provider">
                <span className="config-provider-label">{provider}</span>
                <Badge variant="default" size="sm">{list.length}</Badge>
              </div>
              {list.map(c => (
                <div key={c.id} className="config-row config-cred">
                  <div className="config-row-label">{c.nome || c.id}</div>
                  <div className="config-row-value">
                    <code>••• {(c.api_key || '').slice(-6)}</code>
                    {' · '}
                    <Badge variant={c.status === 'ok' ? 'success' : 'danger'} size="sm">{c.status || '?'}</Badge>
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <Button size="sm" variant="ghost" onClick={() => testCred(c.id)}>🧪</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingCred(c)}>✏</Button>
                    <Button size="sm" variant="danger" onClick={() => deleteCred(c.id)}>×</Button>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>

      <KeyEditor
        editing={editingKey}
        currentValue={editingKey ? config[editingKey.id] : ''}
        onClose={() => setEditingKey(null)}
        onSave={(value) => { saveKey(editingKey.id, value); setEditingKey(null) }}
      />

      <CredEditor
        cred={editingCred}
        onClose={() => setEditingCred(null)}
        onSave={saveCred}
      />
    </>
  )
}

function KeyEditor({ editing, currentValue, onClose, onSave }) {
  const [v, setV] = useState('')
  useEffect(() => { setV(currentValue || '') }, [editing])
  if (!editing) return null
  return (
    <Modal open={!!editing} onClose={onClose} title={editing.label} size="sm"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button variant="primary" onClick={() => onSave(v)}>Salvar</Button>
      </>}>
      <Input
        label="Valor"
        value={v}
        onChange={(e) => setV(e.target.value)}
        type={editing.plain ? 'text' : 'password'}
        hint={editing.plain ? '' : 'Mantenha em segredo. Mascarado no display.'}
      />
    </Modal>
  )
}

function CredEditor({ cred, onClose, onSave }) {
  const [c, setC] = useState({})
  useEffect(() => { setC(cred || {}) }, [cred])
  if (!cred) return null
  return (
    <Modal open={!!cred} onClose={onClose} title={cred.id ? 'Editar credencial' : 'Nova credencial'} size="sm"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        <Button variant="primary" onClick={() => onSave(c)}>Salvar</Button>
      </>}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Input label="Nome (label)" value={c.nome || ''}
          onChange={(e) => setC({ ...c, nome: e.target.value })}
          placeholder="Ex: Claude Main, Gemini key 3" />
        <Input label="Provedor" value={c.provedor || ''}
          onChange={(e) => setC({ ...c, provedor: e.target.value })}
          placeholder="claude | gpt | gemini | claude_cli" />
        <Input label="API key" type="password" value={c.api_key || ''}
          onChange={(e) => setC({ ...c, api_key: e.target.value })} />
      </div>
    </Modal>
  )
}
