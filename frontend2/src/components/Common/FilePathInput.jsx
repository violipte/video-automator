import { useState } from 'react'
import { Input } from './Input'
import { Button } from './Button'
import { Modal } from './Modal'
import './FilePathInput.css'

/**
 * Input de path com botão "Visualizar" que abre modal.
 * Tipos: 'image' | 'video' | 'auto' (detecta pela extensão)
 */
export function FilePathInput({ label, value, onChange, hint, type = 'auto', actionLabel = 'Visualizar' }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="filepath-wrap">
      <Input label={label} value={value || ''} onChange={onChange} hint={hint} />
      {value && (
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)} style={{ marginTop: 6 }}>
          👁 {actionLabel}
        </Button>
      )}
      <PreviewModal open={open} onClose={() => setOpen(false)} path={value} type={type} title={label} />
    </div>
  )
}

function detectType(path) {
  if (!path) return 'unknown'
  const ext = path.toLowerCase().split('.').pop()
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(ext)) return 'image'
  if (['mp4', 'webm', 'mov', 'avi', 'mkv', 'm4v'].includes(ext)) return 'video'
  return 'unknown'
}

export function PreviewModal({ open, onClose, path, type = 'auto', title }) {
  const [erro, setErro] = useState(false)
  const detectedType = type === 'auto' ? detectType(path) : type
  const url = path ? `/api/preview/serve?path=${encodeURIComponent(path)}` : ''

  return (
    <Modal open={open} onClose={onClose} title={title || 'Visualizar'} size="lg">
      <div className="preview-wrap">
        <p className="preview-path">
          <code>{path || '—'}</code>
        </p>

        {!path && (
          <div className="preview-empty">Sem caminho configurado</div>
        )}

        {path && erro && (
          <div className="preview-error">
            ⚠ Não foi possível carregar o arquivo.
            <p style={{ marginTop: 8, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              Se o arquivo está no seu PC local (caminho <code>F:\</code>, <code>C:\</code>, etc) e o sistema
              está rodando em servidor remoto, o servidor não consegue acessá-lo. Faça upload do arquivo pra
              pasta do projeto ou use caminho relativo do servidor (ex: <code>/opt/video-automator/...</code>).
            </p>
          </div>
        )}

        {path && !erro && detectedType === 'image' && (
          <div className="preview-stage">
            <img
              src={url}
              alt={title}
              onError={() => setErro(true)}
              onLoad={() => setErro(false)}
              style={{ maxWidth: '100%', maxHeight: '60vh', borderRadius: 'var(--radius-sm)' }}
            />
          </div>
        )}

        {path && !erro && detectedType === 'video' && (
          <div className="preview-stage">
            <video
              src={url}
              controls
              autoPlay
              muted
              loop
              onError={() => setErro(true)}
              style={{ maxWidth: '100%', maxHeight: '60vh', borderRadius: 'var(--radius-sm)' }}
            />
          </div>
        )}

        {path && !erro && detectedType === 'unknown' && (
          <div className="preview-error">
            Extensão de arquivo não reconhecida pra preview visual.
          </div>
        )}
      </div>
    </Modal>
  )
}
