"""
Módulo de correção de legendas SRT.
Aplica regras tabeladas por idioma/template: substituições, formatação, limpeza.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
TEMP_DIR = Path(__file__).parent / "temp"

HESITACOES = {
    "en": [r"\buh\b", r"\bum\b", r"\bah\b", r"\bhm+\b", r"\blike\b,?\s*$"],
    "de": [r"\bäh\b", r"\böh\b", r"\bhm+\b", r"\bnaja\b"],
    "pt": [r"\béh\b", r"\bahm?\b", r"\bhm+\b", r"\btipo\b,?\s*$"],
    "es": [r"\beh\b", r"\bahm?\b", r"\bhm+\b", r"\bo sea\b,?\s*$"],
}


@dataclass
class Legenda:
    indice: int
    inicio: str
    fim: str
    texto: str


def _parse_srt(conteudo: str) -> list[Legenda]:
    """Faz parse de conteúdo SRT para lista de Legenda."""
    blocos = re.split(r"\n\s*\n", conteudo.strip())
    legendas = []
    for bloco in blocos:
        linhas = bloco.strip().split("\n")
        if len(linhas) < 3:
            continue
        try:
            indice = int(linhas[0].strip())
        except ValueError:
            continue
        match = re.match(r"(.+?)\s*-->\s*(.+)", linhas[1].strip())
        if not match:
            continue
        inicio = match.group(1).strip()
        fim = match.group(2).strip()
        texto = "\n".join(linhas[2:]).strip()
        legendas.append(Legenda(indice, inicio, fim, texto))
    return legendas


def _legendas_para_srt(legendas: list[Legenda]) -> str:
    """Converte lista de Legenda de volta para texto SRT."""
    blocos = []
    for i, leg in enumerate(legendas, 1):
        blocos.append(f"{i}\n{leg.inicio} --> {leg.fim}\n{leg.texto}\n")
    return "\n".join(blocos)


def _carregar_regras(idioma: str, template_id: str = None) -> dict:
    """Carrega e mescla regras globais + template-específicas."""
    arquivo = RULES_DIR / f"{idioma}.json"
    if not arquivo.exists():
        return {
            "substituicoes": {},
            "max_chars_linha": 42,
            "remover_hesitacoes": True,
            "capitalizar_inicio": True,
        }

    with open(arquivo, "r", encoding="utf-8") as f:
        todas_regras = json.load(f)

    regras = dict(todas_regras.get("_global", {}))

    if template_id and template_id in todas_regras:
        especificas = todas_regras[template_id]
        # Mesclar substituições (template sobrescreve global)
        if "substituicoes" in especificas:
            subs_global = regras.get("substituicoes", {})
            subs_global.update(especificas["substituicoes"])
            regras["substituicoes"] = subs_global
        # Sobrescrever demais campos
        for k, v in especificas.items():
            if k != "substituicoes":
                regras[k] = v

    return regras


def _remover_hesitacoes(texto: str, idioma: str) -> str:
    """Remove hesitações/fillers do texto."""
    padroes = HESITACOES.get(idioma, HESITACOES["en"])
    for padrao in padroes:
        texto = re.sub(padrao, "", texto, flags=re.IGNORECASE)
    # Limpar espaços duplos resultantes
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    # Limpar vírgulas soltas
    texto = re.sub(r",\s*,", ",", texto)
    texto = re.sub(r"^\s*,\s*", "", texto)
    texto = re.sub(r"\s*,\s*$", "", texto)
    return texto.strip()


def _aplicar_substituicoes(texto: str, substituicoes: dict) -> str:
    """Aplica substituições de palavras (case-insensitive)."""
    for errado, correto in substituicoes.items():
        texto = re.sub(re.escape(errado), correto, texto, flags=re.IGNORECASE)
    return texto


def _capitalizar_inicio(texto: str) -> str:
    """Capitaliza início de frases."""
    if not texto:
        return texto
    # Capitalizar primeiro caractere
    texto = texto[0].upper() + texto[1:]
    # Capitalizar após pontuação final
    texto = re.sub(r"([.!?]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), texto)
    return texto


def _ts_to_seconds(ts: str) -> float:
    """Converte timestamp SRT (HH:MM:SS,mmm) para segundos."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _seconds_to_ts(s: float) -> str:
    """Converte segundos para timestamp SRT."""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{int(sec):02d},{ms:03d}"


def _interpolar_timestamp(inicio: str, fim: str, fator: float) -> str:
    """Calcula timestamp intermediário entre início e fim."""
    s1 = _ts_to_seconds(inicio)
    s2 = _ts_to_seconds(fim)
    meio = s1 + (s2 - s1) * fator
    return _seconds_to_ts(meio)


def _quebrar_linhas(texto: str, max_chars: int, max_linhas: int = 2) -> str:
    """Quebra texto em até max_linhas, cada uma com no máximo max_chars.
    Prioriza equilíbrio entre linhas. Nunca corta palavras."""
    if len(texto) <= max_chars:
        return texto

    palavras = texto.split()
    if not palavras:
        return texto

    if max_linhas == 1:
        # Construir a linha mais longa possível dentro do limite
        resultado = ""
        for p in palavras:
            teste = f"{resultado} {p}".strip() if resultado else p
            if len(teste) <= max_chars:
                resultado = teste
            else:
                break
        return resultado or texto  # fallback: texto inteiro se nenhuma palavra cabe

    # 2 linhas: tentar encontrar a divisão onde ambas cabem em max_chars
    total = len(palavras)
    melhor_quebra = -1
    melhor_diff = float("inf")

    for i in range(1, total):
        l1 = " ".join(palavras[:i])
        l2 = " ".join(palavras[i:])
        # Ambas devem caber
        if len(l1) <= max_chars and len(l2) <= max_chars:
            diff = abs(len(l1) - len(l2))
            if diff < melhor_diff:
                melhor_diff = diff
                melhor_quebra = i

    # Se encontrou divisão onde ambas cabem, usar
    if melhor_quebra > 0:
        return " ".join(palavras[:melhor_quebra]) + "\n" + " ".join(palavras[melhor_quebra:])

    # Se não cabe em 2 linhas de max_chars, fazer o melhor possível:
    # Linha 1 = máximo que cabe, Linha 2 = resto (libass faz word-wrap se necessário)
    linha1_palavras = []
    for p in palavras:
        teste = " ".join(linha1_palavras + [p])
        if len(teste) <= max_chars:
            linha1_palavras.append(p)
        else:
            break

    if linha1_palavras:
        resto = palavras[len(linha1_palavras):]
        linha1 = " ".join(linha1_palavras)
        linha2 = " ".join(resto) if resto else ""
        return f"{linha1}\n{linha2}" if linha2 else linha1

    # Fallback: dividir no meio
    meio = total // 2
    return " ".join(palavras[:meio]) + "\n" + " ".join(palavras[meio:])


def corrigir_srt(srt_path: str, idioma: str, template_id: str = None, maiuscula: bool = False,
                 max_linhas: int = 2, max_chars: int = None, regras_template: dict = None) -> str:
    """
    Aplica regras de correção em um arquivo SRT.

    Args:
        srt_path: Caminho do arquivo SRT.
        idioma: Código do idioma (en, de, pt, es).
        template_id: ID do template para regras específicas.
        maiuscula: Se True, converte todo texto para MAIÚSCULA (estilo 2).
        max_linhas: Máximo de linhas por bloco (1 ou 2).
        max_chars: Máximo de caracteres por linha (sobrescreve regras se informado).
        regras_template: Regras embutidas no template (prioridade sobre arquivo JSON).

    Returns:
        Caminho do arquivo SRT corrigido na pasta temp/.
    """
    srt_path = Path(srt_path)
    conteudo = srt_path.read_text(encoding="utf-8")
    legendas = _parse_srt(conteudo)

    # Prioridade: regras do template > arquivo JSON por idioma
    if regras_template and regras_template.get("substituicoes"):
        regras = regras_template
    else:
        regras = _carregar_regras(idioma, template_id)

    max_chars = max_chars or regras.get("max_chars_linha", 42)
    max_linhas = regras.get("max_linhas", max_linhas)
    substituicoes = regras.get("substituicoes", {})
    remover_hes = regras.get("remover_hesitacoes", True)
    capitalizar = regras.get("capitalizar_inicio", True)
    palavras_remover = regras.get("palavras_remover", [])

    for leg in legendas:
        texto = leg.texto

        # 1. Remover hesitações
        if remover_hes:
            texto = _remover_hesitacoes(texto, idioma)

        # 2. Remover palavras customizadas
        for palavra in palavras_remover:
            if palavra:
                texto = re.sub(r'\b' + re.escape(palavra) + r'\b', '', texto, flags=re.IGNORECASE)
                texto = re.sub(r'\s{2,}', ' ', texto).strip()

        # 3. Substituições
        if substituicoes:
            texto = _aplicar_substituicoes(texto, substituicoes)

        # 4. Capitalizar início de frases
        if capitalizar:
            texto = _capitalizar_inicio(texto)

        # 5. Maiúscula total (estilo 2)
        if maiuscula:
            texto = texto.upper()

        # 6. Quebra de linhas
        texto = _quebrar_linhas(texto, max_chars, max_linhas)

        # 7. Limpar espaços
        texto = re.sub(r" +", " ", texto)
        texto = "\n".join(line.strip() for line in texto.split("\n"))

        leg.texto = texto

    # Remover legendas vazias
    legendas = [leg for leg in legendas if leg.texto.strip()]

    # Dividir blocos onde a 2ª linha estourou max_chars (evitar 3+ linhas na tela)
    legendas_final = []
    for leg in legendas:
        linhas = leg.texto.split("\n")
        # Se alguma linha estoura max_chars, dividir o bloco
        if max_linhas >= 2 and len(linhas) == 2 and len(linhas[1]) > max_chars:
            # Dividir em 2 blocos, cada um com 1 ou 2 linhas que cabem
            todas_palavras = leg.texto.replace("\n", " ").split()
            # Dividir no meio temporal
            meio = len(todas_palavras) // 2
            texto1 = _quebrar_linhas(" ".join(todas_palavras[:meio]), max_chars, max_linhas)
            texto2 = _quebrar_linhas(" ".join(todas_palavras[meio:]), max_chars, max_linhas)
            # Calcular timestamp do meio
            ts_meio = _interpolar_timestamp(leg.inicio, leg.fim, 0.5)
            legendas_final.append(Legenda(0, leg.inicio, ts_meio, texto1))
            legendas_final.append(Legenda(0, ts_meio, leg.fim, texto2))
        else:
            legendas_final.append(leg)

    srt_corrigido = _legendas_para_srt(legendas_final)
    saida = TEMP_DIR / f"{srt_path.stem}_fixed.srt"
    saida.write_text(srt_corrigido, encoding="utf-8")
    return str(saida)


def listar_regras(idioma: str) -> dict:
    """Retorna todas as regras de um idioma."""
    arquivo = RULES_DIR / f"{idioma}.json"
    if not arquivo.exists():
        return {"_global": {"substituicoes": {}, "max_chars_linha": 42, "remover_hesitacoes": True, "capitalizar_inicio": True}}
    with open(arquivo, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_regras(idioma: str, template_id: str, regras: dict):
    """Salva regras de um template específico."""
    arquivo = RULES_DIR / f"{idioma}.json"
    if arquivo.exists():
        with open(arquivo, "r", encoding="utf-8") as f:
            todas = json.load(f)
    else:
        todas = {"_global": {"substituicoes": {}, "max_chars_linha": 42, "remover_hesitacoes": True, "capitalizar_inicio": True}}

    todas[template_id] = regras
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(todas, f, ensure_ascii=False, indent=2)
