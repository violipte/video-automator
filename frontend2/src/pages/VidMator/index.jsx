import { useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card } from '../../components/Common/Card'
import './VidMator.css'

/* ============================================================
   VidMator — ACERVO DE EDIÇÃO (estilo CapCut) + Formatos
   Fase 1: catálogo por categoria (preview em GIF entra na Fase 2).
   O pool já existe no Director (Remotion/BrollTest) — aqui é a vitrine.
   ============================================================ */

const TABS = [
  { id: 'catalogo',    icon: '📚', label: 'Catálogo' },
  { id: 'imagens',     icon: '🖼️', label: 'Imagens' },
  { id: 'videos',      icon: '🎞️', label: 'Vídeos' },
  { id: 'personagens', icon: '🧍', label: 'Personagens' },
  { id: 'modelos',     icon: '🎨', label: 'Modelos' },
  { id: 'transicoes',  icon: '🔀', label: 'Transições' },
  { id: 'overlays',    icon: '✨', label: 'Overlays' },
  { id: 'sfx',         icon: '🔊', label: 'SFX' },
  { id: 'formatos',    icon: '📦', label: 'Formatos' },
]

/* ---- ACERVO: cada opção = um "container" de edição plugável ---- */
const ACERVO = {
  imagens: [
    ['Ken Burns', 'zoom/pan lento — padrão de produção', '🖼️'],
    ['Lupa / Glass Inspect', 'documento, rosto, mapa, pista', '🔍'],
    ['Spotlight Focus', 'dirigir o olhar, suspense', '💡'],
    ['Split / Two-Image', 'comparação, antes/depois', '⟷'],
    ['Photo Grid / Collage', 'coleção, montagem, "vários"', '🔳'],
    ['Polaroid / Moldura', 'histórico, evidência, memória', '🖼'],
    ['Film / VHS Frame', 'época, found-footage', '📽️'],
    ['Parallax', 'profundidade 3D numa foto', '🌀'],
    ['Layered Reveal', 'revelação dramática (cinza→cor)', '🎭'],
  ],
  videos: [
    ['Loop mudo (OffthreadVideo)', 'b-roll de movimento — áudio sempre 0%', '🎞️'],
    ['Archive Clip', 'footage de arquivo/época com fade', '📼'],
    ['EditMask (Standard License)', 'moldura + grão + scanline + crop de marca-d’água', '🩹'],
    ['Ken Burns em vídeo pausado', 'still de um frame de vídeo', '⏸️'],
  ],
  personagens: [
    ['Person Card', 'card histórico: foto recortada + nome', '🧑'],
    ['Mascote (Galo)', 'entra a cada 2-3 cenas, poses/lados alternados', '🐓'],
  ],
  modelos: [
    ['— a definir —', 'estilo 2D (palitinho / raccoon), personagem de canal, etc. Vamos desenhar juntos.', '🎨'],
  ],
  transicoes: [
    ['Crossfade', 'padrão — dissolve suave', '🔀'],
    ['Slide Horizontal', 'entra deslizando (L/R)', '➡️'],
    ['Whip Pan', 'chicote rápido (com whoosh)', '💨'],
    ['Smooth Zoom', 'zoom de aproximação', '🔎'],
  ],
  overlays: [
    ['Red Wash (tenso)', 'lavagem vermelha de tensão', '🟥'],
    ['Cold Blue (frio)', 'azul frio / distante', '🟦'],
    ['CRT / VHS', 'linhas de tubo + varredura', '📺'],
    ['Glitch Flash', 'estouro digital pontual', '⚡'],
    ['TV Static', 'chiado de fronteira de tópico', '🌫️'],
    ['Light Leak / Rays', 'vazamento de luz / raios', '🌅'],
    ['Aurora / Particles / Stars', 'ambiente cósmico/etéreo', '🌌'],
    ['Period (vintage)', 'P&B + grão + vinheta de época', '🎞️'],
  ],
  sfx: [
    ['Whoosh', 'transição de movimento (baixo)', '💨'],
    ['Riser', 'build-up até a fronteira de tópico', '📈'],
    ['Glitch / Static', 'só em fronteira de tópico', '⚡'],
    ['Typing (ASMR)', 'digitação/typewriter, vol baixo', '⌨️'],
    ['Paper', 'virar página (cards/imagens)', '📄'],
    ['Click', 'clique de câmera na troca', '🖱️'],
    ['CTA Ding', 'sino do CTA', '🔔'],
  ],
}

export function VidMator() {
  const [tab, setTab] = useState('catalogo')
  return (
    <>
      <PageHeader
        title="VidMator"
        subtitle="Acervo de edição por formato — os containers que compõem cada estilo"
      />
      <div className="vm-tabs">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`vm-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            <span>{t.icon}</span>{t.label}
          </button>
        ))}
      </div>
      {tab === 'catalogo' ? <SecCatalogo />
        : tab === 'formatos' ? <SecFormatos />
        : <Categoria id={tab} />}
    </>
  )
}

function Categoria({ id }) {
  const itens = ACERVO[id] || []
  const meta = TABS.find(t => t.id === id)
  return (
    <div className="vm-section">
      <p className="vm-lead">
        {LEADS[id]}
      </p>
      <div className="vm-grid">
        {itens.map((o, i) => (
          <div key={i} className="vm-opt">
            <div className="vm-opt-preview"><span>{o[2]}</span><em>preview em breve</em></div>
            <div className="vm-opt-body">
              <strong>{o[0]}</strong>
              <span>{o[1]}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="vm-note">
        Fase 2 = renderizar o GIF de cada opção (as 8 de Imagens saem direto do <code>PresentationGallery.tsx</code>).
        Fase 3 = cada opção vira container com <strong>probabilidade</strong> por formato.
      </div>
    </div>
  )
}

const LEADS = {
  imagens: 'Como uma FOTO/imagem aparece na tela. O Director escolhe por contexto (default = Ken Burns).',
  videos: 'Como o B-ROLL de vídeo é tratado. Clipe de YouTube (Standard License) SEMPRE com áudio 0% + EditMask.',
  personagens: 'Figuras que entram sobre a cena — card histórico e mascote do canal.',
  modelos: 'Estilos de personagem/animação por formato (2D, palitinho, etc.). Categoria a desenhar.',
  transicoes: 'Como uma cena vira a próxima.',
  overlays: 'Camadas atmosféricas por cima da cena (mood, luz, época, glitch).',
  sfx: 'Efeitos sonoros — tempero em volume BAIXO, nunca estourado.',
}

/* ---------- Formatos & Regras (consolida a padronização) ---------- */
const TIPOS = [
  ['A', 'a', 'Produto / Veículo', 'Moto, carro, tech, review. Mostrar O PRODUTO ESPECÍFICO em todas as formas — nada de stock genérico nem palavra literal.'],
  ['B', 'b', 'Documentário / Mistério', 'Dark, true crime, história, ciência, geopolítica, guerra. Atmosfera semântica + mídia de entidade.'],
  ['C', 'c', 'Reflexivo', 'Estoicismo, filosofia. Atmosfera (estátuas, ruínas) + quotes. Sem "produto".'],
]
const PRESETS = [
  ['estoicismo', '0.18', 'false', 'serif'],
  ['documentario', '0.35', 'false', 'serif'],
  ['true_crime', '0.55', 'herda (true)', 'typewriter'],
  ['ttm', '0.16', 'false', 'clean'],
  ['survival', '0.22', 'false', 'impact'],
  ['motos', '0.12', 'false', 'impact'],
]
function SecFormatos() {
  return (
    <div className="vm-section">
      <p className="vm-lead">
        Um <strong>formato</strong> = composição probabilística de opções do acervo, plugável por canal/nicho.
        Ex.: <em>documentário</em> serve doc + curiosidades; <em>top-rank</em> serve vários nichos.
        Por ora, as regras/presets que já padronizamos:
      </p>
      <h3 className="vm-h3">Tipologia (estratégia de footage)</h3>
      <div className="vm-typo">
        {TIPOS.map(t => (
          <div key={t[0]} className="vm-typo-card">
            <h4><span className={`vm-tipo vm-tipo-${t[1]}`}>Tipo {t[0]}</span> {t[2]}</h4>
            <p>{t[3]}</p>
          </div>
        ))}
      </div>
      <h3 className="vm-h3">Presets por nicho (do <code>presets.json</code>)</h3>
      <div className="vm-table-wrap">
        <table className="vm-table">
          <thead><tr><th>Nicho</th><th>Efeito</th><th>glitch_topico</th><th>Fonte</th></tr></thead>
          <tbody>
            {PRESETS.map((r, i) => (
              <tr key={i}>
                <td><strong>{r[0]}</strong></td>
                <td>{r[1]}</td>
                <td><span className={`vm-chip ${r[2].startsWith('false') ? 'off' : 'on'}`}>{r[2]}</span></td>
                <td>{r[3]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="vm-note">
        Regras completas em <code>REGRAS_NICHOS.md</code>. Próximo passo do MOTOR: definir o schema de formato
        (probabilidades por container) e ligar no template do canal.
      </div>
    </div>
  )
}

/* ============================================================
   CATÁLOGO — as 54 animações construídas (Remotion/compositions)
   Cada uma: tipo (overlay / imagem / gráfico / mapa) + variáveis editáveis.
   Fonte: decupagem da VidRush → VIDMATOR_ACERVO.md §4.6 + Lotes 1/2.
   ============================================================ */
const TIPOMETA = {
  overlay: { label: 'Overlay',  cls: 'ov', desc: 'Transparente — entra POR CIMA do footage atual (lower-third, callout, número). Não traz imagem própria; a b-roll continua rodando por trás.' },
  imagem:  { label: 'Imagem',   cls: 'im', desc: 'Cena de imagem — VOCÊ fornece a(s) foto(s). A animação É o quadro daquele beat (substitui a b-roll).' },
  grafico: { label: 'Gráfico',  cls: 'gr', desc: 'Self-contained — desenha tudo em fundo próprio (escuro). Sem imagem externa; só dados/texto viram variável.' },
  mapa:    { label: 'Mapa',     cls: 'ma', desc: 'Geografia real (d3-geo / satélite ESRI). Países e coordenadas (lat/long) são as variáveis.' },
}

// [ nome exibido, id do componente, categoria, tipo, [variáveis...], nota? ]
const ANIMS = [
  // ---- Gráficos & Dados ----
  ['Percentage Bar Chart', 'PercentageBarChart', 'Gráficos & Dados', 'grafico', ['Title Text', 'Bottom Text', 'Percentage Value']],
  ['Pie Chart', 'PieChart', 'Gráficos & Dados', 'grafico', ['Title', 'Nº de Fatias', 'Distribuição Igual?', 'Fatia Destacada', 'Valor Fatia 1–5', 'Label do Destaque']],
  ['Line Chart', 'LineChart', 'Gráficos & Dados', 'grafico', ['Chart Title', 'Nº de Linhas', 'Data Points', 'Start Value', 'End Value', 'Eixo X (tipo)', 'Eixo Y (tipo)', 'Padrão da Tendência']],
  ['Growing Bar Chart', 'GrowingBarChart', 'Gráficos & Dados', 'grafico', ['Title', 'Ano da Barra Final', 'Texto da Barra Final']],
  ['Bar Chart Comparison', 'BarChartComparison', 'Gráficos & Dados', 'grafico', ['Chart Title', 'Label Esq.', 'Valor Esq.', 'Label Dir.', 'Valor Dir.', 'Logo (opcional)']],
  ['Circle Percent', 'CirclePercent', 'Gráficos & Dados', 'grafico', ['Title Content', 'Circle Percent']],
  ['Number Count', 'NumberCountOverlay', 'Gráficos & Dados', 'overlay', ['Label', 'Value (conta até)', 'Prefixo', 'Sufixo']],
  ['Stock Chart', 'StockChart', 'Gráficos & Dados', 'grafico', ['Title', 'Ticker', 'Tendência (alta/baixa)']],

  // ---- Stats & Callouts ----
  ['Price Call Out', 'PriceCallOut', 'Stats & Callouts', 'overlay', ['Price Amount', 'Moeda', 'Descriptor Text']],
  ['Object Dual Stat', 'ObjectDualStat', 'Stats & Callouts', 'imagem', ['Object Image', 'Nº Grande Esq.', 'Label Esq.', 'Nº Grande Dir.', 'Label Dir.']],
  ['Poll / Survey Bar', 'PollSurveyBar', 'Stats & Callouts', 'grafico', ['Question', 'Palavra Destacada', 'Label Primário', 'Label Secundário', '% Primário', 'Source Text']],
  ['One Word Callout', 'OneWordCallout', 'Stats & Callouts', 'overlay', ['Word']],
  ['Icon Grid', 'IconGrid', 'Stats & Callouts', 'grafico', ['Main Text', 'Ícone Topo', 'Ícone Direita', 'Ícone Baixo', 'Ícone Esquerda']],
  ['Icon Labels', 'IconLabels', 'Stats & Callouts', 'overlay', ['Ícones []', 'Labels []']],
  ['Circle Highlight', 'CircleHighlight', 'Stats & Callouts', 'overlay', ['Image', 'Label'], 'Overlay que ANOTA uma imagem que você fornece (desenha o círculo em cima).'],
  ['Bullet Points', 'BulletPointOverlay', 'Stats & Callouts', 'overlay', ['Bullets [] (lista)']],

  // ---- Mapas ----
  ['Multi Country Outline', 'MultiCountryOutline', 'Mapas', 'mapa', ['Países [] (nomes)', 'Valores [] (por país)']],
  ['Satellite Draw Path', 'SatelliteDrawPath', 'Mapas', 'mapa', ['Location Label', 'Coordenada Central (lat/long)']],
  ['Map Route', 'MapRoute', 'Mapas', 'mapa', ['Nome Origem', 'Coord. Origem', 'Nome Destino', 'Coord. Destino']],
  ['Satellite Location Pin', 'SatelliteLocationPin', 'Mapas', 'mapa', ['Latitude', 'Longitude', 'Location Name', 'Location Sub Title']],
  ['Region Location Text', 'RegionLocationText', 'Mapas', 'mapa', ['Country Name', 'Region Name', 'Text']],
  ['Country + Character Map', 'CountryCharacterMap', 'Mapas', 'mapa', ['Country Name', 'Nome', 'Título', 'Character Image'], 'Mapa real + slot de foto do personagem sobreposto.'],

  // ---- Texto ----
  ['Sentence Highlight', 'SentenceHighlight', 'Texto', 'grafico', ['Parágrafos []', 'Palavras Destacadas []']],
  ['Text Reveal', 'TextReveal', 'Texto', 'grafico', ['Main Text', 'Secondary Text', 'Final Label']],
  ['Title + Description', 'TitleDescription', 'Texto', 'grafico', ['Title', 'Description']],
  ['Quote Card', 'QuoteCard', 'Texto', 'grafico', ['Quote Text', 'Autor (nome)', 'Autor (título)']],
  ['Chapter Title', 'ChapterTitle', 'Texto', 'grafico', ['Title', 'Chapter Number', 'Subtitle']],
  ['Display Text', 'DisplayText', 'Texto', 'overlay', ['Text']],
  ['Date / Location Overlay', 'DateLocationOverlay', 'Texto', 'overlay', ['Text (data / local)']],
  ['Caption Overlay', 'CaptionTextOverlay', 'Texto', 'overlay', ['Caption']],
  ['Dual Impact Sentence', 'DualImpactSentence', 'Texto', 'grafico', ['First Sentence', 'Second Sentence']],
  ['Single Sentence Slide', 'SingleSentenceTextSlide', 'Texto', 'overlay', ['Sentence']],

  // ---- Pessoas & Objetos ----
  ['Character Card', 'CharacterCard', 'Pessoas & Objetos', 'imagem', ['Character Image', 'Title', 'Subtitle']],
  ['Character + Keyword', 'CharacterKeyword', 'Pessoas & Objetos', 'imagem', ['Character Image', 'Keyword']],
  ['Object + Title', 'ObjectTitle', 'Pessoas & Objetos', 'imagem', ['Object Image', 'Title']],
  ['Node Hierarchy', 'NodeHierarchy', 'Pessoas & Objetos', 'grafico', ['Top Node', 'Bottom Nodes []']],
  ['Subject Title Card', 'SubjectTitleCard', 'Pessoas & Objetos', 'grafico', ['First Title', 'Second Title', 'SubTitle']],
  ['Detective Board', 'DetectiveBoard', 'Pessoas & Objetos', 'imagem', ['Left Image', 'Right Image', 'Títulos']],
  ['Instagram Conversation', 'InstagramConversation', 'Pessoas & Objetos', 'grafico', ['Messages [] (balões)']],

  // ---- Imagens & Comparação ----
  ['Two Image Comparison', 'TwoImageComparison', 'Imagens & Comparação', 'imagem', ['Title Text', 'Left Image', 'Right Image']],
  ['Three Image Reveal', 'ThreeImageReveal', 'Imagens & Comparação', 'imagem', ['Imagens [3]']],
  ['Four Image Slideshow', 'FourImageSlideshow', 'Imagens & Comparação', 'imagem', ['Imagens [4]']],
  ['Multi Image Cut Text', 'MultiImageCutText', 'Imagens & Comparação', 'imagem', ['Items [] (imagem + título)']],
  ['Dual Image on Grid', 'DualImageOnGrid', 'Imagens & Comparação', 'imagem', ['Left Image', 'Left Label', 'Right Image', 'Right Label']],
  ['Split Screen Comparison', 'SplitScreenComparison', 'Imagens & Comparação', 'imagem', ['Left Image', 'Right Image']],
  ['Four Image Caption Grid', 'FourImageCaptionGrid', 'Imagens & Comparação', 'imagem', ['Imagens [4]', 'Captions [4]', 'Mostrar Texto?']],
  ['Five Text Listicle', 'FiveTextListicle', 'Imagens & Comparação', 'imagem', ['Items [5] (imagem + texto)']],
  ['Before / After Arrow', 'BeforeAfterArrow', 'Imagens & Comparação', 'imagem', ['Before Image', 'After Image']],
  ['Image Text Annotation', 'ImageTextAnnotation', 'Imagens & Comparação', 'overlay', ['Image', 'Labels [] (texto + x/y)'], 'Overlay que ANOTA a imagem fornecida com marcações posicionadas.'],
  ['Website Screenshot Reveal', 'WebsiteScreenshotReveal', 'Imagens & Comparação', 'imagem', ['URL', 'Screenshot']],
  ['Article / News Card', 'ArticleNewsCard', 'Imagens & Comparação', 'imagem', ['Article Image', 'Article Text', 'Highlight Text', 'Image Caption']],
  ['Logo / Flag Grid', 'LogoFlagGrid', 'Imagens & Comparação', 'grafico', ['Items [] (logos / bandeiras)']],
  ['Image Callout', 'ImageCallout', 'Imagens & Comparação', 'overlay', ['Image', 'Callout Text', 'Spot X', 'Spot Y'], 'Overlay que aponta um ponto (x/y) sobre a imagem fornecida.'],
  ['Paper Moving Transparent Object', 'PaperMovingTransparentObject', 'Imagens & Comparação', 'imagem', ['Object Image (recorte PNG)']],
]

const CAT_ORDER = ['Gráficos & Dados', 'Stats & Callouts', 'Mapas', 'Texto', 'Pessoas & Objetos', 'Imagens & Comparação']
const FILTROS = [
  ['todos', 'Todos'],
  ['overlay', 'Overlay'],
  ['imagem', 'Imagem'],
  ['grafico', 'Gráfico'],
  ['mapa', 'Mapa'],
]

function SecCatalogo() {
  const [q, setQ] = useState('')
  const [ftipo, setFtipo] = useState('todos')

  const termo = q.trim().toLowerCase()
  const filtradas = ANIMS.filter(a => {
    if (ftipo !== 'todos' && a[3] !== ftipo) return false
    if (!termo) return true
    const alvo = (a[0] + ' ' + a[1] + ' ' + a[2] + ' ' + a[4].join(' ')).toLowerCase()
    return alvo.includes(termo)
  })

  const contagem = FILTROS.reduce((acc, [k]) => {
    acc[k] = k === 'todos' ? ANIMS.length : ANIMS.filter(a => a[3] === k).length
    return acc
  }, {})

  const porCat = CAT_ORDER
    .map(cat => [cat, filtradas.filter(a => a[2] === cat)])
    .filter(([, itens]) => itens.length)

  return (
    <div className="vm-section">
      <p className="vm-lead">
        <strong>{ANIMS.length} animações</strong> já construídas (Remotion/<code>compositions</code>). Cada linha traz as
        <strong> variáveis editáveis</strong> e o <strong>tipo</strong> — que decide se ela entra <em>por cima</em> do
        footage (overlay) ou <em>vira a cena</em> (imagem/gráfico/mapa). É esse mapa que o Director usa pra escolher container por contexto.
      </p>

      <div className="vm-legend">
        {Object.entries(TIPOMETA).map(([k, m]) => (
          <div key={k} className="vm-legend-item">
            <span className={`vm-tp vm-tp-${m.cls}`}>{m.label}</span>
            <span className="vm-legend-desc">{m.desc}</span>
          </div>
        ))}
      </div>

      <div className="vm-catbar">
        <input
          className="vm-search"
          placeholder="Buscar animação ou variável…"
          value={q}
          onChange={e => setQ(e.target.value)}
        />
        <div className="vm-filtros">
          {FILTROS.map(([k, lbl]) => (
            <button
              key={k}
              className={`vm-filtro ${ftipo === k ? 'active' : ''} ${k !== 'todos' ? 'vm-tp-' + TIPOMETA[k].cls : ''}`}
              onClick={() => setFtipo(k)}
            >
              {lbl} <span className="vm-filtro-n">{contagem[k]}</span>
            </button>
          ))}
        </div>
      </div>

      {porCat.length === 0 && <div className="vm-note">Nada encontrado para “{q}”.</div>}

      {porCat.map(([cat, itens]) => (
        <div key={cat} className="vm-catgroup">
          <h3 className="vm-h3">{cat} <span className="vm-catn">{itens.length}</span></h3>
          <div className="vm-table-wrap">
            <table className="vm-table vm-cattable">
              <thead>
                <tr><th style={{ width: '22%' }}>Animação</th><th style={{ width: '12%' }}>Tipo</th><th>Variáveis editáveis</th></tr>
              </thead>
              <tbody>
                {itens.map(a => {
                  const m = TIPOMETA[a[3]]
                  return (
                    <tr key={a[1]}>
                      <td>
                        <strong>{a[0]}</strong>
                        <code className="vm-compid">{a[1]}</code>
                      </td>
                      <td>
                        <span className={`vm-tp vm-tp-${m.cls}`} title={m.desc}>{m.label}</span>
                        {a[5] && <span className="vm-nota" title={a[5]}>ⓘ</span>}
                      </td>
                      <td>
                        <div className="vm-vars">
                          {a[4].map((v, i) => <span key={i} className="vm-var">{v}</span>)}
                        </div>
                        {a[5] && <div className="vm-nota-txt">{a[5]}</div>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      <div className="vm-note">
        Componentes em <code>remotion/src/compositions/*.tsx</code> (registrados no <code>Root.tsx</code>). Preview animado no reel
        <code>out/acervo_reel.mp4</code>. Próximo: atribuir <strong>probabilidade</strong> por formato e ligar no Director (MOTOR).
      </div>
    </div>
  )
}
