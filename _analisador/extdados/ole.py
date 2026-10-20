# -*- coding: utf-8 -*-
"""Arquivos OLE2 (Compound File Binary): Excel 97-2003 (.xls) e Word 97-2003 (.doc).

Implementação própria (biblioteca padrão), conforme as especificações públicas
[MS-CFB], [MS-XLS] e [MS-DOC].
"""
import struct
from decimal import Decimal

FIM_CADEIA = 0xFFFFFFFE
LIVRE = 0xFFFFFFFF
LIMITE_SETORES = 10000000


class ErroOLE(Exception):
    pass


class OLE(object):
    def __init__(self, dados):
        if dados[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
            raise ErroOLE('assinatura OLE2 ausente')
        self.d = dados
        (self.versao_maior, ordem, shift, mini_shift) = struct.unpack_from('<HHHH', dados, 0x1A)
        self.tam_setor = 1 << shift
        self.tam_mini = 1 << mini_shift
        (n_fat, self.dir_ini, _, self.corte_mini, self.minifat_ini, n_minifat,
         difat_ini, n_difat) = struct.unpack_from('<IIIIIIII', dados, 0x2C)
        difat = list(struct.unpack_from('<109I', dados, 0x4C))
        s = difat_ini
        vistos = 0
        por_setor = self.tam_setor // 4
        while s not in (FIM_CADEIA, LIVRE) and vistos < n_difat + 1 and vistos < 100000:
            setor = self._setor(s)
            vals = struct.unpack('<%dI' % por_setor, setor)
            difat.extend(vals[:-1])
            s = vals[-1]
            vistos += 1
        self.fat = []
        for s in difat[:n_fat]:
            if s in (FIM_CADEIA, LIVRE):
                continue
            self.fat.extend(struct.unpack('<%dI' % por_setor, self._setor(s)))
        self.minifat = []
        if self.minifat_ini not in (FIM_CADEIA, LIVRE):
            bruto = self._cadeia(self.minifat_ini)
            self.minifat = list(struct.unpack('<%dI' % (len(bruto) // 4), bruto[:len(bruto) // 4 * 4]))
        self.entradas = []
        dir_bruto = self._cadeia(self.dir_ini)
        for i in range(len(dir_bruto) // 128):
            e = dir_bruto[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from('<H', e, 64)[0]
            nome = e[:max(0, nlen - 2)].decode('utf-16-le', 'replace')
            tipo = e[66]
            esq, dir_, filho = struct.unpack_from('<III', e, 68)
            ini, tam = struct.unpack_from('<IQ', e, 116)
            if self.versao_maior == 3:
                tam &= 0xFFFFFFFF
            self.entradas.append({'nome': nome, 'tipo': tipo, 'esq': esq, 'dir': dir_,
                                  'filho': filho, 'ini': ini, 'tam': tam})
        if not self.entradas:
            raise ErroOLE('diretório vazio')
        raiz = self.entradas[0]
        self.mini_stream = self._cadeia(raiz['ini'], raiz['tam']) if raiz['ini'] not in (FIM_CADEIA, LIVRE) else b''
        self.caminhos = {}
        self._indexar(raiz['filho'], '', set())

    def _setor(self, s):
        off = (s + 1) * self.tam_setor
        if off + self.tam_setor > len(self.d) + self.tam_setor:
            raise ErroOLE('setor fora do arquivo')
        bloco = self.d[off:off + self.tam_setor]
        if len(bloco) < self.tam_setor:
            bloco += b'\x00' * (self.tam_setor - len(bloco))
        return bloco

    def _cadeia(self, ini, tamanho=None):
        partes = []
        s = ini
        n = 0
        while s not in (FIM_CADEIA, LIVRE) and s < len(self.fat) + 1:
            partes.append(self._setor(s))
            n += 1
            if n > LIMITE_SETORES or s >= len(self.fat):
                break
            s = self.fat[s]
        dados = b''.join(partes)
        return dados[:tamanho] if tamanho is not None else dados

    def _cadeia_mini(self, ini, tamanho):
        partes = []
        s = ini
        n = 0
        while s not in (FIM_CADEIA, LIVRE) and s < len(self.minifat):
            off = s * self.tam_mini
            partes.append(self.mini_stream[off:off + self.tam_mini])
            s = self.minifat[s]
            n += 1
            if n > LIMITE_SETORES:
                break
        return b''.join(partes)[:tamanho]

    def _indexar(self, idx, prefixo, vistos):
        pilha = [(idx, prefixo)]
        while pilha:
            i, pref = pilha.pop()
            if i == LIVRE or i >= len(self.entradas) or i in vistos:
                continue
            vistos.add(i)
            e = self.entradas[i]
            caminho = pref + e['nome']
            self.caminhos[caminho.lower()] = e
            pilha.append((e['esq'], pref))
            pilha.append((e['dir'], pref))
            if e['tipo'] == 1:
                pilha.append((e['filho'], caminho + '/'))

    def existe(self, caminho):
        return caminho.lower() in self.caminhos

    def ler(self, caminho):
        e = self.caminhos.get(caminho.lower())
        if e is None:
            return None
        if e['tam'] < self.corte_mini:
            return self._cadeia_mini(e['ini'], e['tam'])
        return self._cadeia(e['ini'], e['tam'])


def ler_ole(doc, dados):
    try:
        o = OLE(dados)
    except (ErroOLE, struct.error, IndexError) as e:
        doc.tipo = 'OLE2 inválido'
        doc.aviso('Arquivo OLE2 corrompido (%s)' % e)
        return
    if o.existe('Workbook') or o.existe('Book'):
        doc.tipo = 'Excel 97-2003 (.xls)'
        ler_xls(doc, o.ler('Workbook') if o.existe('Workbook') else o.ler('Book'))
    elif o.existe('WordDocument'):
        doc.tipo = 'Word 97-2003 (.doc)'
        ler_doc(doc, o)
    elif any(k.startswith('__substg1.0_') for k in o.caminhos):
        doc.tipo = 'Outlook (.msg)'
        ler_msg(doc, o)
    else:
        doc.tipo = 'OLE2 (desconhecido)'
        doc.aviso('Arquivo OLE2 de tipo não suportado (streams: %s)' % ', '.join(sorted(o.caminhos)[:8]))


# ===================================================================== XLS (BIFF5/BIFF8)

def _registros(ws, ini=0):
    pos = ini
    n = len(ws)
    while pos + 4 <= n:
        tipo, tam = struct.unpack_from('<HH', ws, pos)
        yield tipo, ws[pos + 4:pos + 4 + tam], pos
        pos += 4 + tam


def _num(v):
    if v != v or v in (float('inf'), float('-inf')):
        return ''
    if float(v).is_integer() and abs(v) < 1e17:
        return '%d' % int(v)
    return format(Decimal(repr(v)).normalize(), 'f')


def _rk(rk):
    if rk & 0x02:
        v = rk >> 2
        if v & 0x20000000:
            v -= 0x40000000
        v = float(v)
    else:
        v = struct.unpack('<d', struct.pack('<Q', (rk & 0xFFFFFFFC) << 32))[0]
    if rk & 0x01:
        v /= 100.0
    return v


class _Fluxo(object):
    """Lê strings Unicode do BIFF8 que podem atravessar registros CONTINUE."""

    def __init__(self, blocos):
        self.blocos = blocos      # lista de bytes (registro + CONTINUEs)
        self.b = 0
        self.p = 0

    def _garantir(self):
        while self.b < len(self.blocos) and self.p >= len(self.blocos[self.b]):
            self.b += 1
            self.p = 0

    def ler(self, n):
        saida = b''
        while n > 0:
            self._garantir()
            if self.b >= len(self.blocos):
                break
            bloco = self.blocos[self.b]
            pedaco = bloco[self.p:self.p + n]
            saida += pedaco
            self.p += len(pedaco)
            n -= len(pedaco)
        return saida

    def u8(self):
        return struct.unpack('<B', self.ler(1) or b'\x00')[0]

    def u16(self):
        b = self.ler(2)
        return struct.unpack('<H', b.ljust(2, b'\x00'))[0]

    def u32(self):
        b = self.ler(4)
        return struct.unpack('<I', b.ljust(4, b'\x00'))[0]

    def string_unicode(self, cch):
        flags = self.u8()
        rich = flags & 0x08
        ext = flags & 0x04
        n_runs = self.u16() if rich else 0
        tam_ext = self.u32() if ext else 0
        chars = []
        restantes = cch
        alto = flags & 0x01
        while restantes > 0:
            self._garantir()
            if self.b >= len(self.blocos):
                break
            disponivel = len(self.blocos[self.b]) - self.p
            if alto:
                n = min(restantes, disponivel // 2)
                if n == 0:
                    self.b += 1
                    self.p = 0
                    if self.b < len(self.blocos):
                        alto = self.u8() & 0x01
                    continue
                chars.append(self.ler(n * 2).decode('utf-16-le', 'replace'))
            else:
                n = min(restantes, disponivel)
                chars.append(self.ler(n).decode('latin-1'))
            restantes -= n
            if restantes > 0:
                self.b += 1
                self.p = 0
                if self.b < len(self.blocos):
                    alto = self.u8() & 0x01
        self.ler(n_runs * 4 + tam_ext)
        return ''.join(chars)


def _str_biff8(dados, off, cch_bytes=2):
    if cch_bytes == 2:
        cch = struct.unpack_from('<H', dados, off)[0]
    else:
        cch = dados[off]
    off += cch_bytes
    flags = dados[off]
    off += 1
    if flags & 0x08:
        off += 2
    if flags & 0x04:
        off += 4
    if flags & 0x01:
        return dados[off:off + cch * 2].decode('utf-16-le', 'replace')
    return dados[off:off + cch].decode('latin-1')


def ler_xls(doc, ws):
    if not ws:
        doc.aviso('Stream Workbook vazio')
        return
    versao = 0x0600
    codepage = 'cp1252'
    sst = []
    folhas = []
    regs = list(_registros(ws))
    i = 0
    while i < len(regs):
        tipo, dados, pos = regs[i]
        if tipo == 0x0809 and i == 0:
            versao = struct.unpack_from('<H', dados, 0)[0]
        elif tipo == 0x002F:
            doc.aviso('Planilha .xls protegida por senha (criptografada): não é possível ler. '
                      'Abra no Excel, remova a senha e salve novamente.')
            return
        elif tipo == 0x0042 and len(dados) >= 2:
            cp = struct.unpack_from('<H', dados, 0)[0]
            if cp not in (1200, 0x8000, 0x8001):
                codepage = 'cp%d' % cp
        elif tipo == 0x0085:
            off = struct.unpack_from('<I', dados, 0)[0]
            if versao >= 0x0600:
                nome = _str_biff8(dados, 6, 1)
            else:
                n = dados[6]
                nome = dados[7:7 + n].decode(codepage, 'replace')
            folhas.append((nome, off))
        elif tipo == 0x00FC:
            blocos = [dados[8:]]
            j = i + 1
            while j < len(regs) and regs[j][0] == 0x003C:
                blocos.append(regs[j][1])
                j += 1
            total_unicos = struct.unpack_from('<I', dados, 4)[0]
            fl = _Fluxo(blocos)
            for _ in range(total_unicos):
                fl._garantir()
                if fl.b >= len(blocos):
                    break
                cch = fl.u16()
                sst.append(fl.string_unicode(cch))
            i = j
            continue
        elif tipo == 0x000A:
            break
        i += 1
    if versao < 0x0600:
        doc.info['biff'] = 'BIFF5/7 (Excel 95)'
    for nome, off in folhas:
        doc.tabela('aba ' + nome, _folha_xls(ws, off, sst, versao, codepage))


def _folha_xls(ws, off, sst, versao, codepage):
    celulas = {}
    maxr = -1
    pendente = None

    def por(r, c, v):
        celulas[(r, c)] = v

    for tipo, dados, pos in _registros(ws, off):
        if tipo == 0x000A:
            break
        try:
            if tipo == 0x00FD:                                  # LABELSST
                r, c, _, idx = struct.unpack_from('<HHHI', dados, 0)
                por(r, c, sst[idx] if idx < len(sst) else '')
            elif tipo in (0x0204, 0x00D6):                      # LABEL / RSTRING
                r, c = struct.unpack_from('<HH', dados, 0)
                if versao >= 0x0600:
                    por(r, c, _str_biff8(dados, 6, 2))
                else:
                    n = struct.unpack_from('<H', dados, 6)[0]
                    por(r, c, dados[8:8 + n].decode(codepage, 'replace'))
            elif tipo == 0x0203:                                # NUMBER
                r, c, _, v = struct.unpack_from('<HHHd', dados, 0)
                por(r, c, _num(v))
            elif tipo == 0x027E:                                # RK
                r, c, _, rk = struct.unpack_from('<HHHI', dados, 0)
                por(r, c, _num(_rk(rk)))
            elif tipo == 0x00BD:                                # MULRK
                r, c0 = struct.unpack_from('<HH', dados, 0)
                n = (len(dados) - 6) // 6
                for k in range(n):
                    _, rk = struct.unpack_from('<HI', dados, 4 + k * 6)
                    por(r, c0 + k, _num(_rk(rk)))
            elif tipo == 0x0006:                                # FORMULA
                r, c = struct.unpack_from('<HH', dados, 0)
                res = dados[6:14]
                if res[6:8] == b'\xff\xff':
                    if res[0] == 0:
                        pendente = (r, c)
                    elif res[0] == 1:
                        por(r, c, 'VERDADEIRO' if res[2] else 'FALSO')
                else:
                    por(r, c, _num(struct.unpack('<d', res)[0]))
            elif tipo == 0x0207 and pendente is not None:     # STRING (resultado da fórmula)
                if versao >= 0x0600:
                    por(pendente[0], pendente[1], _str_biff8(dados, 0, 2))
                else:
                    n = struct.unpack_from('<H', dados, 0)[0]
                    por(pendente[0], pendente[1], dados[2:2 + n].decode(codepage, 'replace'))
                pendente = None
            elif tipo == 0x0205:                                # BOOLERR
                r, c, _, v, e = struct.unpack_from('<HHHBB', dados, 0)
                if not e:
                    por(r, c, 'VERDADEIRO' if v else 'FALSO')
        except (struct.error, IndexError):
            continue
    if not celulas:
        return []
    maxr = max(r for r, _ in celulas)
    linhas = [[] for _ in range(maxr + 1)]
    for (r, c), v in sorted(celulas.items()):
        linha = linhas[r]
        while len(linha) < c:
            linha.append('')
        linha.append(v)
    return linhas


# ===================================================================== DOC (Word 97-2003)

def ler_doc(doc, o):
    wd = o.ler('WordDocument')
    if not wd or len(wd) < 0x1AA or struct.unpack_from('<H', wd, 0)[0] != 0xA5EC:
        doc.aviso('Documento Word sem cabeçalho FIB válido')
        return
    nfib = struct.unpack_from('<H', wd, 2)[0]
    flags = struct.unpack_from('<H', wd, 0x0A)[0]
    if flags & 0x0100:
        doc.aviso('Documento Word protegido por senha (criptografado): não é possível ler')
        return
    if nfib < 0x00C1:
        doc.aviso('Word 6/95 (formato antigo): texto extraído por varredura simples')
        from .leitores import extrair_strings
        doc.texto('texto (varredura)', extrair_strings(wd))
        return
    tabela = o.ler('1Table' if flags & 0x0200 else '0Table') or b''
    fc_clx, lcb_clx = struct.unpack_from('<II', wd, 0x01A2)
    clx = tabela[fc_clx:fc_clx + lcb_clx]
    pos = 0
    pcdt = None
    while pos < len(clx):
        if clx[pos] == 0x01:
            cb = struct.unpack_from('<H', clx, pos + 1)[0]
            pos += 3 + cb
        elif clx[pos] == 0x02:
            lcb = struct.unpack_from('<I', clx, pos + 1)[0]
            pcdt = clx[pos + 5:pos + 5 + lcb]
            break
        else:
            break
    if pcdt is None:
        doc.aviso('Tabela de peças (CLX) não encontrada: texto extraído por varredura simples')
        from .leitores import extrair_strings
        doc.texto('texto (varredura)', extrair_strings(wd))
        return
    n = (len(pcdt) - 4) // 12
    cps = struct.unpack_from('<%dI' % (n + 1), pcdt, 0)
    partes = []
    for k in range(n):
        off = 4 * (n + 1) + 8 * k
        fc_bruto = struct.unpack_from('<I', pcdt, off + 2)[0]
        comprimido = fc_bruto & 0x40000000
        fc = fc_bruto & 0x3FFFFFFF
        qtd = cps[k + 1] - cps[k]
        if qtd <= 0:
            continue
        if comprimido:
            ini = fc // 2
            partes.append(wd[ini:ini + qtd].decode('cp1252', 'replace'))
        else:
            partes.append(wd[fc:fc + 2 * qtd].decode('utf-16-le', 'replace'))
    texto = ''.join(partes)
    # caracteres de controle do Word: \r parágrafo, \x07 fim de célula, \x0b quebra manual,
    # \x0c quebra de página/seção, \x13-\x15 campos, \x01/\x08 objetos.
    tr = {0x0D: '\n', 0x0B: '\n', 0x0C: '\n', 0x07: '\t', 0x13: ' ', 0x14: ' ', 0x15: ' ',
          0x01: '', 0x08: '', 0x02: '', 0x05: '', 0x1E: '-', 0x1F: '', 0xA0: ' '}
    doc.texto('', texto.translate(tr))


# ===================================================================== MSG (Outlook)

def ler_msg(doc, o):
    def prop(codigo):
        for sufixo, cod in (('001F', 'utf-16-le'), ('001E', 'cp1252')):
            b = o.ler('__substg1.0_%s%s' % (codigo, sufixo))
            if b:
                return b.decode(cod, 'replace').rstrip('\x00')
        return ''
    linhas = []
    for codigo, rotulo in (('0C1A', 'From'), ('0C1F', 'From (e-mail)'), ('0E04', 'To'), ('0E03', 'Cc'),
                           ('0E02', 'Bcc'), ('0037', 'Subject'), ('5D01', 'Sender (e-mail)')):
        v = prop(codigo)
        if v:
            linhas.append('%s: %s' % (rotulo, v))
    for caminho in sorted(o.caminhos):
        if caminho.startswith('__recip_version1.0_') and caminho.count('/') == 1:
            base = caminho.split('/')[0]
            for codigo, rotulo in (('3001', 'Destinatário'), ('39FE', 'Destinatário (e-mail)'),
                                   ('3003', 'Destinatário (e-mail)')):
                for sufixo, cod in (('001F', 'utf-16-le'), ('001E', 'cp1252')):
                    b = o.ler('%s/__substg1.0_%s%s' % (base, codigo, sufixo))
                    if b:
                        linhas.append('%s: %s' % (rotulo, b.decode(cod, 'replace').rstrip('\x00')))
    doc.texto('cabeçalho da mensagem', '\n'.join(dict.fromkeys(linhas)))
    corpo = prop('1000')
    if corpo:
        doc.texto('corpo da mensagem', corpo)
    else:
        html = o.ler('__substg1.0_10130102')
        if html:
            from .leitores import html_para_texto
            doc.texto('corpo da mensagem', html_para_texto(html.decode('utf-8', 'replace')))
    from .leitores import ler_bytes, _incorporar
    for base in sorted(c for c in o.caminhos if c.startswith('__attach_version1.0_') and '/' not in c):
        dados = o.ler(base + '/__substg1.0_37010102')
        nome = ''
        for codigo in ('3707', '3704'):
            for sufixo, cod in (('001F', 'utf-16-le'), ('001E', 'cp1252')):
                b = o.ler('%s/__substg1.0_%s%s' % (base, codigo, sufixo))
                if b and not nome:
                    nome = b.decode(cod, 'replace').rstrip('\x00')
        if not dados:
            doc.aviso('Anexo "%s" da mensagem sem conteúdo legível (ex.: mensagem incorporada)' % (nome or base))
            continue
        nome = nome or base
        _incorporar(doc, ler_bytes(nome, dados, 1), 'anexo ' + nome)
