# -*- coding: utf-8 -*-
"""Detectores de e-mail, telefone, IMEI e CPF (regras determinísticas).

Cada candidato carrega o código da regra que o aceitou (ex.: R-TEL-03) e o
motivo em português. Números que pareciam um dos tipos mas falharam numa
validação vão para a lista de descartados, com o motivo.

Princípios:
  * Um número só é extraído se estiver isolado: não pode estar "colado" em
    letras (ex.: dentro de um ID alfanumérico) nem fazer parte de uma sequência
    numérica maior (ex.: chave de NF-e, linha digitável, ID de transação).
  * Formatação explícita conta como evidência: "(81) 98765-4321" é telefone e
    "123.456.789-09" é CPF, independentemente do contexto.
  * Números sem formatação usam a palavra-chave mais próxima ANTES deles
    (mesma linha, fim da linha anterior ou rótulo da coluna da planilha).
"""
import re

from .regras import (DDD_UF, DDDS, DDI, DOMINIOS_DESCARTAVEIS, EXTENSOES_ARQUIVO,
                     RBI_AUTOMATICO)

OK, ATENCAO, INVALIDO = 'ok', 'atencao', 'invalido'
ORDEM_STATUS = {OK: 0, ATENCAO: 1, INVALIDO: 2}


class Candidato(object):
    __slots__ = ('tipo', 'chave', 'valor', 'formatos', 'status', 'motivo', 'regra',
                 'ini', 'fim', 'bruto', 'extra', 'alternativa', 'digitos')

    def __init__(self, tipo, chave, valor, formatos, status, motivo, regra,
                 ini=0, fim=0, bruto='', extra=None, digitos=''):
        self.tipo = tipo
        self.chave = chave
        self.valor = valor
        self.formatos = formatos
        self.status = status
        self.motivo = motivo
        self.regra = regra
        self.ini = ini
        self.fim = fim
        self.bruto = bruto
        self.extra = extra or {}
        self.alternativa = None
        self.digitos = digitos

    def __repr__(self):
        return '<%s %s %s %s>' % (self.tipo, self.valor, self.status, self.regra)


class Descarte(object):
    __slots__ = ('tipo', 'bruto', 'motivo', 'regra', 'ini', 'fim')

    def __init__(self, tipo, bruto, motivo, regra, ini, fim):
        self.tipo, self.bruto, self.motivo, self.regra = tipo, bruto, motivo, regra
        self.ini, self.fim = ini, fim

    def __repr__(self):
        return '<descarte %s %r %s>' % (self.tipo, self.bruto, self.regra)


# ======================================================================= validações

def cpf_valido(d):
    if len(d) != 11 or not d.isdigit() or d == d[0] * 11:
        return False
    s = sum(int(d[i]) * (10 - i) for i in range(9))
    dv1 = (s * 10 % 11) % 10
    s = sum(int(d[i]) * (11 - i) for i in range(10))
    dv2 = (s * 10 % 11) % 10
    return d[9] == str(dv1) and d[10] == str(dv2)


def dv_luhn(corpo):
    """Dígito verificador Luhn para o corpo de 14 dígitos do IMEI (TAC + série)."""
    total = 0
    for i, c in enumerate(corpo):
        n = ord(c) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def fmt_cpf(d):
    return '%s.%s.%s-%s' % (d[:3], d[3:6], d[6:9], d[9:])


def fmt_imei(d15):
    return '%s-%s-%s-%s' % (d15[:2], d15[2:8], d15[8:14], d15[14:])


def _formatos_cpf(d):
    return {'formatado': fmt_cpf(d), 'digitos': d}


def _local(n):
    return '%s-%s' % (n[:-4], n[-4:])


def candidato_tel_br(ddd, n, status, motivo, regra, extra=None):
    nacional = '(%s) %s' % (ddd, _local(n))
    formatos = {
        'nacional': nacional,
        'internacional': '+55 %s %s' % (ddd, _local(n)),
        'e164': '+55%s%s' % (ddd, n),
        'digitos_ddi': '55%s%s' % (ddd, n),
        'digitos': '%s%s' % (ddd, n),
    }
    ex = {'uf': DDD_UF.get(int(ddd), '')}
    if extra:
        ex.update(extra)
    return Candidato('telefone', '55' + ddd + n, nacional, formatos, status, motivo, regra,
                     extra=ex, digitos=ddd + n)


def candidato_tel_local(n, status, motivo, regra):
    v = _local(n)
    formatos = dict.fromkeys(('nacional', 'internacional'), v)
    # sem DDD não há como montar DDI+DDD: os formatos "só dígitos" ficam só com o número local
    formatos.update(dict.fromkeys(('e164', 'digitos_ddi', 'digitos'), n))
    return Candidato('telefone', 'L:' + n, v, formatos, status, motivo, regra,
                     extra={'sem_ddd': True}, digitos=n)


def _candidato_tel_intl(cc, resto, status, motivo, regra):
    if cc == '1' and len(resto) == 10:
        v = '+1 %s %s %s' % (resto[:3], resto[3:6], resto[6:])
    else:
        v = '+%s %s' % (cc, resto)
    formatos = {'nacional': v, 'internacional': v, 'e164': '+' + cc + resto,
                'digitos_ddi': cc + resto, 'digitos': cc + resto}
    return Candidato('telefone', cc + resto, v, formatos, status, motivo, regra,
                     extra={'pais': DDI.get(cc, '')}, digitos=cc + resto)


def _classe_numero(n):
    """'celular' | 'fixo' | 'celular_antigo' | None para o número local (8/9 dígitos)."""
    if len(n) == 9:
        return 'celular' if n[0] == '9' else None
    if len(n) == 8:
        if n[0] in '2345':
            return 'fixo'
        if n[0] in '6789':
            return 'celular_antigo'
    return None


def _ddd_ok(ddd):
    return len(ddd) == 2 and ddd.isdigit() and int(ddd) in DDDS


# ======================================================================= e-mail

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9_%+\-](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9_%+\-])?)"
    r"@((?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24})"
    r"(?![A-Za-z0-9\-])")

_PONTO = r'(?:\[\s*(?:dot|ponto|\.)\s*\]|\(\s*(?:dot|ponto|\.)\s*\)|\{\s*(?:dot|ponto)\s*\}|\s+ponto\s+|\s+dot\s+|\.)'
_EMAIL_OFUSCADO = re.compile(
    r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9][A-Za-z0-9._%+\-]{0,63})\s*"
    r"(?:\[\s*(?:at|arroba|@)\s*\]|\(\s*(?:at|arroba|@)\s*\)|\{\s*(?:at|arroba)\s*\}|\s+arroba\s+)\s*"
    r"([A-Za-z0-9\-]+(?:\s*" + _PONTO + r"\s*[A-Za-z0-9\-]+)+)", re.I)
_PONTO_RE = re.compile(r'\s*' + _PONTO + r'\s*', re.I)


def _cand_email(local, dominio, ini, fim, bruto, ofuscado):
    dominio = dominio.lower()
    valor = '%s@%s' % (local, dominio)
    chave = valor.lower()
    tld = dominio.rsplit('.', 1)[-1]
    if '..' in local or local.startswith('.') or local.endswith('.'):
        return None, Descarte('email', bruto, 'Parte local com ponto inválido', 'R-EML-90', ini, fim)
    if tld in EXTENSOES_ARQUIVO and not ofuscado:
        return None, Descarte('email', bruto, 'Termina em extensão de arquivo (.%s): provável nome de arquivo' % tld,
                              'R-EML-91', ini, fim)
    obs = []
    status, regra = OK, 'R-EML-01'
    if ofuscado:
        status, regra = ATENCAO, 'R-EML-02'
        obs.append('Escrito de forma ofuscada no original')
    if dominio in DOMINIOS_DESCARTAVEIS:
        status = ATENCAO
        obs.append('Domínio de e-mail temporário/descartável')
    formatos = {'original': valor, 'minusculo': chave}
    c = Candidato('email', chave, chave, formatos, status,
                  '; '.join(obs) or 'Sintaxe de e-mail válida', regra, ini, fim, bruto,
                  extra={'dominio': dominio})
    return c, None


def detectar_emails(texto):
    """Retorna (candidatos, descartes, spans_ocupados)."""
    cands, descs, spans = [], [], []
    if '@' in texto:
        for m in _EMAIL.finditer(texto):
            c, d = _cand_email(m.group(1), m.group(2), m.start(), m.end(), m.group(0), False)
            spans.append((m.start(), m.end()))
            (cands if c else descs).append(c or d)
    low = texto.lower()
    if 'at' in low or 'arroba' in low:
        for m in _EMAIL_OFUSCADO.finditer(texto):
            if any(a <= m.start() < b for a, b in spans):
                continue
            dominio = _PONTO_RE.sub('.', m.group(2))
            if not re.match(r'^(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,24}$', dominio):
                continue
            c, d = _cand_email(m.group(1), dominio, m.start(), m.end(), m.group(0), True)
            spans.append((m.start(), m.end()))
            (cands if c else descs).append(c or d)
    return cands, descs, spans


# ======================================================================= CPF mascarado

_MASC = '*•#xX'
_CPF_MASCARADO = re.compile(
    r'(?<![\w*•#])([\d*•#xX]{3})\.([\d*•#xX]{3})\.([\d*•#xX]{3})-([\d*•#xX]{2})(?![\w*•#])')


def detectar_cpf_mascarado(texto):
    cands, spans = [], []
    if '.' not in texto:
        return cands, spans
    for m in _CPF_MASCARADO.finditer(texto):
        bruto = m.group(0)
        mascara = ''.join('*' if ch in _MASC else ch for ch in ''.join(m.groups()))
        if '*' not in mascara or sum(ch.isdigit() for ch in mascara) < 3:
            continue
        valor = '%s.%s.%s-%s' % (mascara[:3], mascara[3:6], mascara[6:9], mascara[9:])
        c = Candidato('cpf', 'M:' + mascara, valor, {'formatado': valor, 'digitos': mascara},
                      ATENCAO, 'CPF parcialmente mascarado (dígitos visíveis: %d)' %
                      sum(ch.isdigit() for ch in mascara), 'R-CPF-06', m.start(), m.end(), bruto,
                      extra={'mascara': mascara}, digitos=mascara)
        cands.append(c)
        spans.append((m.start(), m.end()))
    return cands, spans


# ======================================================================= cadeias numéricas

_GRUPO = re.compile(r'\d+')
# Separadores aceitos entre grupos de dígitos de UM mesmo número.
_GAP_OK = re.compile(r'(?: ?[-./] ?| | ?\) ?(?:[-.] ?)?| ?\( ?)\Z')
_COLA = re.compile(r'[A-Za-z0-9_@À-ÿ]')


class _Grupo(object):
    __slots__ = ('d', 'ini', 'fim', 'gap', 'paren', 'plus', 'cola_ini', 'cola_fim', 'ini_real', 'fim_real')


def _cadeias(t):
    grupos = []
    for m in _GRUPO.finditer(t):
        g = _Grupo()
        g.d, g.ini, g.fim = m.group(), m.start(), m.end()
        g.gap, g.paren, g.plus, g.cola_ini, g.cola_fim = None, False, False, False, False
        g.ini_real, g.fim_real = g.ini, g.fim
        grupos.append(g)
    cadeias, atual = [], []
    for g in grupos:
        if atual:
            gap = t[atual[-1].fim:g.ini]
            if len(gap) <= 5 and _GAP_OK.match(gap):
                g.gap = gap
                atual.append(g)
                continue
            cadeias.append(atual)
        atual = [g]
    if atual:
        cadeias.append(atual)
    for c in cadeias:
        _marcar(c, t)
    return cadeias


def _marcar(c, t):
    n = len(c)
    # prefixo do primeiro grupo: "(", "( ", "+", "+ ", "+(" ...
    g0 = c[0]
    j = g0.ini
    abre = False
    k = j
    while k > 0 and t[k - 1] == ' ' and j - k < 2:
        k -= 1
    if k > 0 and t[k - 1] == '(':
        abre = True
        j = k - 1
        k = j
        while k > 0 and t[k - 1] == ' ' and j - k < 2:
            k -= 1
    else:
        k = j
        while k > 0 and t[k - 1] == ' ' and j - k < 2:
            k -= 1
    if k > 0 and t[k - 1] == '+':
        g0.plus = True
        j = k - 1
    g0.ini_real = j if (abre or g0.plus) else g0.ini
    antes = t[g0.ini_real - 1] if g0.ini_real > 0 else ''
    if antes and _COLA.match(antes):
        g0.cola_ini = True
    elif antes == ',' and g0.ini_real > 1 and t[g0.ini_real - 2].isdigit():
        g0.cola_ini = True
    for i, g in enumerate(c):
        abre_i = abre if i == 0 else g.gap.rstrip().endswith('(')
        if i + 1 < n:
            fecha = c[i + 1].gap.lstrip().startswith(')')
        else:
            resto = t[g.fim:g.fim + 2].lstrip()
            fecha = resto.startswith(')')
            if fecha:
                g.fim_real = t.index(')', g.fim) + 1
        g.paren = bool(abre_i and fecha)
    ult = c[-1]
    depois = t[ult.fim_real:ult.fim_real + 1]
    if depois and _COLA.match(depois):
        ult.cola_fim = True
    elif depois == ',' and t[ult.fim_real + 1:ult.fim_real + 2].isdigit():
        ult.cola_fim = True


def _inicio_ok(c, i):
    if i == 0:
        return not c[0].cola_ini
    gap = c[i].gap
    return ' ' in gap or '(' in gap


def _fim_ok(c, j):
    if j == len(c) - 1:
        return not c[j].cola_fim
    return ' ' in c[j + 1].gap


def _span(c, i, j):
    """Posições (início, fim) do trecho original que vai do grupo i ao j."""
    g = c[i]
    if i == 0:
        ini = g.ini_real
    elif '(' in g.gap:
        ini = c[i - 1].fim + g.gap.index('(')
    else:
        ini = g.ini
    if j == len(c) - 1:
        fim = c[j].fim_real
    elif c[j].paren:
        fim = c[j].fim + c[j + 1].gap.index(')') + 1
    else:
        fim = c[j].fim
    return ini, fim


# ----------------------------------------------------------------------- telefone (multi-grupo)

# Papéis: cc='55'; ddd=2 díg. (ou 3 com 0 na frente); n=8/9; n1=4/5 + n2=4; d9='9' + q=4 + q=4
_MODELOS_TEL = (
    ('cc', 'ddd', 'd9', 'q', 'q'),
    ('cc', 'ddd', 'n1', 'n2'),
    ('cc', 'ddd', 'n'),
    ('ddd', 'd9', 'q', 'q'),
    ('ddd', 'n1', 'n2'),
    ('ddd', 'n'),
    ('d9', 'q', 'q'),
    ('n1', 'n2'),
)
_SERVICO = ('0800', '0300', '0500', '0900')


def _papel_ok(papel, d):
    L = len(d)
    if papel == 'cc':
        return d == '55'
    if papel == 'n':
        return L in (8, 9)
    if papel == 'n1':
        return L in (4, 5)
    if papel in ('n2', 'q'):
        return L == 4
    if papel == 'd9':
        return d == '9'
    return False


def _tel_multi(c, i):
    """Tenta os modelos a partir do grupo i. Retorna dict ou None."""
    resto = len(c) - i
    for modelo in _MODELOS_TEL:
        k = len(modelo)
        if k > resto:
            continue
        j = i + k - 1
        if not _fim_ok(c, j):
            continue
        if c[i].plus and modelo[0] != 'cc':
            continue
        valido, ddd, partes, ddd_invalido = True, None, [], None
        for pos, (p, g) in enumerate(zip(modelo, c[i:j + 1])):
            if pos > 0:
                gap = g.gap
                # "(" só pode abrir o DDD; ")" só pode fechar o DDD
                if '(' in gap and p != 'ddd':
                    valido = False
                    break
                if ')' in gap and modelo[pos - 1] != 'ddd':
                    valido = False
                    break
            if g.paren and p != 'ddd':
                valido = False
                break
            if p == 'ddd':
                L = len(g.d)
                if L == 3 and g.d[0] == '0':
                    dd = g.d[1:]
                elif L == 2:
                    dd = g.d
                else:
                    valido = False
                    break
                if not _ddd_ok(dd):
                    ddd_invalido = dd
                ddd = dd
            elif not _papel_ok(p, g.d):
                valido = False
                break
            elif p != 'cc':
                partes.append(g.d)
        if not valido:
            continue
        n = ''.join(partes)
        classe = _classe_numero(n)
        if classe is None:
            continue
        tem_cc = modelo[0] == 'cc'
        paren_ddd = any(g.paren for g in c[i:j + 1])
        ult_gap = c[j].gap or ''
        forte = c[i].plus or tem_cc or paren_ddd or (k >= 2 and '-' in ult_gap and modelo[-1] in ('n2', 'q'))
        return {'k': k, 'ddd': ddd, 'n': n, 'classe': classe, 'forte': forte,
                'cc': tem_cc, 'ddd_invalido': ddd_invalido}
    return None


def _servico_multi(c, i):
    g = c[i]
    if g.d not in _SERVICO or g.plus or g.paren:
        return None
    if len(c) - i >= 3 and len(c[i + 1].d) == 3 and len(c[i + 2].d) == 4 and _fim_ok(c, i + 2):
        return 3, g.d + c[i + 1].d + c[i + 2].d
    if len(c) - i >= 2 and len(c[i + 1].d) == 7 and _fim_ok(c, i + 1):
        return 2, g.d + c[i + 1].d
    return None


def _cand_servico(d):
    v = '%s %s %s' % (d[:4], d[4:7], d[7:])
    formatos = dict.fromkeys(('nacional', 'internacional'), v)
    formatos.update(dict.fromkeys(('e164', 'digitos_ddi', 'digitos'), d))   # 0800 não tem DDI
    return Candidato('telefone', 'S:' + d, v, formatos, ATENCAO,
                     'Número de serviço (%s) — não é linha pessoal' % d[:4], 'R-TEL-07', digitos=d)


# ----------------------------------------------------------------------- IMEI

_MODELOS_IMEI = ((2, 6, 6, 1), (8, 6, 1), (6, 2, 6, 1), (2, 6, 6, 2), (8, 6, 2))


def _imei_multi(c, i):
    for modelo in _MODELOS_IMEI:
        k = len(modelo)
        if i + k > len(c):
            continue
        j = i + k - 1
        if not _fim_ok(c, j):
            continue
        if any(len(g.d) != L for g, L in zip(c[i:j + 1], modelo)):
            continue
        if any(g.paren for g in c[i:j + 1]) or c[i].plus:
            continue
        if any('(' in g.gap or ')' in g.gap for g in c[i + 1:j + 1]):
            continue
        return k, ''.join(g.d for g in c[i:j + 1])
    return None


def _avaliar_imei(d, ctx, formatado):
    """Regras R-IMEI. Retorna (Candidato|None, Descarte-motivo|None)."""
    tem_ctx = ctx == 'imei'
    if len(d) == 14:
        if not tem_ctx:
            return None, None
        dv = dv_luhn(d)
        return _cand_imei(d, d + dv, ATENCAO, 'IMEI informado com 14 dígitos (sem DV); DV calculado = %s' % dv,
                          'R-IMEI-04'), None
    if len(d) == 16:
        if not (tem_ctx or formatado):
            return None, None
        corpo = d[:14]
        dv = dv_luhn(corpo)
        return _cand_imei(corpo, corpo + dv, ATENCAO,
                          'IMEISV (versão de software %s); IMEI = %s' % (d[14:], corpo + dv), 'R-IMEI-05'), None
    if len(d) != 15:
        return None, None
    corpo, rbi = d[:14], d[:2]
    dv = dv_luhn(corpo)
    if d.startswith('724') and not tem_ctx:
        return None, ('Provável IMSI (MCC 724 = Brasil), não IMEI', 'R-IMEI-91')
    if dv == d[14]:
        if rbi in RBI_AUTOMATICO:
            return _cand_imei(corpo, d, OK, 'Luhn válido; TAC %s' % d[:8], 'R-IMEI-01'), None
        if tem_ctx:
            return _cand_imei(corpo, d, ATENCAO, 'Luhn válido, mas prefixo %s é incomum para IMEI' % rbi,
                              'R-IMEI-06'), None
        return None, ('15 dígitos com Luhn válido, mas prefixo %s não é de IMEI e não há contexto "IMEI"' % rbi,
                      'R-IMEI-92')
    if d[14] == '0' and (rbi in RBI_AUTOMATICO or tem_ctx):
        return _cand_imei(corpo, corpo + dv, ATENCAO,
                          'DV substituído por 0 (padrão de registros de operadora); DV correto = %s' % dv,
                          'R-IMEI-02'), None
    if tem_ctx:
        return _cand_imei(corpo, d, INVALIDO, 'Dígito verificador não confere (esperado %s)' % dv,
                          'R-IMEI-03'), None
    if rbi in RBI_AUTOMATICO:
        return None, ('15 dígitos com prefixo de IMEI, mas Luhn não confere e não há contexto "IMEI"', 'R-IMEI-93')
    return None, None


def _cand_imei(corpo, valor15, status, motivo, regra):
    formatos = {'imei15': valor15, 'imei14': corpo, 'formatado': fmt_imei(valor15)}
    return Candidato('imei', corpo, valor15, formatos, status, motivo, regra,
                     extra={'tac': corpo[:8]}, digitos=valor15)


# ----------------------------------------------------------------------- grupo único

def _grupo_unico(d, ctx, kw, plus):
    """Número sem separadores internos. Retorna (Candidato|None, (motivo, regra)|None)."""
    L = len(d)
    # DDI explícito com "+"
    if plus:
        return _internacional(d)
    if L in (14, 16):
        return _avaliar_imei(d, ctx, False)
    if L == 15:
        if d.startswith('0055'):
            return _tel_de_digitos(d[4:], ctx, kw, forte=True, origem='0055')
        return _avaliar_imei(d, ctx, False)
    if L == 11:
        if d[:4] in _SERVICO:
            return _cand_servico(d), None
        return _onze(d, ctx, kw)
    if L in (9, 10) and ctx == 'cpf':
        p = d.zfill(11)
        if cpf_valido(p):
            return Candidato('cpf', p, fmt_cpf(p), _formatos_cpf(p), ATENCAO,
                             'Zero(s) à esquerda restaurado(s) (%d dígitos no original)' % L,
                             'R-CPF-05', digitos=p), None
        return None, ('Contexto de CPF, mas nem com zeros à esquerda o DV confere', 'R-CPF-91')
    if L == 10:
        return _tel_de_digitos(d, ctx, kw, forte=False)
    if L in (12, 13):
        if d.startswith('55'):
            return _tel_de_digitos(d[2:], ctx, kw, forte=True, origem='55')
        if d[0] == '0' and L == 12:
            return _tel_de_digitos(d[1:], ctx, kw, forte=True, origem='0')
        return None, None
    if L in (8, 9):
        if ctx != 'tel':
            return None, None
        classe = _classe_numero(d)
        if classe is None:
            return None, ('Contexto de telefone, mas %s não é número local válido' % d, 'R-TEL-91')
        return candidato_tel_local(d, ATENCAO, 'Sem DDD (%s)' % classe.replace('_', ' '), 'R-TEL-05'), None
    return None, None


def _onze(d, ctx, kw):
    cpf_ok = cpf_valido(d)
    if d[0] == '0':
        tel_ok = _ddd_ok(d[1:3]) and _classe_numero(d[3:]) is not None
        tel = (d[1:3], d[3:])
    else:
        tel_ok = _ddd_ok(d[:2]) and _classe_numero(d[2:]) == 'celular'
        tel = (d[:2], d[2:])
    if ctx == 'cpf':
        if cpf_ok:
            return _cand_cpf(d, OK, 'DV válido; contexto "%s"' % kw, 'R-CPF-03'), None
        return _cand_cpf(d, INVALIDO, 'Contexto "%s", mas o DV não confere' % kw, 'R-CPF-03'), None
    if ctx == 'tel':
        if tel_ok and d[0] != '0':
            nota = '; também passaria no DV de CPF' if cpf_ok else ''
            return candidato_tel_br(tel[0], tel[1], OK, 'Celular sem formatação; contexto "%s"%s' % (kw, nota),
                                    'R-TEL-03'), None
        if tel_ok:
            return candidato_tel_br(tel[0], tel[1], OK, 'Com 0 de longa distância; contexto "%s"' % kw,
                                    'R-TEL-04'), None
        if cpf_ok:
            return _cand_cpf(d, ATENCAO, 'Contexto "%s" sugere telefone, mas só o DV de CPF confere' % kw,
                             'R-CPF-04'), None
        return None, ('Contexto "%s", mas 11 dígitos sem estrutura de telefone' % kw, 'R-TEL-92')
    if ctx == 'neg':
        if cpf_ok:
            return _cand_cpf(d, ATENCAO, 'DV de CPF válido, mas o contexto é "%s"' % kw, 'R-CPF-04'), None
        if tel_ok:
            return None, ('Estrutura de celular, mas o contexto é "%s"' % kw, 'R-TEL-93')
        return None, None
    if ctx == 'imei':
        return None, None
    # sem contexto
    if d[0] == '0':
        tel_ok = False   # fixo com 0 exige contexto
    if cpf_ok and tel_ok:
        c = _cand_cpf(d, ATENCAO, 'Ambíguo: DV de CPF válido e também é celular válido', 'R-AMB-01')
        c.alternativa = candidato_tel_br(tel[0], tel[1], ATENCAO,
                                         'Ambíguo: celular válido e também passa no DV de CPF', 'R-AMB-01')
        return c, None
    if cpf_ok:
        return _cand_cpf(d, OK, 'DV válido (11 dígitos sem formatação)', 'R-CPF-04'), None
    if tel_ok:
        return candidato_tel_br(tel[0], tel[1], OK, 'Celular sem formatação (DDD + 9 dígitos)', 'R-TEL-03'), None
    return None, None


def _tel_de_digitos(nac, ctx, kw, forte, origem=''):
    """nac = DDD + número (10 ou 11 dígitos)."""
    ddd, n = nac[:2], nac[2:]
    classe = _classe_numero(n)
    if not _ddd_ok(ddd) or classe is None:
        if forte:
            return None, ('%s seguido de número inválido (DDD %s)' % (origem or 'DDI', ddd), 'R-TEL-94')
        return None, None
    if ctx == 'neg' and not forte:
        return None, None   # 10 dígitos sem formatação já exigiriam contexto de telefone
    if forte:
        regra = 'R-TEL-02'
        if classe == 'celular':
            return candidato_tel_br(ddd, n, OK, 'Com código do país (%s)' % origem, regra), None
        if classe == 'fixo':
            return candidato_tel_br(ddd, n, OK, 'Fixo com código do país (%s)' % origem, regra), None
        return candidato_tel_br(ddd, n, ATENCAO, 'Celular no formato antigo (8 dígitos, sem o 9)', regra), None
    # 10 dígitos sem formatação: só com contexto de telefone (evita timestamps e outros IDs)
    if ctx != 'tel':
        return None, None
    if classe == 'fixo':
        return candidato_tel_br(ddd, n, OK, 'Fixo sem formatação; contexto "%s"' % kw, 'R-TEL-04'), None
    return candidato_tel_br(ddd, n, ATENCAO, 'Celular no formato antigo (8 dígitos); contexto "%s"' % kw,
                            'R-TEL-04'), None


def _internacional(d):
    if d.startswith('55'):
        nac = d[2:]
        if len(nac) in (10, 11):
            return _tel_de_digitos(nac, None, None, forte=True, origem='+55')
        return None, ('+55 seguido de %d dígitos (esperado 10 ou 11)' % len(nac), 'R-TEL-94')
    cc = next((d[:k] for k in (3, 2, 1) if d[:k] in DDI), None)
    if cc is None:
        return None, ('Código de país inexistente', 'R-TEL-95')
    resto = d[len(cc):]
    if not (8 <= len(d) <= 15) or len(resto) < 4:
        return None, ('Comprimento incompatível com número internacional', 'R-TEL-95')
    pais = DDI[cc]
    if cc == '1':
        if len(resto) == 10 and resto[0] in '23456789' and resto[3] in '23456789':
            return _candidato_tel_intl(cc, resto, OK, 'Internacional — %s' % pais, 'R-TEL-06'), None
        return _candidato_tel_intl(cc, resto, ATENCAO, 'Internacional — %s; formato fora do padrão NANP' % pais,
                                   'R-TEL-06'), None
    return _candidato_tel_intl(cc, resto, ATENCAO, 'Internacional — %s; formato não validado' % pais,
                               'R-TEL-06'), None


def _cand_cpf(d, status, motivo, regra):
    return Candidato('cpf', d, fmt_cpf(d), _formatos_cpf(d), status, motivo, regra, digitos=d)


# ----------------------------------------------------------------------- varredura

def detectar_numeros(texto, ctx_fn, ocupados):
    """Percorre as cadeias de dígitos do texto.

    ctx_fn(posicao) -> (categoria, palavra). ocupados: spans já usados (e-mail etc.).
    Retorna (candidatos, descartes).
    """
    cands, descs = [], []

    def livre(a, b):
        return not any(a < y and x < b for x, y in ocupados)

    for c in _cadeias(texto):
        n = len(c)
        i = 0
        while i < n:
            if not _inicio_ok(c, i):
                i += 1
                continue
            res = _tentar(c, i, texto, ctx_fn)
            if res is None:
                i += 1
                continue
            k, cand, desc = res
            ini, fim = _span(c, i, i + k - 1)
            bruto = texto[ini:fim]
            if livre(ini, fim):
                if cand is not None:
                    cand.ini, cand.fim, cand.bruto = ini, fim, bruto
                    if cand.alternativa is not None:
                        a = cand.alternativa
                        a.ini, a.fim, a.bruto = ini, fim, bruto
                    cands.append(cand)
                elif desc is not None:
                    tipo = desc[2] if len(desc) > 2 else 'numero'
                    descs.append(Descarte(tipo, bruto, desc[0], desc[1], ini, fim))
            i += k
    return cands, descs


def _tipo_regra(regra):
    return {'CPF': 'cpf', 'TEL': 'telefone', 'IMEI': 'imei'}.get(regra.split('-')[1], 'numero')


def _tentar(c, i, texto, ctx_fn):
    """Retorna (k_grupos, Candidato|None, (motivo, regra, tipo)|None) ou None."""
    g = c[i]
    resto = len(c) - i
    # 1. CPF formatado 000.000.000-00 ou 000000000-00
    if resto >= 4 and [len(x.d) for x in c[i:i + 4]] == [3, 3, 3, 2] and \
            [x.gap for x in c[i + 1:i + 4]] == ['.', '.', '-'] and _fim_ok(c, i + 3) and not g.plus:
        d = ''.join(x.d for x in c[i:i + 4])
        return (4,) + _cpf_formatado(d)
    if resto >= 2 and len(g.d) == 9 and len(c[i + 1].d) == 2 and c[i + 1].gap == '-' and _fim_ok(c, i + 1) \
            and not g.plus:
        return (2,) + _cpf_formatado(g.d + c[i + 1].d)
    # 2. serviço 0800
    sv = _servico_multi(c, i)
    if sv:
        return sv[0], _cand_servico(sv[1]), None
    # 3. IMEI com separadores
    im = _imei_multi(c, i)
    if im:
        k, d = im
        ctx, kw = ctx_fn(_span(c, i, i)[0])
        cand, desc = _avaliar_imei(d, ctx, True)
        if cand or desc:
            return k, cand, (desc + ('imei',)) if desc else None
    # 4. internacional com "+"
    if g.plus:
        k, total = 0, ''
        for x in c[i:]:
            if len(total) + len(x.d) > 15:
                break
            total += x.d
            k += 1
        if k == 0:
            return None
        while k > 1 and not _fim_ok(c, i + k - 1):
            total = total[:-len(c[i + k - 1].d)]
            k -= 1
        if total.startswith('55') and k > 1:
            tm = _tel_multi(c, i)
            if tm and tm['cc']:
                return _resultado_multi(c, i, tm, ctx_fn)
        cand, desc = _internacional(total)
        return k, cand, (desc + ('telefone',)) if desc else None
    # 5. telefone com separadores
    tm = _tel_multi(c, i)
    if tm:
        return _resultado_multi(c, i, tm, ctx_fn)
    # 6. grupo único
    if not _fim_ok(c, i):
        return None
    ctx, kw = ctx_fn(_span(c, i, i)[0])
    cand, desc = _grupo_unico(g.d, ctx, kw, False)
    if cand is None and desc is None:
        return None
    if desc:
        desc = desc + (_tipo_regra(desc[1]),)
    return 1, cand, desc


def _cpf_formatado(d):
    """Retorna (Candidato|None, descarte|None)."""
    if cpf_valido(d):
        return _cand_cpf(d, OK, 'Formatado como CPF; DV válido', 'R-CPF-01'), None
    if d == d[0] * 11:
        return None, ('CPF com todos os dígitos iguais (preenchimento/placeholder)', 'R-CPF-92', 'cpf')
    return _cand_cpf(d, INVALIDO, 'Formatado como CPF, mas o DV não confere', 'R-CPF-02'), None


def _resultado_multi(c, i, tm, ctx_fn):
    k = tm['k']
    ini = _span(c, i, i)[0]
    ctx, kw = ctx_fn(ini)
    if tm['ddd_invalido']:
        if tm['forte']:
            return k, None, ('DDD %s inexistente' % tm['ddd_invalido'], 'R-TEL-94', 'telefone')
        return None
    ddd, n, classe, forte = tm['ddd'], tm['n'], tm['classe'], tm['forte']
    if ddd is None:
        # sem DDD
        if ctx == 'neg':
            return k, None, ('Formato de telefone sem DDD, mas o contexto é "%s"' % kw, 'R-TEL-93', 'telefone')
        if classe == 'celular' and (forte or ctx == 'tel'):
            return k, candidato_tel_local(n, ATENCAO, 'Sem DDD (celular)', 'R-TEL-05'), None
        if ctx == 'tel':
            return k, candidato_tel_local(n, ATENCAO, 'Sem DDD (%s)' % classe.replace('_', ' '), 'R-TEL-05'), None
        return None
    if tm['cc']:
        regra, base = 'R-TEL-02', 'Com código do país (+55)'
    else:
        regra, base = 'R-TEL-01', 'Formatado com DDD'
    if classe == 'celular':
        return k, candidato_tel_br(ddd, n, OK, base, regra), None
    if classe == 'fixo':
        if forte or ctx == 'tel':
            return k, candidato_tel_br(ddd, n, OK, base + ' (fixo)', regra), None
        return None
    # celular antigo (8 dígitos começando por 6-9)
    if forte or ctx == 'tel':
        return k, candidato_tel_br(ddd, n, ATENCAO, 'Celular no formato antigo (8 dígitos, sem o 9)', regra), None
    return None
