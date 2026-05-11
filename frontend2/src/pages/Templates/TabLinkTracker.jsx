import { useState, useEffect } from 'react'
import { Input, Textarea } from '../../components/Common/Input'
import { Button } from '../../components/Common/Button'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

export function TabLinkTracker({ info }) {
  const [t, setT] = useState({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setT({
      tracker_url: info.template?.tracker_url || '',
      tracker_slug: info.template?.tracker_slug || '',
      tracker_destino: info.template?.tracker_destino || '',
      comment_template: info.template?.comment_template || '',
    })
  }, [info])

  function set(k, v) { setT(prev => ({ ...prev, [k]: v })) }

  async function save() {
    if (!info.template_id) { toast.error('Sem template_id'); return }
    setSaving(true)
    try {
      const updated = { ...info.template, ...t }
      await api.put(`/api/templates/${info.template_id}`, updated)
      toast.success('Link Tracker salvo')
    } catch (e) { toast.error('Erro: ' + e.message) } finally { setSaving(false) }
  }

  if (!info.template) {
    return <div style={{ padding: 20, color: 'var(--text-sec)' }}>Sem template.</div>
  }

  return (
    <div>
      <div className="form-section">
        <div className="form-section-title">Link rastreável (innerlink.online)</div>
        <div className="form-grid">
          <Input label="Slug base" value={t.tracker_slug}
            onChange={(e) => set('tracker_slug', e.target.value)}
            hint="Ex: chosen-ones-blueprint, the-lyran-path" />
          <Input label="URL de destino (LP)" value={t.tracker_destino}
            onChange={(e) => set('tracker_destino', e.target.value)}
            hint="Ex: https://inner-light.flowlink.site/chosen-ones-blueprint" />
        </div>
      </div>

      <div className="form-section">
        <div className="form-section-title">Comentário fixado no YouTube</div>
        <Textarea
          label="Template do comentário (use {slug} pra inserir o slug do dia)"
          value={t.comment_template}
          onChange={(e) => set('comment_template', e.target.value)}
          rows={5}
          hint="Ex: 🎁 Acesse o material exclusivo: https://innerlink.online/{slug}"
        />
      </div>

      <div style={{ padding: 12, background: 'var(--info-soft)', border: '1px solid rgba(37, 99, 235, 0.25)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--text-xs)', color: 'var(--text-sec)' }}>
        💡 O slug diário (ex: <code>chosen-ones-blueprint-1605</code>) é gerado pelo procedimento
        diário no SQLite do link-tracker, conforme definido no Notion.
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 18 }}>
        <Button variant="primary" onClick={save} loading={saving}>Salvar Link Tracker</Button>
      </div>
    </div>
  )
}
