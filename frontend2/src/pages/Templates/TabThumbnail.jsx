import { useEffect, useState } from 'react'
import { Input, Select, Textarea } from '../../components/Common/Input'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

export function TabThumbnail({ info }) {
  const canal = info.canal
  const isCO = /^CO\d/.test(canal)
  const [data, setData] = useState(null)
  const [tmpl, setTmpl] = useState(null)
  const [coCfg, setCoCfg] = useState(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  async function load() {
    try {
      const d = await api.get('/api/thumb/templates')
      setData(d)
      const t = (d.templates || []).find(x => x.canal === canal)
      setTmpl(t || null)
      if (isCO) {
        const cfg = d.co_config?.canais?.[canal] || {}
        setCoCfg({ ...d.co_config?.default, ...cfg })
      }
    } catch (e) { toast.error('Erro: ' + e.message) }
  }

  useEffect(() => { load() }, [canal])

  async function saveTemplate() {
    if (!tmpl) return
    setSaving(true)
    try {
      const r = await api.put(`/api/thumb/template/${canal}`, tmpl)
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast.success('Template thumbnail salvo')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setSaving(false) }
  }

  async function saveCoConfig() {
    setSaving(true)
    try {
      const r = await api.put(`/api/thumb/co-config/${canal}`, coCfg)
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast.success('Config CO* salva')
      load()
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setSaving(false) }
  }

  async function testar() {
    setTesting(true)
    try {
      const r = await api.post('/api/thumb/regenerar', {
        canal,
        tema: 'Teste de tema fictício',
        titulo: 'STARSEED, THIS IS A PREVIEW THUMBNAIL TEST',
        thumb: 'PREVIEW TEST IMAGE',
      })
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast.success(`Thumb gerada (${r.modo}) → ${r.path}`)
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setTesting(false) }
  }

  if (!data) return <div style={{ padding: 20 }}>Carregando…</div>

  if (isCO) {
    return (
      <div>
        <div className="form-section">
          <div className="form-section-title">Modo: imagem fixa (PIL overlay, sem ai33)</div>
          <div className="form-grid">
            <Input label="Imagem base (path)" value={coCfg?.imagem_base || ''}
              onChange={(e) => setCoCfg({ ...coCfg, imagem_base: e.target.value })}
              hint="PNG/JPG 1280x720 (será resizado se diferente)" />
            <Input label="Fonte (.ttf path)" value={coCfg?.fonte || ''}
              onChange={(e) => setCoCfg({ ...coCfg, fonte: e.target.value })}
              hint="Ex: fonts/Anton-Regular.ttf" />
            <Input label="Tamanho fonte (px)" type="number" value={coCfg?.tamanho_fonte || 110}
              onChange={(e) => setCoCfg({ ...coCfg, tamanho_fonte: parseInt(e.target.value) })} />
            <Input label="Cor texto (hex)" value={coCfg?.cor_texto || '#FFFFFF'}
              onChange={(e) => setCoCfg({ ...coCfg, cor_texto: e.target.value })} />
            <Input label="Outline width" type="number" value={coCfg?.outline_width || 6}
              onChange={(e) => setCoCfg({ ...coCfg, outline_width: parseInt(e.target.value) })} />
            <Input label="Outline color" value={coCfg?.outline_color || '#000000'}
              onChange={(e) => setCoCfg({ ...coCfg, outline_color: e.target.value })} />
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 18 }}>
          <Button variant="ghost" onClick={testar} loading={testing}>🧪 Testar</Button>
          <Button variant="primary" onClick={saveCoConfig} loading={saving}>Salvar</Button>
        </div>
      </div>
    )
  }

  if (!tmpl) {
    return (
      <div style={{ padding: 20, color: 'var(--text-sec)' }}>
        Canal <strong>{canal}</strong> não tem template thumbnail configurado em <code>thumb_templates.json</code>.
      </div>
    )
  }

  const isAgente = tmpl.modo === 'agente'

  return (
    <div>
      <div className="form-section">
        <div className="form-section-title">Modo: {tmpl.modo}</div>
        <div className="form-grid">
          <Select label="Modo" value={tmpl.modo || 'prompt-mixer'}
            onChange={(e) => setTmpl({ ...tmpl, modo: e.target.value })}
            options={[
              { value: 'prompt-mixer', label: '🎨 prompt-mixer (pools + ai33)' },
              { value: 'agente', label: '🤖 agente (Claude CLI runtime)' },
            ]} />
          <Input label="Modelo ai33" value={tmpl.model_id || ''}
            onChange={(e) => setTmpl({ ...tmpl, model_id: e.target.value })} />
        </div>
      </div>

      {!isAgente && (
        <>
          <div className="form-section">
            <Textarea label="Prompt base (use [CENA] [CHARACTER] [TEXTO DE CIMA] [TEXTO DE BAIXO])"
              value={tmpl.prompt_base || ''}
              onChange={(e) => setTmpl({ ...tmpl, prompt_base: e.target.value })}
              rows={6} />
          </div>
          <div className="form-section">
            <Textarea label={`Pool CENA (${(tmpl.pools?.cena || []).length} itens, 1 por linha)`}
              value={(tmpl.pools?.cena || []).join('\n')}
              onChange={(e) => setTmpl({ ...tmpl, pools: { ...tmpl.pools, cena: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
              rows={6} />
          </div>
          <div className="form-section">
            <Textarea label={`Pool CHARACTER (${(tmpl.pools?.character || []).length} itens, 1 por linha)`}
              value={(tmpl.pools?.character || []).join('\n')}
              onChange={(e) => setTmpl({ ...tmpl, pools: { ...tmpl.pools, character: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
              rows={5} />
          </div>
        </>
      )}

      {isAgente && (
        <div className="form-section">
          <p style={{ color: 'var(--text-sec)', fontSize: 'var(--text-sm)' }}>
            Modo agente: prompt é gerado em runtime pelo Claude. Edite as instruções em
            <code style={{ marginLeft: 4 }}>agents/thumbnail-{canal.toLowerCase()}/CLAUDE.md</code>
          </p>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 18 }}>
        <Button variant="ghost" onClick={testar} loading={testing}>🧪 Testar</Button>
        <Button variant="primary" onClick={saveTemplate} loading={saving}>Salvar</Button>
      </div>
    </div>
  )
}
