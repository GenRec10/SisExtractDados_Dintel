# -*- coding: utf-8 -*-
"""Pipeline: documentos -> unidades (linha/célula) -> candidatos -> resolução -> itens.

Saída pensada para a interface e para a exportação: cada item consolidado traz
valor normalizado, formatos de cópia, status, motivo, regras aplicadas,
número de ocorrências e amostras rastreáveis (arquivo, local, trecho).
"""
import re

from .detectores import (ATENCAO, INVALIDO, OK, ORDEM_STATUS, Candidato,
                         detectar_cpf_mascarado, detectar_emails, detectar_numeros)
from .nomes import detectar_nomes
from .normaliza import limpar_unidade, minusculo_sem_acento
from .regras import categoria_contexto

TIPOS = ('email', 'telefone', 'imei', 'cpf', 'nome')
ROTULO_TIPO = {'email': 'E-mail', 'telefone': 'Telefone', 'imei': 'IMEI', 'cpf': 'CPF', 'nome': 'Nome'}
MAX_AMOSTRAS = 30
JANELA_CONTEXTO = 40
_DIGITO_OU_ARROBA = re.compile(r'[\d@]')


class Unidade(object):
    __slots__ = ('texto', 'rotulo', 'local', 'anterior', 'coluna')

    def __init__(self, texto, rotulo, local, anterior, coluna):
        self.texto, self.rotulo, self.local, self.anterior, self.coluna = texto, rotulo, local, anterior, coluna


class Ocorrencia(object):
    __slots__ = ('cand', 'doc', 'local', 'trecho', 'a', 'b', 'coluna')

    def __init__(self, cand, doc, local, trecho, a, b, coluna):
        self.cand, self.doc, self.local, self.trecho, self.a, self.b, self.coluna = \
            cand, doc, local, trecho, a, b, coluna


# ===================================================================== unidades

def _letra_coluna(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _linha_cabecalho(linhas):
    """Linha de cabeçalho de uma tabela, ou None.

    Escolhe, entre as 30 primeiras linhas, a primeira em que: há pelo menos 2
    células preenchidas e pelo menos metade do maior número de células
    preenchidas das 50 primeiras linhas; toda célula tem letra, não tem '@',
    tem menos de 5 dígitos e até 60 caracteres.
    """
    def preenchidas(linha):
        return [c for c in (limpar_unidade(x) for x in linha if x) if c]
    maximo = max([len(preenchidas(l)) for l in linhas[:50]] or [0])
    minimo = max(2, (maximo + 1) // 2)
    for idx, linha in enumerate(linhas[:30]):
        cel = preenchidas(linha)
        if len(cel) < minimo:
            continue
        if all(any(ch.isalpha() for ch in c) and '@' not in c and len(c) <= 60
               and sum(ch.isdigit() for ch in c) < 5 for c in cel):
            return idx
    return None


def unidades(doc):
    for bi, b in enumerate(doc.blocos):
        prefixo = (b.nome + ', ') if b.nome else ''
        if b.tipo == 'texto':
            anterior = ''
            for n, linha in enumerate(b.texto.split('\n'), 1):
                t = limpar_unidade(linha)
                if not t:
                    continue
                yield Unidade(t, None, '%slinha %d' % (prefixo, n), anterior, None)
                anterior = t[-JANELA_CONTEXTO:]
        else:
            linhas = b.linhas
            hdr = _linha_cabecalho(linhas)
            cab = [limpar_unidade(c) if c else '' for c in linhas[hdr]] if hdr is not None else []
            for r, linha in enumerate(linhas):
                for ci, celula in enumerate(linha):
                    if not celula:
                        continue
                    t = limpar_unidade(celula)
                    if not t:
                        continue
                    rot = cab[ci] if (hdr is not None and r > hdr and ci < len(cab) and cab[ci]) else None
                    local = '%slinha %d, col. %s' % (prefixo, r + 1, _letra_coluna(ci))
                    if rot:
                        local += ' (%s)' % (rot if len(rot) <= 30 else rot[:29] + '…')
                    yield Unidade(t, rot, local, '', (doc.id, bi, ci))


# ===================================================================== detecção por unidade

def _ctx_factory(u):
    texto = u.texto
    cache = {}

    def ctx(pos):
        if pos in cache:
            return cache[pos]
        janela = texto[max(0, pos - JANELA_CONTEXTO):pos]
        antes = texto[:pos]
        cortou = _DIGITO_OU_ARROBA.search(antes) is not None
        ult = None
        for m in _DIGITO_OU_ARROBA.finditer(janela):
            ult = m
        if ult is not None:
            janela = janela[ult.end():]
        cat, kw = categoria_contexto(minusculo_sem_acento(janela))
        if cat is None and not cortou and u.anterior:
            ant = u.anterior
            ult = None
            for m in _DIGITO_OU_ARROBA.finditer(ant):
                ult = m
            if ult is not None:
                ant = ant[ult.end():]
            cat, kw = categoria_contexto(minusculo_sem_acento(ant))
        if cat is None and u.rotulo:
            cat, kw = categoria_contexto(minusculo_sem_acento(u.rotulo))
        cache[pos] = (cat, kw)
        return cat, kw
    return ctx


def detectar_unidade(u, com_nomes=True):
    """Retorna (candidatos, descartes) de uma unidade de texto."""
    texto = u.texto
    cands, descs = [], []
    em, de, ocup = detectar_emails(texto)
    cands += em
    descs += de
    cm, ocm = detectar_cpf_mascarado(texto)
    cands += cm
    ocup = ocup + ocm
    if any(ch.isdigit() for ch in texto):
        cn, dn = detectar_numeros(texto, _ctx_factory(u), ocup)
        cands += cn
        descs += dn
        ocup = ocup + [(c.ini, c.fim) for c in cn] + [(d.ini, d.fim) for d in dn]
    if com_nomes:
        nn, dd = detectar_nomes(texto, u.rotulo, ocup)
        cands += nn
        descs += dd
    return cands, descs


def _trecho(u, ini, fim, largura=45):
    t = u.texto
    a = max(0, ini - largura)
    b = min(len(t), fim + largura)
    pre = '…' if a > 0 else ''
    pos = '…' if b < len(t) else ''
    corpo = t[a:b]
    trecho = pre + corpo + pos
    ia = len(pre) + (ini - a)
    ib = ia + (fim - ini)
    if u.rotulo:
        prefixo = '[%s] ' % u.rotulo[:30]
        trecho = prefixo + trecho
        ia += len(prefixo)
        ib += len(prefixo)
    return trecho, ia, ib


# ===================================================================== análise completa

def _copia(c, **mud):
    n = Candidato(c.tipo, c.chave, c.valor, dict(c.formatos), c.status, c.motivo, c.regra,
                  c.ini, c.fim, c.bruto, dict(c.extra), c.digitos)
    for k, v in mud.items():
        setattr(n, k, v)
    return n


class Resultado(object):
    def __init__(self):
        self.itens = []
        self.descartes = []
        self.resumo = {}

    def como_dict(self):
        return {'itens': self.itens, 'descartes': self.descartes, 'resumo': self.resumo}


def analisar(docs, com_nomes=True, progresso=None):
    ocorrencias = []
    descartes = []
    cache = {}
    for doc in docs:
        for u in unidades(doc):
            chave_cache = (u.rotulo, u.texto) if u.coluna is not None else None
            if chave_cache is not None and chave_cache in cache:
                cands, descs = cache[chave_cache]
            else:
                cands, descs = detectar_unidade(u, com_nomes)
                if chave_cache is not None:
                    cache[chave_cache] = (cands, descs)
            for c in cands:
                trecho, a, b = _trecho(u, c.ini, c.fim)
                ocorrencias.append(Ocorrencia(c, doc, u.local, trecho, a, b, u.coluna))
            for d in descs:
                trecho, a, b = _trecho(u, d.ini, d.fim)
                descartes.append((d, doc, u.local, trecho, a, b))
        if progresso:
            progresso(doc)
    ocorrencias = _resolver_ambiguos(ocorrencias)
    res = Resultado()
    res.itens = _consolidar(ocorrencias)
    _referencias_cruzadas(res.itens)
    res.descartes = _consolidar_descartes(descartes)
    res.resumo = _resumo(res.itens, res.descartes, docs)
    return res


def _resolver_ambiguos(ocorrencias):
    """R-AMB: 11 dígitos sem formatação que são CPF válido E celular válido."""
    cpf_firmes, tel_firmes = set(), set()
    por_coluna = {}
    for o in ocorrencias:
        c = o.cand
        if c.regra == 'R-AMB-01':
            continue
        if c.tipo == 'cpf':
            cpf_firmes.add(c.chave)
        elif c.tipo == 'telefone':
            tel_firmes.add(c.chave)
        if o.coluna is not None and c.tipo in ('cpf', 'telefone'):
            cont = por_coluna.setdefault(o.coluna, {'cpf': 0, 'telefone': 0})
            cont[c.tipo] += 1
    saida = []
    for o in ocorrencias:
        c = o.cand
        if c.regra != 'R-AMB-01':
            saida.append(o)
            continue
        tel = c.alternativa
        decisao, motivo, regra = None, '', ''
        if c.chave in cpf_firmes and tel.chave not in tel_firmes:
            decisao, regra = 'cpf', 'R-AMB-02'
            motivo = 'Ambíguo resolvido como CPF: o mesmo número aparece como CPF em outro ponto'
        elif tel.chave in tel_firmes and c.chave not in cpf_firmes:
            decisao, regra = 'telefone', 'R-AMB-02'
            motivo = 'Ambíguo resolvido como telefone: o mesmo número aparece como telefone em outro ponto'
        elif o.coluna is not None and o.coluna in por_coluna:
            cont = por_coluna[o.coluna]
            total = cont['cpf'] + cont['telefone']
            for tipo in ('cpf', 'telefone'):
                if total >= 3 and cont[tipo] >= 0.8 * total:
                    decisao, regra = tipo, 'R-AMB-03'
                    motivo = 'Ambíguo resolvido pela coluna: %d de %d valores da coluna são %s' % (
                        cont[tipo], total, 'CPF' if tipo == 'cpf' else 'telefone')
        if decisao == 'cpf':
            o.cand = _copia(c, status=OK, motivo=motivo, regra=regra)
            saida.append(o)
        elif decisao == 'telefone':
            o.cand = _copia(tel, status=OK, motivo=motivo, regra=regra)
            saida.append(o)
        else:
            saida.append(o)
            saida.append(Ocorrencia(tel, o.doc, o.local, o.trecho, o.a, o.b, o.coluna))
    return saida


def _consolidar(ocorrencias):
    itens = {}
    ordem = []
    for o in ocorrencias:
        c = o.cand
        k = (c.tipo, c.chave)
        it = itens.get(k)
        if it is None:
            it = {
                'tipo': c.tipo, 'valor': c.valor, 'formatos': dict(c.formatos),
                'status': c.status, 'motivo': c.motivo, 'obs': [], 'regras': [],
                'n': 0, 'origens': [], 'amostras': [], 'extra': dict(c.extra),
                '_brutos': [],
            }
            itens[k] = it
            ordem.append(k)
        it['n'] += 1
        if ORDEM_STATUS[c.status] < ORDEM_STATUS[it['status']]:
            if it['motivo'] and it['motivo'] not in it['obs']:
                it['obs'].append(it['motivo'])
            it['status'], it['motivo'] = c.status, c.motivo
            if c.tipo == 'imei':
                it['valor'], it['formatos'] = c.valor, dict(c.formatos)
        elif c.motivo != it['motivo'] and c.motivo not in it['obs'] and len(it['obs']) < 6:
            it['obs'].append(c.motivo)
        if c.regra not in it['regras']:
            it['regras'].append(c.regra)
        if o.doc.nome not in it['origens']:
            it['origens'].append(o.doc.nome)
        bruto = re.sub(r'\D', '', c.bruto) if c.tipo == 'imei' else None
        if bruto and bruto not in it['_brutos']:
            it['_brutos'].append(bruto)
        if len(it['amostras']) < MAX_AMOSTRAS:
            it['amostras'].append({'doc': o.doc.nome, 'doc_id': o.doc.id, 'local': o.local,
                                   'trecho': o.trecho, 'a': o.a, 'b': o.b, 'bruto': c.bruto})
    ordem_tipo = {t: i for i, t in enumerate(TIPOS)}
    lista = []
    posicao = {k: i for i, k in enumerate(ordem)}
    for idx, k in enumerate(sorted(ordem, key=lambda k: (ordem_tipo[k[0]], posicao[k]))):
        it = itens[k]
        brutos = it.pop('_brutos')
        if it['tipo'] == 'imei' and len(brutos) > 1:
            it['obs'].append('Formas encontradas: ' + ', '.join(brutos[:5]))
        it['id'] = idx
        it['rotulo_tipo'] = ROTULO_TIPO[it['tipo']]
        lista.append(it)
    return lista


def _referencias_cruzadas(itens):
    cpfs = [it for it in itens if it['tipo'] == 'cpf' and not it['formatos']['digitos'].count('*')]
    por_local = {}
    for t in itens:
        if t['tipo'] == 'telefone' and not t['extra'].get('sem_ddd') and t['formatos']['e164'].startswith('+55'):
            e = t['formatos']['e164']
            for n in (8, 9):
                por_local.setdefault(e[-n:], []).append(t['valor'])
    for it in itens:
        if it['tipo'] == 'cpf' and '*' in it['formatos']['digitos']:
            mascara = it['formatos']['digitos']
            compat = [c['valor'] for c in cpfs
                      if all(m == '*' or m == d for m, d in zip(mascara, c['formatos']['digitos']))]
            if compat:
                it['extra']['compativeis'] = compat[:10]
                it['obs'].append('Compatível com CPF(s) do lote: ' + ', '.join(compat[:5]) +
                                 (' e mais %d' % (len(compat) - 5) if len(compat) > 5 else ''))
        if it['tipo'] == 'telefone' and it['extra'].get('sem_ddd'):
            compat = por_local.get(it['formatos']['digitos'], [])
            if compat:
                it['obs'].append('Mesmo número local de: ' + ', '.join(compat[:5]))
    nomes = [it for it in itens if it['tipo'] == 'nome']
    if len(nomes) <= 3000:
        conectivos = {'DE', 'DA', 'DO', 'DAS', 'DOS', 'E'}
        toks = [[t for t in _chave_tokens(it['valor']) if t not in conectivos] for it in nomes]
        for i, a in enumerate(nomes):
            ta = toks[i]
            if len(ta) < 2:
                continue
            maiores = []
            for j, b in enumerate(nomes):
                if i == j:
                    continue
                tb = toks[j]
                if len(tb) > len(ta) and tb[0] == ta[0] and set(ta) <= set(tb):
                    maiores.append(b['valor'])
            if maiores:
                a['obs'].append('Pode ser a mesma pessoa que: ' + '; '.join(maiores[:3]))


def _chave_tokens(nome):
    from .nomes import chave_nome
    return chave_nome(nome).split()


def _consolidar_descartes(descartes):
    grupos = {}
    ordem = []
    for d, doc, local, trecho, a, b in descartes:
        k = (d.tipo, d.bruto, d.regra)
        g = grupos.get(k)
        if g is None:
            g = {'tipo': d.tipo, 'valor': d.bruto, 'motivo': d.motivo, 'regra': d.regra,
                 'n': 0, 'origens': [], 'amostras': []}
            grupos[k] = g
            ordem.append(k)
        g['n'] += 1
        if doc.nome not in g['origens']:
            g['origens'].append(doc.nome)
        if len(g['amostras']) < 10:
            g['amostras'].append({'doc': doc.nome, 'doc_id': doc.id, 'local': local,
                                  'trecho': trecho, 'a': a, 'b': b, 'bruto': d.bruto})
    saida = []
    for idx, k in enumerate(ordem):
        g = grupos[k]
        g['id'] = idx
        g['rotulo_tipo'] = ROTULO_TIPO.get(g['tipo'], 'Número')
        saida.append(g)
    return saida


def _resumo(itens, descartes, docs):
    por_tipo = {t: {'total': 0, OK: 0, ATENCAO: 0, INVALIDO: 0} for t in TIPOS}
    for it in itens:
        p = por_tipo[it['tipo']]
        p['total'] += 1
        p[it['status']] += 1
    return {'por_tipo': por_tipo, 'total': len(itens), 'descartes': len(descartes),
            'documentos': len(docs)}
