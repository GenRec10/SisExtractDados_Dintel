# -*- coding: utf-8 -*-
"""Normalização de texto aplicada antes da detecção."""
import re
import unicodedata

# Caracteres invisíveis usados (às vezes de propósito) para "quebrar" números e e-mails.
_INVISIVEIS = dict.fromkeys(map(ord, '​‌‍⁠﻿­'), None)
# Espaços e traços tipográficos -> equivalentes ASCII.
_TROCAS = {}
for _c in '              　':
    _TROCAS[ord(_c)] = ' '
for _c in '‐‑‒–—―−﹘﹣－':
    _TROCAS[ord(_c)] = '-'
# Tabulação separa colunas: vira fronteira dura (nenhum dado atravessa " | ").
_TROCAS[ord('\t')] = ' | '
_TROCAS.update(_INVISIVEIS)

_ESPACOS = re.compile(r' {2,}')


def limpar_unidade(texto):
    """Normaliza uma unidade de texto (linha ou célula) mantendo o conteúdo legível.

    - NFKC (dígitos de largura total, ligaduras 'ﬁ' -> 'fi');
    - remove caracteres invisíveis (zero-width, soft hyphen);
    - espaços/traços tipográficos -> ' ' e '-';
    - colapsa espaços repetidos.
    """
    if not texto:
        return ''
    t = unicodedata.normalize('NFKC', texto).translate(_TROCAS)
    t = _ESPACOS.sub(' ', t)
    return t.strip()


def sem_acento(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', texto)
                   if not unicodedata.combining(c))


def _tabelas():
    """Tabelas de tradução (rápidas) letra acentuada -> letra base, preservando o comprimento."""
    minus, maius = {}, {}
    for faixa in (range(0x41, 0x5B), range(0x61, 0x7B), range(0xC0, 0x250), range(0x1E00, 0x1F00)):
        for cp in faixa:
            ch = chr(cp)
            base = sem_acento(ch)
            if len(base) != 1:
                continue
            b_min, b_mai = base.lower(), base.upper()
            if len(b_min) == 1 and b_min != ch:
                minus[cp] = b_min
            if len(b_mai) == 1 and b_mai != ch:
                maius[cp] = b_mai
    return minus, maius


_TAB_MIN, _TAB_MAI = _tabelas()


def chave_texto(texto):
    """Forma comparável: sem acento, maiúsculas."""
    t = texto.translate(_TAB_MAI)
    if t.isascii():
        return t
    return sem_acento(t).upper()


def minusculo_sem_acento(texto):
    t = texto.translate(_TAB_MIN)
    if t.isascii():
        return t
    return sem_acento(t).lower()


def minusculo_mesmo_tamanho(texto):
    """Minúsculas sem acento com o MESMO comprimento do original (posições continuam válidas).

    Letras fora das faixas latinas ficam como estão (não afetam as regras)."""
    return texto.translate(_TAB_MIN)
