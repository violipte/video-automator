import { useState, useEffect } from 'react'
import { Input, Select } from '../../components/Common/Input'
import { Button } from '../../components/Common/Button'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

export function TabNarracao({ info }) {
  const [t, setT] = useState({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setT(info.template?.narracao_voz || {})
  }, [info])

  function set(k, v) { setT(prev => ({ ...prev, [k]: v })) }
  function setFb(k, v) { setT(prev => ({ ...prev, fallback: { ...(prev.fallback || {}), [k]: v } })) }

  async function save() {
    if (!info.template_id) { toast.error('Sem template_id'); return }
    setSaving(true)
    try {
      const updated = { ...info.template, narracao_voz: t }
      await api.put(`/api/templates/${info.template_id}`, updated)
      toast.success('Narração salva')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally { setSaving(false) }
  }

  if (!info.template) {
    return <div style={{ padding: 20, color: 'var(--text-sec)' }}>Sem template.</div>
  }

  const isChatterbox = t.provider === 'chatterbox'

  return (
    <div>
      <div className="form-section" style={{ borderLeft: '3px solid var(--accent)', paddingLeft: 12 }}>
        <div className="form-section-title">Voice principal</div>
        <div className="form-grid">
          <Select label="Provider" value={t.provider || ''}
            onChange={(e) => set('provider', e.target.value)}
            options={[
              { value: '', label: '—' },
              { value: 'chatterbox', label: '🆓 Chatterbox (local GPU, $0)' },
              { value: 'elevenlabs', label: 'ElevenLabs' },
              { value: 'elevenlabs_shared', label: 'ElevenLabs Shared' },
              { value: 'minimax', label: 'Minimax' },
              { value: 'minimax_clone', label: 'Minimax Clone' },
            ]} />

          {isChatterbox ? (
            <>
              <Select label="Modelo Chatterbox" value={t.chatterbox_model || 'base'}
                onChange={(e) => set('chatterbox_model', e.target.value)}
                options={[
                  { value: 'base', label: 'Base (multilingual, melhor qualidade)' },
                  { value: 'turbo', label: 'Turbo (so EN, ~2x mais rapido)' },
                ]} />
              <Input label="Voice Reference (path)" value={t.voice_ref || ''}
                onChange={(e) => set('voice_ref', e.target.value)}
                hint='Ex: F:/Canal Dark/CapCut/CapCut Materials/Vozes/Bill EN.MP3' />
              <Input label="Exaggeration" type="number" step="0.1" value={t.exaggeration ?? 0.5}
                onChange={(e) => set('exaggeration', parseFloat(e.target.value))}
                hint="0.0 monotone, 1.0 dramatico (so Base; Turbo ignora)" />
              <Input label="CFG Weight" type="number" step="0.1" value={t.cfg_weight ?? 0.5}
                onChange={(e) => set('cfg_weight', parseFloat(e.target.value))}
                hint="Classifier-free guidance (so Base; Turbo ignora)" />
            </>
          ) : (
            <>
              <Input label="Voice ID" value={t.voice_id || ''}
                onChange={(e) => set('voice_id', e.target.value)} />
              <Input label="Speed" type="number" step="0.05" value={t.speed || 1.0}
                onChange={(e) => set('speed', parseFloat(e.target.value))} />
              <Input label="Pitch" type="number" value={t.pitch || 0}
                onChange={(e) => set('pitch', parseInt(e.target.value))} />
            </>
          )}
        </div>
        {isChatterbox && (
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
            ℹ️ Chatterbox roda local na GPU. Sem API/sem custo.
            Voz clonada do arquivo de referencia (5-15s). Em caso de falha, cai pro Inworld.
            <br />
            <b>Base</b>: multilingual (EN/DE/PT/ES), maior qualidade. <b>Turbo</b>: so EN, ~2× mais rapido.
          </div>
        )}
      </div>

      <div className="form-section">
        <div className="form-section-title">Fallback Inworld (segunda chance)</div>
        <div className="form-grid">
          <Input label="Voice ID Inworld" value={t.fallback?.voice_id || ''}
            onChange={(e) => setFb('voice_id', e.target.value)}
            hint="Ex: default-xxx__bill" />
          <Select label="Provider fallback" value={t.fallback?.provider || ''}
            onChange={(e) => setFb('provider', e.target.value)}
            options={[
              { value: '', label: '—' },
              { value: 'inworld', label: 'Inworld TTS' },
            ]} />
          <Input label="Modelo Inworld" value={t.fallback?.model || ''}
            onChange={(e) => setFb('model', e.target.value)}
            hint="inworld-tts-1.5-max | inworld-tts-1.5-mini" />
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 18 }}>
        <Button variant="primary" onClick={save} loading={saving}>Salvar Narração</Button>
      </div>
    </div>
  )
}
