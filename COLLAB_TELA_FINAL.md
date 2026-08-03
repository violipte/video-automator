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

### ⚠ Incógnita a resolver ANTES de codar
A doc oficial **não diz onde o canal-alvo vê e aceita** o convite pendente (notificação no Studio?
seção própria? link enviado?). **Primeiro passo obrigatório = reconhecimento**: abrir o Studio de um
canal via AdsPower e mapear as 3 telas (tela final · envio do convite · aceite), anotando seletores
estáveis. Sem isso não há como escrever o `lib/collab.py`.

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
