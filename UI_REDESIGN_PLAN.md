# UI Redesign Plan — Video Automator

> Plano de migração do UI atual (HTML/JS inline em `app.py`, ~8k linhas) para
> **React 19 + Vite + CSS puro**, alinhado com Link Tracker 2.0.
> Status: **Fase A** (auditoria + wireframes) — 2026-05-07.

---

## 1. Princípios de design

1. **Densidade controlada**: cada tela mostra só o que importa naquele momento. Detalhes em modal/expand.
2. **Hierarquia visual clara**: ações primárias destacadas, secundárias discretas, destrutivas em vermelho com confirmação.
3. **Feedback imediato**: toasts em ações, loading states em todo fetch, optimistic updates onde fizer sentido.
4. **Mobile-first responsivo**: você precisa adicionar links pelo celular sem fricção. Tabelas viram cards no <768px.
5. **Componentização**: 1 botão = 1 componente. Mudar visual em 1 lugar = aplicar em todo lugar.
6. **Estado centralizado**: Zustand (3KB, simples). Sem prop drilling.
7. **Zero magic**: nada de string Python com escape de regex. JSX puro.

---

## 2. Mapeamento feature-por-feature (9 abas → 6 abas)

### Sidebar atual → final

| Atual | Final | Decisão |
|---|---|---|
| Temas | **Temas** | reformar |
| Roteiros | ❌ | merge → Templates (subaba Roteiro) |
| Narração | ❌ | remover (tudo via comando) |
| Thumbnail | ❌ | merge → Templates (subaba Thumbnail) |
| Templates | **Templates** | core, expandir com 5 subabas |
| Produção | ❌ | remover (tudo via comando) |
| Monitor | **Monitor** | reformar + botão Produzir Tudo |
| Log | **Log** | +coluna Thumbnail |
| Config | **Config** | enxugar |
| (novo) | **Backlog Temas** | manter (já redesenhado) |

### Detalhamento por aba — o que vai/fica/migra

#### 🟡 Temas

**Mantém:**
- Grid (linhas=datas, colunas=canais) — o coração visual
- Coluna especial **BASE** no índice 0 (já implementada)
- Drag & drop colunas/linhas
- Replicate cell (modal)
- Date-based rows
- Modal ao clicar célula com campos: tema, título, thumb, link de referência

**Remove:**
- ❌ Sync Supabase (botão + lógica)
- ❌ Campo "Roteiro" no modal (já não usa, vai pelo .txt em Roteiros/)
- ❌ Dropdown "Pipeline" no modal (config fica em Templates)
- ❌ Botão "Produzir Tudo" abaixo da tabela
- ❌ Botão "Gerar Roteiros"
- ❌ Função "Lote" via dropdown de data

**Adiciona:**
- ✅ Botão pequeno "Distribuir BASE → canais" (na célula BASE preenchida)
- ✅ Indicador visual: célula com `coringa_distribuido_em` ganha selo "✓ distribuído"

#### 🟢 Templates (core do redesign)

**Layout**: Grid de cards (1 por canal). Cada card tem:
- Thumb preview (1280x720 da última thumb gerada, ou placeholder)
- Nome do canal
- Indicador de status: 🟢 ativo · 🟡 sem template · 🔴 com erro
- Click → abre modal com **5 subabas**:

```
┌──────────────────────────────────────────────────┐
│ Modal: Canal "EN — Whispers from Arcturus"    ✕ │
├──────────────────────────────────────────────────┤
│ [ Render ] [ Roteiro ] [ Narração ] [ Thumb ] [ Link ] │
├──────────────────────────────────────────────────┤
│ (conteúdo da subaba selecionada)                 │
│                                                  │
└──────────────────────────────────────────────────┘
```

##### Subaba **Render**
- Resolution (1920x1080 default)
- Pasta de imagens / vídeos de fundo
- Tipo fundo (imagens c/ Ken Burns | vídeos)
- Duração por imagem (escalonada)
- Moldura PNG (chromakey/alpha + opacidade)
- CTA (chromakey verde, posição, escala, scheduling)
- Ajustes Lumetri: gamma, contrast, saturation, curves, vignette
- Aleatorização (subtle/medium/none)
- Idioma + regras de legenda (max chars/linhas, casing)
- Pasta de saída

##### Subaba **Roteiro** (NOVA — vem da aba Roteiros antiga)
- Pipeline LLM: lista de etapas (LLM | texto | code)
- Cada etapa: credencial, modelo, system_message, prompt
- Reordenar etapas (drag & drop)
- Testar etapa individual
- Testar cadeia até aqui
- Min chars do roteiro
- `tolerancia_fallback_pct` (0.80 default)

##### Subaba **Narração**
- Voice provider (ElevenLabs | Minimax | clone)
- Voice ID + preview audio
- Speed, pitch
- Fallback Inworld: voice_id + provider + model
- Boost language

##### Subaba **Thumbnail**
- Modo: prompt-mixer | agente | imagem_fixa
- Conforme modo, mostra editor:
  - prompt-mixer: prompt_base + pools cena/character (textareas)
  - agente: edit `agents/thumbnail-{canal}/CLAUDE.md`
  - imagem_fixa: imagem_base + fonte + cor + posições
- Casing override (uppercase | titlecase | default)
- Botão "🧪 Testar agora" (gera 1 thumb preview com tema fictício)
- Preview da última thumb gerada

##### Subaba **Link Tracker** (NOVA — vem de Config antiga)
- URL da página de destino (LP)
- Slug base do canal
- Comentário fixado no YouTube (template com {slug})
- Auth do tracker (URL + token)

#### 🟡 Monitor (reformulado + botão Produzir Tudo)

**Atual ruim** porque mostra muita coisa irrelevante. Reformular pra dashboard limpo:

```
┌──────────────────────────────────────────────────────────┐
│ Monitor                            [▶ Produzir Tudo]    │
├──────────────────────────────────────────────────────────┤
│  Status do sistema (em tempo real)                       │
│  ┌────────────────┬────────────────┬──────────────────┐  │
│  │ Produção       │ Render Queue   │ Workers          │  │
│  │  🟢 ativa      │  3 jobs · 1 ▶  │  2 conectados    │  │
│  │  Data: 12/05   │  fila: 3       │  remoto + local  │  │
│  └────────────────┴────────────────┴──────────────────┘  │
├──────────────────────────────────────────────────────────┤
│  Canais em produção (ao vivo)                            │
│  ┌──────────────────────────────────────────────────┐    │
│  │ EN     ▶ rendering   65% ▓▓▓▓▓▓░░░░  (12min)    │    │
│  │ DE     ▶ narrando    -                          │    │
│  │ EN2    ✓ concluído                              │    │
│  │ EN3    ⏸ aguardando                             │    │
│  └──────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────┤
│  Log live (últimas 20 entradas, scroll ao topo nas novas)│
│  > 12:30 EN: render started                              │
│  > 12:28 DE: narration ok (Inworld fallback)             │
│  ...                                                     │
└──────────────────────────────────────────────────────────┘
```

**Botão `▶ Produzir Tudo`**:
- Modal pergunta: data início (YYYY-MM-DD), modo (loop/single)
- Confirma → chama `/api/producao-completa/iniciar`
- Painel acima atualiza em tempo real

#### 🟡 Log

**Mantém:**
- Tabela com filtros (data, canal, status)
- Botões export CSV / clear

**Adiciona:**
- ✅ **Coluna Thumbnail**: mostra `path` + ícone do modo (🎨 prompt-mixer | 🤖 agente | 🖼️ imagem_fixa | ⚠️ fallback)
- ✅ Click em thumb path → preview modal

#### 🟡 Config (enxuto)

**Mantém:**
- API keys (ai33.pro, OpenAI, Claude, Gemini, Inworld)
- Render worker token
- Credenciais LLM (multi-key por provedor — usado no fallback)

**Remove:**
- ❌ Sync Supabase (URL + key)
- ❌ Sync Google Sheets (ID + tab + key)
- ❌ Comment template (vai pra Templates)
- ❌ Tracker URL + auth (vai pra Templates)

#### 🟢 Backlog Temas (manter)
Já redesenhado recentemente. Sem mudança nesta fase.

---

## 3. Wireframes ASCII das 6 abas finais

### 3.1 Layout shell (sidebar + main)

```
┌────────────────────────────────────────────────────────────┐
│ ┌──────────┐ ┌────────────────────────────────────────┐ │
│ │ ⚡ Auto  │ │  [Page Header — título + ações]        │ │
│ │  mator   │ │  ─────────────────────────────────     │ │
│ │          │ │                                        │ │
│ │ ▸ Temas  │ │  [Conteúdo da página]                 │ │
│ │ ▸ Bcklg  │ │                                        │ │
│ │ ▸ Templ  │ │                                        │ │
│ │ ▸ Mntr   │ │                                        │ │
│ │ ▸ Log    │ │                                        │ │
│ │ ▸ Cnfg   │ │                                        │ │
│ │          │ │                                        │ │
│ │ ─────    │ │                                        │ │
│ │ pid: 123 │ │                                        │ │
│ │ 🟢 prod  │ │                                        │ │
│ └──────────┘ └────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### 3.2 Templates (cards + modal)

```
┌─ Templates ─────────────────────────  [+ Novo template] ─┐
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ [thumb]  │  │ [thumb]  │  │ [thumb]  │  │ [thumb]  │  │
│  │  EN 🟢   │  │  DE 🟢   │  │  EN2 🟡  │  │  CO1 🔴  │  │
│  │ 2 vids/d │  │ 1 vid/d  │  │ s/templt │  │  erro    │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────┐  ...                                       │
│  │ [thumb]  │                                            │
│  │  ENO2    │                                            │
│  └──────────┘                                            │
└──────────────────────────────────────────────────────────┘
```

Modal ao clicar card EN:
```
╔═════════════════════════════════════════════════════╗
║  EN — Whispers from Arcturus           [↩] [✕] ║
╠═════════════════════════════════════════════════════╣
║ ┌─Render─┐ Roteiro  Narração  Thumbnail  LinkTrack ║
║ ╞════════╧══════════════════════════════════════╡  ║
║ │ Resolution: [1920] x [1080]   FPS: [30]       │  ║
║ │ Pasta imagens: [F:/.../EN-images]    [Browse] │  ║
║ │ Tipo: ⦿ imagens (Ken Burns) ⦿ vídeos          │  ║
║ │ Duração/imagem: [escalonado ▼]                │  ║
║ │ ...                                           │  ║
║ │ Moldura: [moldura.png]   Opacidade: [1.0]     │  ║
║ │ CTA: [cta.mp4]   Pos: [bottom-right ▼]        │  ║
║ │ Lumetri: [▶ expandir ajustes]                 │  ║
║ │ Aleatoriz: ⦿ none ⦿ subtle ⦿ medium          │  ║
║ │ ...                                           │  ║
║ │                                               │  ║
║ │           [Cancelar]    [Salvar]              │  ║
║ └───────────────────────────────────────────────┘  ║
╚═════════════════════════════════════════════════════╝
```

### 3.3 Monitor

```
┌─ Monitor ────────────────  [▶ Produzir Tudo] [↻]  ─┐
│                                                     │
│ ┌─────────────┬─────────────┬────────────────────┐ │
│ │ Produção    │ Render      │ Workers            │ │
│ │ 🟢 ativa    │ 3 jobs      │ 2 conectados       │ │
│ │ 12/05/2026  │ ▶ EN_20260512│ remoto+local       │ │
│ └─────────────┴─────────────┴────────────────────┘ │
│                                                     │
│ Canais (linha por canal, atualiza ao vivo)         │
│ ┌─────────────────────────────────────────────────┐ │
│ │ EN    ▶ rendering   65% ▓▓▓▓▓▓░░  (12min)     │ │
│ │ DE    ▶ narração    --                         │ │
│ │ EN2   ✓ concluído   ENO2_20260512_01.mp4      │ │
│ │ EN3   ⏸ aguardando                             │ │
│ │ ENO2  ✓ concluído                              │ │
│ │ ENS   ❌ erro: roteiro curto                   │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ Log live (últimas 20)                               │
│ > 12:30 EN: render started                         │
│ > 12:28 DE: narration ok (Inworld fallback)        │
│ > ...                                               │
└─────────────────────────────────────────────────────┘
```

### 3.4 Temas (grid + BASE)

```
┌─ Temas ───────────  [▶ Distribuir BASE]  [+ data]  ─┐
│                                                      │
│       │ BASE  │  CON  │  EN   │  DE   │  CO1  │ ... │
│  ─────┼───────┼───────┼───────┼───────┼───────┼─────│
│ 11/05 │ ✓CHO  │ Cho.. │ STAR  │ AUSER │       │     │
│       │ Don't │ Don't │ DON'T │ TU N  │       │     │
│ 12/05 │ ✓God  │ God   │ STAR  │       │       │     │
│ 13/05 │ ✓Cho  │       │       │       │       │     │
│ 14/05 │       │       │       │       │       │     │
│                                                      │
│ Click em célula → modal (tema, título, thumb)       │
└──────────────────────────────────────────────────────┘
```

### 3.5 Backlog Temas (já existe, manter)

```
┌─ Backlog Temas ──── [⚡BASE] [📤Distrib] [🔄CO*] ───┐
│ 🟢 Auto: LIGADA — clique pra desligar               │
│ Cron: BASE 🟢 (15min) · DIST 🟢 (15min, delay 30m)  │
├─────────────────────────────────────────────────────┤
│ [Cole link YT____] [Título______] [Thumb__] [+ Add] │
├─────────────────────────────────────────────────────┤
│ Data    │ G │ CO* │ Título           │ Thumb        │
│ 11/05   │ ✓ │ ✓   │ Chosen Ones...   │ RELAX...     │
│ 12/05   │ ✓ │ ✓   │ God Says...      │ 2 HOURS...   │
│ 13/05   │ ✓ │ ✓   │ Chosen Ones...   │ IF YOU SEE.. │
└─────────────────────────────────────────────────────┘
```

### 3.6 Log (com Thumbnail)

```
┌─ Log ──────  [filtros]  [export CSV]  [clear]  ────┐
│ Data       │ Canal │ Status │ Roteiro │ Render │ Thumb│
│ 12/05      │ EN    │ ✓      │ 22.5k  │ 24min  │ 🎨pm │
│ 12/05      │ DE    │ ✓      │ 21.0k  │ 22min  │ 🎨pm │
│ 12/05      │ NARC  │ ✓      │ 19.8k  │ 18min  │ 🤖ag │
│ 12/05      │ CO1   │ ✓      │ -      │ 17min  │ 🖼️if │
│ 11/05      │ ENO   │ ❌      │ erro   │ -      │ -    │
└─────────────────────────────────────────────────────┘

Legenda: 🎨 prompt-mixer · 🤖 agente · 🖼️ imagem_fixa · ⚠️ fallback
```

### 3.7 Config (enxuto)

```
┌─ Config ────────────────────────────────────────────┐
│                                                     │
│ ┌─ API Keys ─────────────────────────────────────┐  │
│ │ ai33.pro:     [...4in8o976]    [editar] [test]│  │
│ │ OpenAI:       [...kSfgAB12]    [editar] [test]│  │
│ │ Inworld:      [não configurado] [adicionar]   │  │
│ │ Render Token: [...3cfabb7]     [editar]       │  │
│ └────────────────────────────────────────────────┘  │
│                                                     │
│ ┌─ Credenciais LLM ──────────────────────────────┐  │
│ │ Claude API    [adicionar]                      │  │
│ │ Claude CLI    [Max plan ✓]    (sem key)        │  │
│ │ GPT           [adicionar]                      │  │
│ │ Gemini  (9 keys)              [+ adicionar]   │  │
│ │   ├ key 1 ✓                   [editar][teste] │  │
│ │   ├ key 2 ✓                   [editar][teste] │  │
│ │   └ ...                                        │  │
│ └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 4. Paleta + Tipografia (PREMIUM)

Inspiração: Vercel Dashboard + Linear + Stripe Dashboard. Pretos profundos, verde
esmeralda sofisticado (não fluorescente), accent dourado champanhe muito discreto
pra estados raros/destaque (selos premium), tipografia com tracking refinado.

### Tokens CSS

```css
:root {
  /* ═══ Backgrounds ═══ Pretos profundos com hierarquia sutil */
  --bg:           #08090a;   /* fundo geral (deep black premium) */
  --bg-elevated:  #0c0d0f;   /* zonas elevadas dentro do bg */
  --panel:        #111113;   /* cards, modais, sidebar */
  --panel-2:      #17181b;   /* hover de panel, header de tabela */
  --panel-3:      #1c1d20;   /* segundo nivel (modal sobre panel) */

  /* ═══ Borders ═══ Quase invisíveis, premium */
  --border:       #1f2024;   /* divisores padrão (quase sumindo) */
  --border-hi:    #2a2b2f;   /* hover/focus */
  --border-glow:  rgba(255, 255, 255, 0.04);  /* inner glow */

  /* ═══ Texto ═══ Off-white premium, não branco puro */
  --text:         #f0f0f2;   /* primário */
  --text-sec:     #a1a1aa;   /* secundário (zinc-400) */
  --text-dim:     #71717a;   /* placeholders (zinc-500) */
  --text-muted:   #52525b;   /* timestamps, hints (zinc-600) */

  /* ═══ Accent verde esmeralda PREMIUM (mais sóbrio, menos vibrante) */
  --accent:        #10b981;            /* emerald-500 */
  --accent-hover:  #059669;            /* emerald-600 */
  --accent-active: #047857;            /* emerald-700 */
  --accent-soft:   rgba(16, 185, 129, 0.10);  /* tint de fundo */
  --accent-glow:   rgba(16, 185, 129, 0.25);  /* focus ring */
  --accent-text:   #34d399;            /* texto destacado em verde */

  /* ═══ Gold champanhe ═══ usado SÓ em destaques raros/premium */
  --gold:          #c9a96e;
  --gold-soft:     rgba(201, 169, 110, 0.10);

  /* ═══ Estados ═══ Refinados, menos saturados */
  --success:       #10b981;
  --success-soft:  rgba(16, 185, 129, 0.10);
  --warning:       #d97706;            /* amber-600 (menos amarelo) */
  --warning-soft:  rgba(217, 119, 6, 0.10);
  --danger:        #dc2626;            /* red-600 (menos coral) */
  --danger-soft:   rgba(220, 38, 38, 0.10);
  --info:          #2563eb;            /* blue-600 */
  --info-soft:     rgba(37, 99, 235, 0.10);

  /* ═══ Sombras refinadas ═══ Multi-layer, sutil mas presente */
  --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.4);
  --shadow-sm: 0 2px 4px rgba(0, 0, 0, 0.4),
               inset 0 1px 0 rgba(255, 255, 255, 0.03);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.5),
               0 0 0 1px rgba(255, 255, 255, 0.04);
  --shadow-lg: 0 12px 32px rgba(0, 0, 0, 0.6),
               0 0 0 1px rgba(255, 255, 255, 0.05);
  --shadow-glow: 0 0 0 3px var(--accent-glow);

  /* ═══ Radius ═══ Mais arredondado em destaques */
  --radius-xs: 4px;
  --radius-sm: 6px;
  --radius:    8px;
  --radius-lg: 12px;
  --radius-xl: 16px;

  /* ═══ Transições refinadas ═══ ease-out cubic */
  --ease:        cubic-bezier(0.4, 0, 0.2, 1);
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);  /* leve overshoot */
  --t-fast:      0.12s var(--ease);
  --t-base:      0.2s var(--ease);
  --t-slow:      0.4s var(--ease);

  /* ═══ Backdrop blur ═══ pra modais */
  --blur:        12px;
}
```

### Tipografia

```css
--font-sans: 'Inter', 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'JetBrains Mono', 'Geist Mono', 'Fira Code', Consolas, monospace;

/* Escala (rem ao invés de px pra acessibilidade) */
--text-xs:   0.6875rem;  /* 11px - labels, hints */
--text-sm:   0.8125rem;  /* 13px - body secundário */
--text-base: 0.875rem;   /* 14px - body padrão */
--text-md:   1rem;       /* 16px - destaques */
--text-lg:   1.25rem;    /* 20px - h3 */
--text-xl:   1.5rem;     /* 24px - h2 */
--text-2xl:  1.875rem;   /* 30px - h1 page header */

/* Pesos */
--fw-normal: 400;
--fw-medium: 500;
--fw-semi:   600;        /* preferido pra headings (mais elegante que 700) */
--fw-bold:   700;        /* só destaques fortes */

/* Tracking refinado em headings (premium) */
--track-tight:  -0.02em;  /* h1, h2 */
--track-normal: 0;
--track-wide:   0.02em;   /* labels caixa-alta */

/* Line-heights generosos */
--lh-tight:  1.2;   /* headings */
--lh-base:   1.5;   /* body */
--lh-loose:  1.7;   /* parágrafos longos */
```

### Detalhes premium aplicados em todo lugar

```css
/* Botões: lift sutil no hover */
.btn:hover {
  transform: translateY(-1px);
  transition: var(--t-fast);
}

/* Inputs/cards: focus ring com glow verde */
.input:focus,
.card:focus-within {
  outline: none;
  box-shadow: var(--shadow-glow);
  border-color: var(--accent);
}

/* Modal overlay com backdrop blur */
.modal-overlay {
  background: rgba(8, 9, 10, 0.7);
  backdrop-filter: blur(var(--blur));
}

/* Inner glow nas borders dos cards (sutil shimmer) */
.card {
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
}

/* Scrollbars custom (finas, premium) */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--border-hi);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* Skeleton shimmer pra loading */
@keyframes shimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}
.skeleton {
  background: linear-gradient(
    90deg,
    var(--panel) 0%,
    var(--panel-2) 50%,
    var(--panel) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}
```

---

## 5. Componentes base reusáveis

```
src/components/
├── Layout/
│   ├── AppShell.jsx        # sidebar + main wrapper
│   ├── Sidebar.jsx         # nav + footer
│   └── PageHeader.jsx      # título + ações
├── Common/
│   ├── Button.jsx          # primary | secondary | danger | ghost (size: sm | md)
│   ├── Input.jsx           # text | number | select | textarea
│   ├── Card.jsx            # container padrão
│   ├── Modal.jsx           # overlay + close + body + footer
│   ├── ModalTabs.jsx       # tabs internas (Templates 5 abas)
│   ├── Table.jsx           # tabela responsiva (vira cards <768px)
│   ├── Badge.jsx           # status pills (success | warning | danger | info)
│   ├── Toast.jsx           # global notifications (provider + hook)
│   ├── ConfirmDialog.jsx   # modal de confirmação (destrutivos)
│   └── Spinner.jsx         # loading state
├── Domain/
│   ├── ChannelCard.jsx     # card de canal (Templates page)
│   ├── ProductionRow.jsx   # linha de canal em Monitor
│   ├── BacklogItem.jsx     # row de Backlog Temas
│   └── LogRow.jsx          # row de Log
└── Forms/
    ├── PoolEditor.jsx      # textarea com 1-linha-por-item
    ├── ColorPicker.jsx     # input cor com preview
    ├── FontPicker.jsx      # dropdown de fonts disponíveis
    └── KeyValueEditor.jsx  # pra credenciais multi-key
```

---

## 6. Cronograma das próximas fases

| Fase | Escopo | Tempo | Output |
|---|---|---|---|
| **A (atual)** | Auditoria + wireframes + paleta | 4h | Este doc + aprovação |
| **B** | Setup React+Vite + componentes base | 6h | `frontend2/` rodando, layout shell + 8 componentes base |
| **C1** | Migrar **Templates** (mais complexa) | 5h | 5 subabas funcionais, paridade com backend atual |
| **C2** | Migrar **Monitor** (reformulado) | 3h | Dashboard limpo + botão Produzir Tudo |
| **C3** | Migrar **Temas** (grid limpo) | 3h | Sem campos obsoletos, com BASE |
| **C4** | Migrar **Backlog Temas** | 1h | Porta direta do existente |
| **D1** | Migrar **Log** (+ Thumbnail) | 2h | Coluna nova + filtros |
| **D2** | Migrar **Config** (enxuto) | 1h | Só credenciais |
| **D3** | Remover endpoints obsoletos backend | 1h | Limpa app.py |
| **E** | Build + deploy + polimento | 3h | Servido pelo FastAPI prod |

**Total: ~29h** dev. Pode ser entregue em 2-3 sprints.

---

## 7. Decisões pendentes do Piter

1. **Aprovar este plano completo?** Wireframes batem com o que você imaginou?
2. **Paleta**: o verde-esmeralda atual continua? Ou ajustar pro verde do Link Tracker?
3. **Tipografia**: Inter (popular, bonita, free) está OK? Ou prefere SF Pro / Outros?
4. **Mobile breakpoint**: 768px OK pra tabela→card?
5. **Fase B (setup React)**: parto direto após sua aprovação?

---

## Apêndice — Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Backend `app.py` tem ~2k linhas só de endpoint, refactor pode quebrar | Manter 100% compatível: React só consome API existente, sem mudar endpoint nesta fase |
| 29h é muito pra rodada única | Faseado: cada fase B/C/D pode ir pra prod independente |
| Risco de regressão visual em PROD durante migração | Hospedar React em path separado (`/v2`) durante migração; toggle pra voltar pra UI velha |
| Bibliotecas npm = vulnerabilidades | Usar `npm audit` em CI, manter deps minimas (React + Vite + react-router + zustand) |
| Estado global mal modelado = bugs | Zustand com slices por domínio (production, backlog, templates, monitor) |
