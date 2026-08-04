# Collab + Tela Final — automação pós-upload (spec, 31/07/2026)

> **Pedido do Piter (31/07).** Duas automações novas na esteira: **tela final** em todo vídeo e
> **collab** (pedido + aceite) entre canais da rede.
> **Ownership:** o RPA vive no `drive-to-youtube` (`lib/`) e entra na esteira — território da
> **sessão esteira**. A *configuração* (par de collab, liga/desliga tela final) já está pronta no
> Painel Youtube › Canais › aba Produção (feita pela sessão do editor/VidMator).

## 1. O achado que define a arquitetura: NÃO existe API

| recurso | API? | evidência |
|---|---|---|
| Tela final (end screen) / cards | ❌ | feature request aberta no Google Issue Tracker [#387277988](https://issuetracker.google.com/issues/387277988) desde jan/2025, nunca implementada |
| Collab (colaboradores no vídeo) | ❌ | recurso só existe dentro do Studio ([suporte oficial](https://support.google.com/youtube/answer/16554898)) |

⇒ **Os dois saem por RPA no YouTube Studio**, na mesma trilha do `lib/pin.py` que já roda em
produção: Playwright `connect_over_cdp` no browser do AdsPower (sessão já logada), com
`lib/adspower.py` (throttle + `garantir_app`), `lib/pinlock.py` (cap global 5) e lock por profile.

**Reaproveitar do `pin.py`** (já resolvidos, não reinventar): `_dismiss_consent`, `_human_sleep`,
`_human_scroll_until`, `_pause_video`, `_dump_menu` (evidência forense quando o seletor some) e o
padrão de confirmação visual no fim de cada passo.

## 2. Mecânica do collab (confirmada na doc oficial)

- Até **10 colaboradores** por vídeo; vale long-form e Shorts (live ativa não, arquivada sim).
- Convite: Studio → vídeo → **Detalhes → Público → Mostrar mais → Convidar colaborador** → busca o
  handle do canal.
- **O convidado precisa ACEITAR.** Enquanto não aceita, é invisível pro espectador.
- Aceito: nome, avatar e **botão de inscrição** do parceiro aparecem sob o vídeo, e o vídeo entra
  no **feed de inscritos de todos os colaboradores** (o ganho que motivou o pedido).
- Receita **não é dividida** — fica 100% com o canal que subiu.

### Aceite (esclarecido pelo Piter 31/07)
O convite **gera um LINK** que o canal-alvo abre pra aceitar. Ou seja, o aceite não depende de achar
notificação no Studio do alvo — basta abrir o link no profile certo. Por isso `lib/collab.py` guarda
o link em `_collab_links.json` (`pendentes_de_aceite()` é a entrada do futuro RPA de aceite).
**Decisão do Piter: o pedido vem primeiro e bem robusto; o aceite depois.**

## 2b. RECONHECIMENTO FEITO (ENO2, 31/07) — seletores REAIS

Rodado com `collab_invite.py --canal ENO2 --video <id> --probe-dialog` (não altera nada):

| passo | rótulo/seletor real | observação |
|---|---|---|
| expandir | `Show more` (`div`) — rótulo completo: *"Show more — Paid promotion, collaboration, and more"* | fica **abaixo do viewport**: precisa `scroll_into_view` antes de clicar |
| seção | `div:Collaboration` + *"Grow your audience by collaborating…"* | só existe após expandir |
| botão | `ytcp-button:Invite a collaborator` (também `button:` e `div:`) | |
| campo | `input[placeholder="Search for a channel"]` | dentro do diálogo |
| confirmar | **`Save`** | ⚠ **NÃO é "Create link"** como diziam os artigos — a UI real confirma em `Save` (o vocabulário aceita os dois por segurança) |
| cancelar | `Cancel` | o probe sai com `Esc`, sem tocar em Save |

**Achado operacional:** o profile do **EST (`k1e61o9g`) está DESLOGADO** — o Studio redireciona pra
`accounts.google.com/signin`. Isso quebra o pin desse canal também. Precisa re-login manual no
AdsPower. ENO2/CO3/CON estavam logados. *(A pedido do Piter, os testes seguem só no ENO2.)*

## 2c. SEQUÊNCIA REAL do convite (ditada pelo Piter 31/07) — implementada

1. **Buscar pelo NOME EXATO** do canal-alvo (⚠ **não** `@handle` — o Studio casa pelo nome).
   O alvo aparece como **primeiro da lista** → clicar pra selecionar.
2. Abre a tela de **permissão de ver analytics** + botão **`Criar link`**.
   *(o toggle de analytics fica no default do YouTube — o RPA não mexe nele)*
3. O **link aparece na tela** → copiar e **fechar essa janela**.
4. De volta à busca, o alvo fica com **ação pendente** → **`Salvar`** efetiva o convite.

⚠ `Criar link` e `Salvar` são **passos distintos** — juntar os dois num regex só faria o RPA parar
no meio, com o convite criado e **não efetivado**.

**Guard anti-canal-errado** (`_casa_nome`): o 1º da lista só é clicado se o texto dele casar com o
nome buscado (normalizado, ignorando acento/caixa/@/contagem de inscritos). Busca que não devolve o
canal certo **aborta** em vez de convidar quem estiver no topo — convite errado fica público.

## 2d. ✅ CONVITE VALIDADO EM PRODUÇÃO (ENO2 → Whispers from Arcturus, 31/07)

`python collab_invite.py --canal ENO2 --video IiuiQO_0FoA --alvo "Whispers from Arcturus"`
→ `{"status": "convidado", "link": "https://studio.youtube.com/channel/UC3-iT3rJ2Zi3nn89GJ1DURw/collaboration/UC1HJA1zSkBAEXCggFgikqAQ"}`

Confirmado por leitura independente da tela: o colaborador aparece como **"Pending acceptance"** e o
botão da seção virou **"Manage collaborators"**. Rodar de novo devolve `ja_convidado` (não duplica).

### As 5 armadilhas que só apareceram rodando de verdade
Cada uma custou um run; todas estão travadas no código com comentário:

1. **Lazy render** — a seção Colaboração só existe no DOM depois de expandir **e rolar**. O probe
   rolava, o fluxo real não → botão "não encontrado". Hoje os dois usam `_abrir_secao_collab()`.
2. **Shadow DOM** — `page.evaluate`/`querySelectorAll` voltam **vazios** com a lista na tela;
   **locator do Playwright atravessa**. Só se descobre por screenshot.
3. **8 `[role=dialog]` na página** — escopar a busca no `.last` pegava o diálogo errado. O certo é
   `page.get_by_role("option")` direto na página.
4. **DOIS botões "Save"** — o da página (topo) e o do modal. Clicar no da página deixa o convite
   "pending" e **não efetiva**. `_clicar_salvar_do_dialogo()` escopa no modal e, na dúvida, pega o
   de maior `y`. Depois **confirma que o modal fechou** — senão reporta falha.
5. **Link errado** — o regex genérico pegava o "Video link" (`youtu.be/…`) do painel lateral. O link
   do convite tem forma fixa: `studio.youtube.com/channel/<CANAL>/collaboration/<ID>`.

### O que falta
Só o **RPA de aceite** (o link já é guardado em `_collab_links.json`; `pendentes_de_aceite()` é a
entrada dele) e a **tela final**. O Piter definiu: pedido primeiro, aceite depois.

## 3. Decisões do Piter (31/07) — já fechadas

| tema | decisão | consequência |
|---|---|---|
| Escopo do collab | **Pares fixos escolhidos a mão** | nada de round-robin/automático. Canal sem `collab_alvo` não faz collab. O vínculo entre canais fica **público** no vídeo — por isso o Piter quer controlar par a par |
| Tela final | **"Importar do vídeo anterior"** | o Studio copia a tela final do último vídeo do canal em ~2 cliques. MUITO menos seletores que montar elemento a elemento ⇒ menos quebra quando o YouTube mexe na UI |
| Timing do aceite | **Antes do `publishAt`** | roda na janela entre upload e publicação, com o vídeo ainda agendado. O vídeo já estreia com os colaboradores visíveis |

## 4. Configuração (PRONTA — Painel Youtube › Canais › aba Produção)

Gravada em `canais_yt.producao` (JSONB; fallback local enquanto a coluna não existir):

```json
{ "collab_alvo": "TTM",   // alias do canal parceiro; vazio/ausente = sem collab
  "tela_final": true }    // aplica tela final automaticamente
```

O select do `collab_alvo` lista os outros canais do painel (nunca ele mesmo).

## 5. Desenho proposto na esteira

```
render → thumb → upload (unlisted) → [TELA FINAL] → [CONVITE COLLAB] → pin → agenda
                                                            ↓
                                          [ACEITE no canal-alvo] (outro profile AdsPower)
                                                            ↓
                                                    antes do publishAt
```

- `lib/endscreen.py` → `aplicar_tela_final(video_id, cdp_endpoint)` — importa do vídeo anterior.
- `lib/collab.py` → `convidar(video_id, handle_alvo, cdp)` e `aceitar_pendentes(cdp) -> list`.
- O aceite roda **no profile do canal-alvo** — é outro login, então é uma etapa própria
  (não dá pra fazer na mesma sessão do convite).

**REGRAS DE OURO (herdadas da esteira):** nenhuma das duas pode segurar a publicação. Falhou →
`tela_final_status` / `collab_status` = `pendente` na célula, o vídeo **segue normalmente**, e o
`upload_daily_check --apply` re-tenta no dia seguinte. Publicação é prioridade absoluta.

## 6. Riscos anotados

1. **Footprint da rede** — o collab **expõe publicamente** a ligação entre os canais (nome + botão
   de inscrição um do outro). Isso inverte o isolamento que a operação mantém (proxy dedicado por
   canal, metadata stripping). O Piter está ciente e por isso escolheu pares fixos manuais.
2. **Fragilidade de RPA** — UI do Studio muda sem aviso. Mitigar com: "importar do vídeo anterior"
   (menos passos), `_dump_menu` em toda falha e status `pendente` que nunca bloqueia.
3. **Concorrência AdsPower** — convite e aceite são profiles diferentes; respeitar `pinlock` (cap 5)
   e o lock por profile (CO3/CO4 dividem profile).
