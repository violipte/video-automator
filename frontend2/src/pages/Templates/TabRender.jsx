import { useState, useEffect } from 'react'
import { Input, Select, Textarea } from '../../components/Common/Input'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { FilePathInput } from '../../components/Common/FilePathInput'
import { LegendaPreview } from '../../components/Common/LegendaPreview'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

export function TabsRender({ info }) {
  const [t, setT] = useState(info.template || {})
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState({ identif: true })

  useEffect(() => { setT(info.template || {}) }, [info])

  function set(k, v) { setT(prev => ({ ...prev, [k]: v })) }
  function setNested(path, v) {
    setT(prev => {
      const novo = { ...prev }
      const keys = path.split('.')
      let cur = novo
      for (let i = 0; i < keys.length - 1; i++) {
        cur[keys[i]] = { ...(cur[keys[i]] || {}) }
        cur = cur[keys[i]]
      }
      cur[keys[keys.length - 1]] = v
      return novo
    })
  }

  async function save() {
    if (!info.template_id) { toast.error('Sem template_id'); return }
    setSaving(true)
    try {
      await api.put(`/api/templates/${info.template_id}`, t)
      toast.success('Template salvo')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally { setSaving(false) }
  }

  if (!info.template) {
    return <div style={{ padding: 20, color: 'var(--text-sec)' }}>
      Canal não tem template associado em <code>temas.json</code>.
    </div>
  }

  const toggle = (k) => setOpen(prev => ({ ...prev, [k]: !prev[k] }))

  return (
    <div className="render-form">

      <Section title="Identificação" open={open.identif} onToggle={() => toggle('identif')}>
        <div className="form-grid">
          <Input label="Nome" value={t.nome || ''} onChange={(e) => set('nome', e.target.value)} />
          <Input label="Tag" value={t.tag || ''} onChange={(e) => set('tag', e.target.value)} hint="Ex: EN, DE, CO1" />
          <Select label="Idioma" value={t.idioma || 'en'}
            onChange={(e) => set('idioma', e.target.value)}
            options={[
              { value: 'en', label: 'English' },
              { value: 'de', label: 'Deutsch' },
              { value: 'pt', label: 'Português' },
              { value: 'es', label: 'Español' },
            ]} />
        </div>
      </Section>

      <Section title="Resolução & Output" open={open.resol} onToggle={() => toggle('resol')}>
        <div className="form-grid">
          <Input label="Largura" type="number" value={t.resolucao?.[0] || 1920}
            onChange={(e) => set('resolucao', [parseInt(e.target.value), t.resolucao?.[1] || 1080])} />
          <Input label="Altura" type="number" value={t.resolucao?.[1] || 1080}
            onChange={(e) => set('resolucao', [t.resolucao?.[0] || 1920, parseInt(e.target.value)])} />
          <Input label="FPS" type="number" value={t.fps || 30}
            onChange={(e) => set('fps', parseInt(e.target.value))} />
          <Input label="Pasta de saída" value={t.pasta_saida || ''}
            onChange={(e) => set('pasta_saida', e.target.value)} />
        </div>
      </Section>

      <Section title="Fundo" open={open.fundo} onToggle={() => toggle('fundo')}>
        <div className="form-grid">
          <Select label="Tipo fundo" value={t.tipo_fundo || 'imagens'}
            onChange={(e) => set('tipo_fundo', e.target.value)}
            options={[
              { value: 'imagens', label: 'Imagens (Ken Burns)' },
              { value: 'videos', label: 'Vídeos' },
            ]} />
          <Input label="Pasta" value={t.pasta_imagens || ''}
            onChange={(e) => set('pasta_imagens', e.target.value)} />
          <Input label="Duração por imagem (s)" type="number" value={t.duracao_por_imagem || ''}
            onChange={(e) => set('duracao_por_imagem', parseFloat(e.target.value))}
            hint="Vazio = escalonado" />
          <Input label="Zoom ratio" type="number" step="0.05" value={t.zoom_ratio || 1.3}
            onChange={(e) => set('zoom_ratio', parseFloat(e.target.value))} />
          <label className="checkbox-row">
            <input type="checkbox" checked={!!t.video_loop} onChange={(e) => set('video_loop', e.target.checked)} />
            <span>Video loop (repetir até preencher narração)</span>
          </label>
        </div>
      </Section>

      <Section title="Lumetri (ajustes visuais)" open={open.lumetri} onToggle={() => toggle('lumetri')}>
        <div className="form-grid">
          <Input label="Brilho" type="number" step="0.05" value={t.brilho ?? 0}
            onChange={(e) => set('brilho', parseFloat(e.target.value))} />
          <Input label="Contraste" type="number" step="0.05" value={t.contraste ?? 1}
            onChange={(e) => set('contraste', parseFloat(e.target.value))} />
          <Input label="Saturação" type="number" step="0.05" value={t.saturacao ?? 1}
            onChange={(e) => set('saturacao', parseFloat(e.target.value))} />
          <Input label="Gamma (exposição)" type="number" step="0.05" value={t.gamma ?? 1}
            onChange={(e) => set('gamma', parseFloat(e.target.value))} />
          <Input label="Realces (highlights)" type="number" step="0.05" value={t.realces ?? 0}
            onChange={(e) => set('realces', parseFloat(e.target.value))} />
          <Input label="Sombras" type="number" step="0.05" value={t.sombras ?? 0}
            onChange={(e) => set('sombras', parseFloat(e.target.value))} />
          <Input label="Brancos" type="number" step="0.05" value={t.brancos ?? 0}
            onChange={(e) => set('brancos', parseFloat(e.target.value))} />
          <Input label="Pretos" type="number" step="0.05" value={t.pretos ?? 0}
            onChange={(e) => set('pretos', parseFloat(e.target.value))} />
          <Input label="Temperatura" type="number" step="1" value={t.temperatura ?? 0}
            onChange={(e) => set('temperatura', parseFloat(e.target.value))} />
          <Input label="Tonalidade (tint)" type="number" step="1" value={t.tonalidade ?? 0}
            onChange={(e) => set('tonalidade', parseFloat(e.target.value))} />
          <Input label="Vinheta" type="number" step="0.05" value={t.vinheta ?? 0}
            onChange={(e) => set('vinheta', parseFloat(e.target.value))}
            hint="0-1, 0 = desligado" />
        </div>
      </Section>

      <Section title="Randomização de ajustes" open={open.rand} onToggle={() => toggle('rand')}>
        <div className="form-grid">
          <Select label="Modo" value={t.aleatorizacao || 'none'}
            onChange={(e) => set('aleatorizacao', e.target.value)}
            options={[
              { value: 'none', label: 'Desligado' },
              { value: 'subtle', label: 'Sutil (variações pequenas)' },
              { value: 'medium', label: 'Médio (mais variação)' },
            ]} />
          <Input label="Seed (opcional)" value={t.aleat_seed || ''}
            onChange={(e) => set('aleat_seed', e.target.value)}
            hint="Deixe vazio pra random a cada render" />
        </div>
      </Section>

      <Section title="Moldura (overlay permanente)" open={open.moldura} onToggle={() => toggle('moldura')}>
        <div className="form-grid">
          <FilePathInput
            label="Arquivo (PNG)"
            value={t.moldura?.arquivo || ''}
            onChange={(e) => setNested('moldura.arquivo', e.target.value)}
            type="image"
            actionLabel="Visualizar Moldura"
          />
          <Select label="Tipo" value={t.moldura?.tipo || 'alpha'}
            onChange={(e) => setNested('moldura.tipo', e.target.value)}
            options={[
              { value: 'alpha', label: 'Alpha (PNG transparente)' },
              { value: 'chromakey', label: 'Chromakey verde' },
            ]} />
          <Input label="Opacidade" type="number" step="0.05" value={t.moldura?.opacidade ?? 1.0}
            onChange={(e) => setNested('moldura.opacidade', parseFloat(e.target.value))} />
        </div>
      </Section>

      <Section title="Overlays adicionais" open={open.overlays} onToggle={() => toggle('overlays')}>
        <OverlaysEditor
          overlays={t.overlays || []}
          onChange={(novo) => set('overlays', novo)}
        />
      </Section>

      <Section title="CTA (Call To Action verde periódico)" open={open.cta} onToggle={() => toggle('cta')}>
        <div className="form-grid">
          <FilePathInput
            label="Arquivo CTA (greenscreen .mp4)"
            value={t.cta?.arquivo || ''}
            onChange={(e) => setNested('cta.arquivo', e.target.value)}
            type="video"
            actionLabel="Visualizar CTA"
          />
          <Select label="Posição" value={t.cta?.posicao || 'bottom-right'}
            onChange={(e) => setNested('cta.posicao', e.target.value)}
            options={[
              { value: 'bottom-right', label: 'Inferior Direita' },
              { value: 'bottom-center', label: 'Inferior Centro' },
              { value: 'bottom-left', label: 'Inferior Esquerda' },
              { value: 'top-right', label: 'Superior Direita' },
              { value: 'top-center', label: 'Superior Centro' },
              { value: 'top-left', label: 'Superior Esquerda' },
              { value: 'center', label: 'Centro' },
            ]} />
          <Input label="Escala" type="number" step="0.05" value={t.cta?.escala ?? 0.3}
            onChange={(e) => setNested('cta.escala', parseFloat(e.target.value))} />
          <Input label="Início (s)" type="number" value={t.cta?.inicio ?? 30}
            onChange={(e) => setNested('cta.inicio', parseInt(e.target.value))} />
          <Input label="Duração (s)" type="number" value={t.cta?.duracao ?? 8}
            onChange={(e) => setNested('cta.duracao', parseInt(e.target.value))} />
          <Input label="Intervalo entre (s)" type="number" value={t.cta?.intervalo ?? 300}
            onChange={(e) => setNested('cta.intervalo', parseInt(e.target.value))}
            hint="300 = a cada 5 min, 600 = a cada 10 min" />
        </div>
      </Section>

      <Section title="Legenda" open={open.legenda} onToggle={() => toggle('legenda')}>
        <div className="legenda-grid">
          <div className="legenda-fields">
            <div className="form-grid">
              <FilePathInput
                label="Fonte (.ttf path)"
                value={t.legenda_config?.fonte || ''}
                onChange={(e) => setNested('legenda_config.fonte', e.target.value)}
                type="auto"
                actionLabel="Abrir fonte"
              />
              <Input label="Tamanho" type="number" value={t.legenda_config?.tamanho ?? 36}
                onChange={(e) => setNested('legenda_config.tamanho', parseInt(e.target.value))} />
              <Input label="Cor primária (hex)" value={t.legenda_config?.cor_primaria || '#FFFFFF'}
                onChange={(e) => setNested('legenda_config.cor_primaria', e.target.value)} />
              <Input label="Cor outline (hex)" value={t.legenda_config?.cor_outline || '#000000'}
                onChange={(e) => setNested('legenda_config.cor_outline', e.target.value)} />
              <Input label="Outline width" type="number" value={t.legenda_config?.outline ?? 3}
                onChange={(e) => setNested('legenda_config.outline', parseInt(e.target.value))} />
              <Input label="Shadow" type="number" value={t.legenda_config?.shadow ?? 1}
                onChange={(e) => setNested('legenda_config.shadow', parseInt(e.target.value))} />
              <Select label="Posição" value={t.legenda_config?.posicao || 'bottom'}
                onChange={(e) => setNested('legenda_config.posicao', e.target.value)}
                options={[
                  { value: 'bottom', label: 'Bottom' },
                  { value: 'top', label: 'Top' },
                  { value: 'center', label: 'Center' },
                  { value: 'custom', label: 'Custom (X%, Y%)' },
                ]} />
              <Input label="Margin Y (px)" type="number" value={t.legenda_config?.marginV ?? 50}
                onChange={(e) => setNested('legenda_config.marginV', parseInt(e.target.value))} />
              <Input label="Max chars/linha" type="number" value={t.legenda_config?.max_chars ?? 30}
                onChange={(e) => setNested('legenda_config.max_chars', parseInt(e.target.value))} />
              <Input label="Max linhas" type="number" value={t.legenda_config?.max_linhas ?? 2}
                onChange={(e) => setNested('legenda_config.max_linhas', parseInt(e.target.value))} />
              <label className="checkbox-row">
                <input type="checkbox" checked={!!t.legenda_config?.maiuscula}
                  onChange={(e) => setNested('legenda_config.maiuscula', e.target.checked)} />
                <span>Tudo MAIÚSCULAS</span>
              </label>
            </div>
          </div>

          <div className="legenda-preview-col">
            <LegendaPreview config={t.legenda_config || {}} fundo="escuro" />
            <LegendaPreview config={t.legenda_config || {}} fundo="claro" />
          </div>
        </div>
      </Section>

      <Section title="Regras de legenda (correções/substituições)" open={open.regras} onToggle={() => toggle('regras')}>
        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
          Regras específicas deste template (sobrescrevem rules/{`{idioma}`}.json). JSON com chaves: hesitations, custom_words, substitutions, etc.
        </p>
        <Textarea
          label="JSON de regras (template)"
          value={JSON.stringify(t.regras || {}, null, 2)}
          rows={10}
          onChange={(e) => {
            try { set('regras', JSON.parse(e.target.value)) } catch { /* ignore */ }
          }}
          hint='Ex: {"hesitations":["uh","um"], "substitutions":{"oldword":"newword"}}'
        />
      </Section>

      <Section title="Roteiro (mín chars + tolerância fallback)" open={open.roteiro} onToggle={() => toggle('roteiro')}>
        <div className="form-grid">
          <Input label="Min chars do roteiro" type="number" value={t.min_roteiro_chars || 22000}
            onChange={(e) => set('min_roteiro_chars', parseInt(e.target.value))} />
          <Input label="Tolerância fallback %" type="number" step="0.05" value={t.tolerancia_fallback_pct ?? 0.80}
            onChange={(e) => set('tolerancia_fallback_pct', parseFloat(e.target.value))}
            hint="0.80 = aceita 80% do min" />
        </div>
      </Section>

      <Section title="Upload (proxy para YouTube — futuro)" open={open.upload} onToggle={() => toggle('upload')}>
        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
          Configuração de proxy SOCKS5/HTTP por canal pra evitar correlação de IP no upload (feature em backlog).
        </p>
        <div className="form-grid">
          <Select label="Tipo" value={t.upload?.proxy_tipo || ''}
            onChange={(e) => setNested('upload.proxy_tipo', e.target.value)}
            options={[
              { value: '', label: '— sem proxy —' },
              { value: 'socks5', label: 'SOCKS5' },
              { value: 'http', label: 'HTTP' },
              { value: 'https', label: 'HTTPS' },
            ]} />
          <Input label="Host" value={t.upload?.proxy_host || ''}
            onChange={(e) => setNested('upload.proxy_host', e.target.value)} />
          <Input label="Porta" type="number" value={t.upload?.proxy_port || ''}
            onChange={(e) => setNested('upload.proxy_port', parseInt(e.target.value))} />
          <Input label="Usuário" value={t.upload?.proxy_user || ''}
            onChange={(e) => setNested('upload.proxy_user', e.target.value)} />
          <Input label="Senha" type="password" value={t.upload?.proxy_pass || ''}
            onChange={(e) => setNested('upload.proxy_pass', e.target.value)} />
          <Input label="Categoria YT (number)" type="number" value={t.upload?.youtube_category || ''}
            onChange={(e) => setNested('upload.youtube_category', parseInt(e.target.value))}
            hint="Ex: 22 = People & Blogs" />
        </div>
      </Section>

      <div className="form-footer">
        <Button variant="primary" onClick={save} loading={saving}>Salvar Render</Button>
      </div>
    </div>
  )
}

function Section({ title, children, open, onToggle }) {
  return (
    <div className="form-section render-section">
      <button type="button" className="render-section-head" onClick={onToggle}>
        <span style={{ fontSize: 12, marginRight: 8, transition: 'transform 0.15s', display: 'inline-block', transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}>▶</span>
        {title}
      </button>
      {open && <div className="render-section-body">{children}</div>}
    </div>
  )
}

function OverlaysEditor({ overlays, onChange }) {
  function update(idx, key, value) {
    const novo = [...overlays]
    novo[idx] = { ...novo[idx], [key]: value }
    onChange(novo)
  }

  function remove(idx) {
    if (!confirm('Remover este overlay?')) return
    onChange(overlays.filter((_, i) => i !== idx))
  }

  function add() {
    onChange([...overlays, { arquivo: '', opacidade: 0.4 }])
  }

  return (
    <div>
      {overlays.length === 0 && (
        <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)', marginBottom: 10 }}>
          Sem overlays. Click em "+ Adicionar Overlay" pra começar.
        </p>
      )}

      {overlays.map((ov, i) => (
        <div key={i} style={{ marginBottom: 12, padding: 12, background: 'var(--panel-2)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <strong style={{ fontSize: 'var(--text-sm)', color: 'var(--text)' }}>Overlay {i + 1}</strong>
            <Button size="sm" variant="danger" onClick={() => remove(i)}>×</Button>
          </div>
          <div className="form-grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
            <FilePathInput
              label="Arquivo"
              value={ov.arquivo || ''}
              onChange={(e) => update(i, 'arquivo', e.target.value)}
              type="video"
              actionLabel="Visualizar Overlay"
            />
            <Input label="Opacidade" type="number" step="0.05"
              value={ov.opacidade ?? 0.4}
              onChange={(e) => update(i, 'opacidade', parseFloat(e.target.value))} />
          </div>
        </div>
      ))}

      <Button variant="ghost" onClick={add}>+ Adicionar Overlay</Button>
    </div>
  )
}
