import { useEffect, useState, useMemo } from 'react'
import './LegendaPreview.css'

const PHRASES = [
  'The universe is sending you a powerful message today',
  'Chosen one, your transformation begins now',
  'STARSEEDS, THE TIME HAS COME',
  'God says: do not abandon hope',
  'A new chapter is opening in your life',
]

/**
 * Preview visual da legenda com fonte/tamanho/cor/outline aplicados.
 * config = template.legenda_config
 */
export function LegendaPreview({ config = {}, fundo = 'escuro' }) {
  const [phrase, setPhrase] = useState(PHRASES[0])

  // Carrega a fonte custom via @font-face dinâmico
  const fontName = useMemo(() => {
    const path = config.fonte || ''
    if (!path) return null
    const file = path.split(/[\\/]/).pop() || ''
    return file.replace(/\.(ttf|otf)$/i, '')
  }, [config.fonte])

  useEffect(() => {
    if (!fontName) return
    const fontFile = (config.fonte || '').split(/[\\/]/).pop()
    if (!fontFile) return
    const styleId = `font-${fontName.replace(/[^a-zA-Z0-9]/g, '_')}`
    let el = document.getElementById(styleId)
    if (!el) {
      el = document.createElement('style')
      el.id = styleId
      document.head.appendChild(el)
    }
    el.textContent = `
      @font-face {
        font-family: '${fontName}';
        src: url('/api/preview/font?name=${encodeURIComponent(fontFile)}') format('truetype');
        font-display: swap;
      }
    `
    return () => {
      // não remove pra cache (fonte fica disponível)
    }
  }, [fontName, config.fonte])

  // Aplica MAIÚSCULAS se config marca
  const text = config.maiuscula ? phrase.toUpperCase() : phrase

  // Quebra em linhas baseado em max_chars (~visual)
  const maxChars = parseInt(config.max_chars) || 30
  const linhas = quebrarLinhas(text, maxChars)
  const maxLinhas = parseInt(config.max_linhas) || 2
  const finalLines = linhas.slice(0, maxLinhas)

  // Estilo do texto
  const tamanho = parseInt(config.tamanho) || 36
  const cor = config.cor_primaria || '#FFFFFF'
  const outline = parseInt(config.outline ?? 3)
  const outlineCor = config.cor_outline || '#000000'
  const shadow = parseInt(config.shadow ?? 1)

  // text-shadow simulando outline (8 direções) + sombra
  const outlineShadow = outline > 0 ? `
    -${outline}px -${outline}px 0 ${outlineCor},
    ${outline}px -${outline}px 0 ${outlineCor},
    -${outline}px ${outline}px 0 ${outlineCor},
    ${outline}px ${outline}px 0 ${outlineCor},
    0 -${outline}px 0 ${outlineCor},
    0 ${outline}px 0 ${outlineCor},
    -${outline}px 0 0 ${outlineCor},
    ${outline}px 0 0 ${outlineCor}
  `.trim() : ''
  const dropShadow = shadow > 0 ? `${shadow}px ${shadow}px ${shadow * 2}px rgba(0,0,0,0.6)` : ''
  const textShadow = [outlineShadow, dropShadow].filter(Boolean).join(', ')

  // Posição vertical
  const posicao = config.posicao || 'bottom'
  const justifyClass = posicao === 'top' ? 'pos-top' : posicao === 'center' ? 'pos-center' : 'pos-bottom'

  // Background do preview (escuro/claro alternativo pra ver o outline)
  const bgClass = fundo === 'claro' ? 'preview-fundo-claro' : 'preview-fundo-escuro'

  function nextPhrase() {
    setPhrase(PHRASES[(PHRASES.indexOf(phrase) + 1) % PHRASES.length])
  }

  return (
    <div className="legenda-preview">
      <div className="legenda-preview-head">
        <span className="legenda-preview-label">Preview da Legenda</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="case-btn" onClick={nextPhrase} title="Próxima frase">↻ Frase</button>
        </div>
      </div>

      <div className={`legenda-preview-stage ${bgClass} ${justifyClass}`}>
        <div className="legenda-preview-text" style={{
          fontFamily: fontName ? `'${fontName}', var(--font-sans)` : 'var(--font-sans)',
          fontSize: Math.min(tamanho, 56), // cap pra não estourar o preview
          color: cor,
          textShadow,
          fontWeight: 700,
          lineHeight: 1.15,
        }}>
          {finalLines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      </div>

      <div className="legenda-preview-meta">
        <span>fonte: <strong>{fontName || '(default)'}</strong></span>
        <span>tamanho: <strong>{tamanho}px</strong></span>
        <span>outline: <strong>{outline}px</strong></span>
        <span>shadow: <strong>{shadow}px</strong></span>
        <span>linhas: <strong>{finalLines.length}/{maxLinhas}</strong></span>
        <span>chars: <strong>{maxChars}/linha</strong></span>
      </div>
    </div>
  )
}

function quebrarLinhas(text, maxChars) {
  if (!text) return []
  const words = text.split(/\s+/)
  const linhas = []
  let cur = ''
  for (const w of words) {
    if ((cur + ' ' + w).trim().length <= maxChars) {
      cur = (cur + ' ' + w).trim()
    } else {
      if (cur) linhas.push(cur)
      cur = w
    }
  }
  if (cur) linhas.push(cur)
  return linhas
}
