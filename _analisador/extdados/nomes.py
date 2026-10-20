# -*- coding: utf-8 -*-
"""Detecção de nomes próprios de pessoas por regras e dicionário (sem IA).

Duas regras, nesta ordem:

R-NOM-01 (rótulo)      O texto vem depois de um rótulo de nome ("Nome:", "Titular:",
                       "Favorecido -") ou numa coluna cujo cabeçalho é um rótulo de nome.
R-NOM-02 / R-NOM-03    Varredura por dicionário: a sequência começa num PRENOME
(dicionário)           frequente do Censo 2010 (IBGE) e segue por palavras com
                       inicial maiúscula, conectivos (de, da, dos, e...) e iniciais.
                       R-NOM-02 = há ao menos um sobrenome/nome conhecido depois do
                       prenome; R-NOM-03 = o restante não está nas listas (atenção).

Fronteiras: um nome nunca atravessa quebra de linha, célula, pontuação, número
ou palavra da lista de bloqueio (cargos, instituições, logradouros, campos de
formulário, UFs etc.). Palavra seguida de ":" é tratada como rótulo e encerra o nome.

Limitação assumida: nomes cujo primeiro nome é raro (fora dos ~8 mil prenomes
com frequência >= 1.000 no Censo) só são achados pela regra de rótulo.
"""
import re

from .detectores import ATENCAO, OK, Candidato, Descarte
from .normaliza import chave_texto, minusculo_mesmo_tamanho
from .regras import (CONECTIVOS, ROTULO_NOME, ROTULO_NOME_EXCLUIDO, listas_nomes,
                     municipios)

_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*\.?")
_GAP_SUAVE = re.compile(r' {1,3}\Z')
_FRONTEIRA_DURA = set(',;:|()[]{}<>/\\"“”«»=+*#!?…\n\r\t')

LOGRADOUROS = frozenset((
    'RUA', 'AV', 'AVENIDA', 'TRAVESSA', 'TV', 'PRACA', 'ALAMEDA', 'ESTRADA', 'RODOVIA',
    'LOTEAMENTO', 'CONJUNTO', 'CONDOMINIO', 'EDIFICIO', 'BAIRRO', 'VILA', 'SITIO', 'FAZENDA',
    'ESCOLA', 'COLEGIO', 'HOSPITAL', 'CLINICA', 'IGREJA', 'FUNDACAO', 'INSTITUTO', 'EMPRESA',
    'LOJA', 'FARMACIA', 'DROGARIA', 'UNIVERSIDADE', 'FACULDADE', 'MERCADO', 'SUPERMERCADO',
    'PARQUE', 'PONTE', 'VIADUTO', 'PRAIA', 'LAGOA', 'ESTADIO', 'SHOPPING', 'AEROPORTO',
    'TERMINAL', 'CENTRO', 'FORUM', 'MATERNIDADE', 'UPA', 'POSTO', 'CRECHE', 'ESTACAO',
    'COMUNIDADE', 'LARGO', 'BECO', 'PASSAGEM', 'VIA', 'CAMPUS', 'MUSEU', 'TEATRO',
    'CAPELA', 'PAROQUIA', 'TEMPLO', 'ASSOCIACAO', 'SINDICATO', 'COOPERATIVA', 'BATALHAO',
    'DELEGACIA', 'PRESIDIO', 'PENITENCIARIA', 'CADEIA', 'UNIDADE', 'BLOCO', 'QUADRA'))

VALORES_VAZIOS = frozenset((
    'NAO INFORMADO', 'NAO INFORMADA', 'NAO CONSTA', 'NAO IDENTIFICADO', 'NAO IDENTIFICADA',
    'SEM NOME', 'SEM INFORMACAO', 'DESCONHECIDO', 'DESCONHECIDA', 'IGNORADO', 'IGNORADA',
    'NAO SE APLICA', 'A DEFINIR', 'NAO DISPONIVEL', 'NAO HA', 'NADA CONSTA'))

_REF_MUNICIPIO_DEPOIS = re.compile(r'\s*(?:/|-|–|\()\s*[A-Z]{2}\b\)?')
_REF_MUNICIPIO_ANTES = re.compile(
    r'(?:\bem|\bde|\bdo|\bda|\bpara|\bcidade|\bmunicipio|\bcomarca|\bnatural)\s*(?:de\s*)?$', re.I)


# --------------------------------------------------------------------- utilidades

def _palavra(tok):
    return tok[:-1] if tok.endswith('.') else tok


def _chave_tok(tok):
    return chave_texto(_palavra(tok)).replace('’', "'")


def titulo(nome):
    partes = nome.split(' ')
    saida = []
    for i, p in enumerate(partes):
        chave = chave_texto(p.rstrip('.'))
        if i > 0 and chave in CONECTIVOS:
            saida.append(p.lower())
        elif len(p.rstrip('.')) == 1:
            saida.append(p.upper())
        elif re.match(r"^[dD]['’]", p) and len(p) > 2:
            saida.append(p[0].upper() + p[1] + p[2].upper() + p[3:].lower())
        else:
            saida.append(p[:1].upper() + p[1:].lower())
    return ' '.join(saida)


def chave_nome(nome):
    toks = [chave_texto(_palavra(t)) for t in nome.split()]
    return ' '.join(t for t in toks if t)


def _limpar_nome(texto):
    t = re.sub(r'\s+', ' ', texto).strip()
    t = t.rstrip('.,;:- ')
    return t


def _formatos(nome):
    return {'original': nome, 'maiusculas': nome.upper(), 'titulo': titulo(nome)}


def _municipio(chave):
    return municipios().get(chave)


# --------------------------------------------------------------------- classificação de tokens

class _Tok(object):
    __slots__ = ('txt', 'ini', 'fim', 'chave', 'classe', 'conhecido', 'ponto', 'bloq')


def _tokens(texto, ocupados):
    iniciais, conhecidos, bloqueio = listas_nomes()
    toks = []
    if ocupados:
        # trechos já usados (e-mails, números) viram fronteira dura, sem mudar as posições
        partes, ult = [], 0
        for x, y in sorted(ocupados):
            if x < ult:
                x = ult
            if y <= x:
                continue
            partes.append(texto[ult:x])
            partes.append('|' * (y - x))
            ult = y
        partes.append(texto[ult:])
        texto = ''.join(partes)
    for m in _TOKEN.finditer(texto):
        a, b = m.start(), m.end()
        t = _Tok()
        t.txt, t.ini, t.fim = m.group(), a, b
        palavra = _palavra(t.txt)
        t.ponto = t.txt.endswith('.')
        t.chave = _chave_tok(t.txt)
        t.conhecido = False
        t.bloq = t.chave in bloqueio
        if t.chave in CONECTIVOS and len(palavra) <= 5 and (palavra.islower() or palavra.isupper()
                                                            or palavra in ('De', 'Da', 'Do', 'Dos', 'Das')):
            t.classe = 'con'
        elif len(palavra) == 1 and palavra.isupper():
            t.classe = 'ini'
        elif re.match(r"^[dD]['’]\w", palavra) or palavra[:1].isupper():
            if t.chave in bloqueio:
                t.classe = 'blq'
            else:
                t.classe = 'cap' if not (palavra.isupper() and len(palavra) > 1) else 'CAP'
                base = t.chave.split("'")[-1]
                t.conhecido = t.chave in conhecidos or base in conhecidos
        else:
            t.classe = 'min'
        toks.append(t)
    return toks


def _gap_suave(texto, a, b):
    return bool(_GAP_SUAVE.match(texto[a:b]))


def _seguido_de_dois_pontos(texto, fim):
    resto = texto[fim:fim + 3].lstrip(' ')
    return resto.startswith(':')


# --------------------------------------------------------------------- regra de rótulo

def _valor_rotulado(texto, pos, ocupados):
    """Lê o nome que começa em `pos` (logo após 'Nome:'). Retorna (ini, fim) ou None."""
    _, _, bloqueio = listas_nomes()
    toks = [t for t in _tokens(texto, ocupados) if t.ini >= pos]
    if not toks or texto[pos:toks[0].ini].strip():
        return None
    sel = []
    tudo_minusculo = toks[0].classe == 'min'
    for idx, t in enumerate(toks):
        if idx > 0 and not _gap_suave(texto, toks[idx - 1].fim, t.ini):
            break
        if t.classe == 'blq' or (t.bloq and t.classe != 'con'):
            break
        if t.classe == 'min' and not tudo_minusculo:
            break
        if tudo_minusculo and t.classe in ('cap', 'CAP') and idx > 0:
            break
        if _seguido_de_dois_pontos(texto, t.fim):
            break
        sel.append(t)
        if t.ponto and len(_palavra(t.txt)) > 1:
            break
        if len(sel) >= 10:
            break
    while sel and sel[-1].classe == 'con':
        sel.pop()
    if not sel or sel[0].classe == 'con':
        return None
    palavras = [t for t in sel if t.classe != 'con' and len(_palavra(t.txt)) > 1]
    if len(palavras) < 2:
        return None
    # depois do nome não pode vir dígito colado na mesma "frase" (ex.: "Nome: MARIA 123")
    return sel[0].ini, sel[-1].fim


def detectar_nomes(texto, rotulo, ocupados):
    """Retorna (candidatos, descartes)."""
    cands, descs = [], []
    if not texto or not any(ch.isalpha() for ch in texto):
        return cands, descs
    usados = list(ocupados)

    # R-NOM-01a: coluna com cabeçalho de nome -> a célula inteira
    if rotulo:
        rot = minusculo_mesmo_tamanho(rotulo)
        if ROTULO_NOME.search(rot) and not ROTULO_NOME_EXCLUIDO.search(rot):
            c = _celula_nome(texto, rotulo)
            if c is not None:
                cands.append(c)
                return cands, descs

    # R-NOM-01b: "Nome: FULANO", "Titular - FULANO"
    baixo = minusculo_mesmo_tamanho(texto)
    for m in ROTULO_NOME.finditer(baixo):
        resto = texto[m.end():m.end() + 4]
        sep = re.match(r'\s*(?:[:\-–])\s*', resto)
        if not sep:
            continue
        janela = baixo[max(0, m.start() - 30):m.end()]
        if ROTULO_NOME_EXCLUIDO.search(janela):
            continue
        r = _valor_rotulado(texto, m.end() + sep.end(), usados)
        if r is None:
            continue
        ini, fim = r
        nome = _limpar_nome(texto[ini:fim])
        if chave_nome(nome) in VALORES_VAZIOS:
            continue
        c = _cand_nome(nome, OK, 'Após o rótulo "%s"' % texto[m.start():m.end()], 'R-NOM-01', ini, fim)
        _marcar_municipio(c, texto, ini, fim, descs)
        if c.status is not None:
            cands.append(c)
        usados.append((ini, fim))

    # R-NOM-02/03: dicionário
    c2, d2 = _varredura(texto, usados)
    cands.extend(c2)
    descs.extend(d2)
    return cands, descs


def _celula_nome(texto, rotulo):
    t = _limpar_nome(texto)
    if not t or any(ch.isdigit() for ch in t) or '@' in t:
        return None
    if chave_nome(t) in VALORES_VAZIOS:
        return None
    _, _, bloqueio = listas_nomes()
    toks = t.split(' ')
    if not 2 <= len(toks) <= 10:
        return None
    for tok in toks:
        if not _TOKEN.fullmatch(tok):
            return None
    chaves = [_chave_tok(x) for x in toks]
    if chaves[0] in CONECTIVOS or chaves[0] in bloqueio:
        return None
    if sum(1 for k in chaves if k not in CONECTIVOS and len(k) > 1) < 2:
        return None
    if any(k in bloqueio for k in chaves):
        return None
    c = _cand_nome(t, OK, 'Coluna "%s"' % rotulo.strip()[:40], 'R-NOM-01', 0, len(texto))
    m = _municipio(chave_nome(t))
    if m:
        c.status = ATENCAO
        c.motivo += '; coincide com nome de município (%s)' % ', '.join('%s/%s' % x for x in m[:3])
    return c


def _cand_nome(nome, status, motivo, regra, ini, fim):
    return Candidato('nome', chave_nome(nome), nome, _formatos(nome), status, motivo, regra,
                     ini, fim, nome)


def _marcar_municipio(c, texto, ini, fim, descs):
    """Nome igual ao de um município: descarta se o texto indicar lugar, senão sinaliza."""
    m = _municipio(c.chave)
    if not m:
        return
    antes = minusculo_mesmo_tamanho(texto[max(0, ini - 25):ini])
    depois = texto[fim:fim + 6]
    if _REF_MUNICIPIO_DEPOIS.match(depois) or _REF_MUNICIPIO_ANTES.search(antes.rstrip()):
        descs.append(Descarte('nome', c.valor, 'Nome de município (%s) usado como lugar' %
                              ', '.join('%s/%s' % x for x in m[:3]), 'R-NOM-91', ini, fim))
        c.status = None
        return
    c.status = ATENCAO
    c.motivo += '; coincide com nome de município (%s)' % ', '.join('%s/%s' % x for x in m[:3])


def _varredura(texto, ocupados):
    iniciais, conhecidos, bloqueio = listas_nomes()
    toks = _tokens(texto, ocupados)
    cands, descs = [], []
    n = len(toks)
    i = 0
    while i < n:
        t = toks[i]
        if t.classe not in ('cap', 'CAP') or t.chave not in iniciais:
            i += 1
            continue
        if _seguido_de_dois_pontos(texto, t.fim):
            i += 1
            continue
        run = [t]
        j = i + 1
        # fronteira natural (fim do texto, pontuação, palavra minúscula, ponto final)
        # x fim forçado (palavra bloqueada, rótulo com ":" ou limite de 10 palavras)
        fronteira_natural = True
        if not (t.ponto and len(_palavra(t.txt)) > 1):
            while j < n:
                u = toks[j]
                if len(run) >= 10:
                    fronteira_natural = False
                    break
                if not _gap_suave(texto, toks[j - 1].fim, u.ini):
                    break
                if u.classe == 'min':
                    break
                if u.classe == 'blq' or _seguido_de_dois_pontos(texto, u.fim):
                    fronteira_natural = False
                    break
                if u.classe == 'con' and u.chave == 'E' and j + 1 < n and toks[j + 1].chave in iniciais:
                    break   # "Maria e José": enumeração de duas pessoas
                run.append(u)
                j += 1
                if u.ponto and len(_palavra(u.txt)) > 1:
                    break
        run, cauda_incerta = _aparar(run, fronteira_natural)
        palavras = [u for u in run if u.classe in ('cap', 'CAP') and len(_palavra(u.txt)) > 1]
        if len(palavras) < 2:
            i += 1
            continue
        ini, fim = run[0].ini, run[-1].fim
        nome = _limpar_nome(texto[ini:fim])
        proximo = toks.index(run[-1]) + 1
        # logradouro/estabelecimento logo antes ("Rua Maria Quitéria", "Escola Estadual José ...")
        anteriores = toks[max(0, i - 2):i]
        if anteriores and _gap_suave(texto, anteriores[-1].fim, t.ini) and any(
                u.chave in LOGRADOUROS for u in anteriores):
            descs.append(Descarte('nome', nome, 'Precedido de "%s": nome de logradouro/estabelecimento' %
                                  next(u.txt for u in anteriores if u.chave in LOGRADOUROS), 'R-NOM-92', ini, fim))
            i = proximo
            continue
        resto_conhecido = any(u.conhecido for u in run[1:] if u.classe in ('cap', 'CAP'))
        if resto_conhecido and cauda_incerta:
            c = _cand_nome(nome, ATENCAO, 'Prenome do Censo + sobrenome conhecido; a última palavra '
                           '("%s") está fora das listas e pode não fazer parte do nome' % run[-1].txt,
                           'R-NOM-02', ini, fim)
        elif resto_conhecido:
            c = _cand_nome(nome, OK, 'Prenome do Censo + sobrenome conhecido', 'R-NOM-02', ini, fim)
        else:
            c = _cand_nome(nome, ATENCAO, 'Prenome do Censo; sobrenome fora das listas', 'R-NOM-03', ini, fim)
        _marcar_municipio(c, texto, ini, fim, descs)
        if c.status is not None:
            cands.append(c)
        i = proximo
    return cands, descs


def _aparar(run, fronteira_natural):
    """Remove do fim os conectivos e as palavras MAIÚSCULAS desconhecidas sem âncora.

    Palavra MAIÚSCULA desconhecida é aceita se houver depois dela um nome conhecido
    (sanduíche) ou se for a ÚLTIMA da sequência, uma só, e a sequência terminar numa
    fronteira natural. Em texto todo em maiúsculas isso impede que
    "JOAO DA SILVA ESTELIONATARIO RESIDENTE..." vire nome.
    """
    ancora = 0
    for idx, u in enumerate(run):
        if u.classe == 'ini' or u.classe == 'cap' or (u.classe == 'CAP' and u.conhecido):
            ancora = idx
    resto = run[ancora + 1:]
    desconhecidos = [u for u in resto if u.classe == 'CAP']
    corte = ancora + 1
    incerta = False
    if fronteira_natural and len(desconhecidos) == 1 and resto and resto[-1] is desconhecidos[0] \
            and len(_palavra(desconhecidos[0].txt)) >= 3:
        corte = len(run)
        incerta = True
    run = run[:corte]
    while run and run[-1].classe == 'con':
        run.pop()
    return run, incerta
