# -*- coding: utf-8 -*-
"""Extração de texto de PDF com a biblioteca padrão.

Cobre: objetos diretos e em object streams (PDF 1.5+), atualizações
incrementais, filtros Flate/LZW/ASCIIHex/ASCII85/RunLength com preditores PNG,
criptografia padrão com senha de abertura vazia (RC4, AES-128, AES-256),
fontes simples e compostas (Type0) com ToUnicode, codificações
WinAnsi/MacRoman/Standard + Differences, Form XObjects, campos de formulário,
anotações e metadados (/Info).

Não cobre: OCR de páginas digitalizadas (a ferramenta avisa quais páginas não
têm camada de texto) e PDFs que exigem senha para abrir.
"""
import math
import re
import zlib

from .cripto import Decifrador, FiltroNaoSuportado, SenhaNecessaria

LIMITE_STREAM = 64 * 1024 * 1024
PROFUNDIDADE_FORM = 8


class Nome(str):
    pass


class Op(str):
    pass


class Ref(object):
    __slots__ = ('n', 'g')

    def __init__(self, n, g):
        self.n, self.g = n, g

    def __repr__(self):
        return 'Ref(%d,%d)' % (self.n, self.g)


# ===================================================================== analisador de objetos

_WS = b' \t\r\n\x0c\x00'
_RE_REF = re.compile(rb'(\d+)\s+(\d+)\s+R(?![^\s()<>\[\]{}/%])')
_RE_NUM = re.compile(rb'[+-]?(?:\d+\.?\d*|\.\d+)')
_RE_PALAVRA = re.compile(rb'[^\s()<>\[\]{}/%]+')
_RE_HEX = re.compile(rb'[^0-9A-Fa-f]')
_ESCAPES = {ord('n'): b'\n', ord('r'): b'\r', ord('t'): b'\t', ord('b'): b'\b', ord('f'): b'\x0c',
            ord('('): b'(', ord(')'): b')', ord('\\'): b'\\'}


def _pular(d, p):
    n = len(d)
    while p < n:
        c = d[p]
        if c in _WS:
            p += 1
        elif c == 0x25:  # %
            while p < n and d[p] not in (10, 13):
                p += 1
        else:
            break
    return p


def _nome(d, p):
    """p aponta para '/'."""
    m = _RE_PALAVRA.match(d, p + 1)
    bruto = m.group() if m else b''
    if b'#' in bruto:
        bruto = re.sub(rb'#([0-9A-Fa-f]{2})', lambda x: bytes([int(x.group(1), 16)]), bruto)
    return Nome(bruto.decode('latin-1')), p + 1 + (len(m.group()) if m else 0)


def _literal(d, p):
    """p aponta para '('. Retorna (bytes, nova_posicao)."""
    p += 1
    n = len(d)
    nivel = 1
    saida = bytearray()
    while p < n:
        c = d[p]
        if c == 0x5C:  # backslash
            p += 1
            if p >= n:
                break
            c = d[p]
            if c in _ESCAPES:
                saida += _ESCAPES[c]
                p += 1
            elif 0x30 <= c <= 0x37:
                k = p
                while k < n and k < p + 3 and 0x30 <= d[k] <= 0x37:
                    k += 1
                saida.append(int(d[p:k], 8) & 0xFF)
                p = k
            elif c == 13:
                p += 1
                if p < n and d[p] == 10:
                    p += 1
            elif c == 10:
                p += 1
            else:
                saida.append(c)
                p += 1
            continue
        if c == 0x28:
            nivel += 1
        elif c == 0x29:
            nivel -= 1
            if nivel == 0:
                return bytes(saida), p + 1
        saida.append(c)
        p += 1
    return bytes(saida), p


def _hexa(d, p):
    fim = d.find(b'>', p + 1)
    if fim < 0:
        fim = len(d)
    h = _RE_HEX.sub(b'', d[p + 1:fim])
    if len(h) % 2:
        h += b'0'
    return bytes.fromhex(h.decode('ascii')), fim + 1


def objeto(d, p, profundidade=0):
    p = _pular(d, p)
    if p >= len(d):
        raise ValueError('fim inesperado')
    if profundidade > 100:
        raise ValueError('aninhamento excessivo')
    c = d[p]
    if c == 0x2F:
        return _nome(d, p)
    if c == 0x28:
        return _literal(d, p)
    if c == 0x3C:
        if d[p + 1:p + 2] == b'<':
            p += 2
            dic = {}
            while True:
                p = _pular(d, p)
                if p >= len(d):
                    return dic, p
                if d[p:p + 2] == b'>>':
                    return dic, p + 2
                if d[p] != 0x2F:
                    # chave inválida: tenta pular um token
                    _, p = objeto(d, p, profundidade + 1)
                    continue
                k, p = _nome(d, p)
                p2 = _pular(d, p)
                if d[p2:p2 + 2] == b'>>':
                    dic[k] = None
                    return dic, p2 + 2
                v, p = objeto(d, p, profundidade + 1)
                dic[k] = v
        return _hexa(d, p)
    if c == 0x5B:
        p += 1
        arr = []
        while True:
            p = _pular(d, p)
            if p >= len(d):
                return arr, p
            if d[p] == 0x5D:
                return arr, p + 1
            v, p = objeto(d, p, profundidade + 1)
            arr.append(v)
    if c in b'+-.0123456789':
        m = _RE_REF.match(d, p)
        if m:
            return Ref(int(m.group(1)), int(m.group(2))), m.end()
        m = _RE_NUM.match(d, p)
        if m:
            t = m.group()
            try:
                v = float(t) if b'.' in t else int(t)
            except ValueError:
                v = 0
            return v, m.end()
        return 0, p + 1
    m = _RE_PALAVRA.match(d, p)
    if not m:
        return Op(chr(c)), p + 1
    w = m.group()
    if w == b'true':
        return True, m.end()
    if w == b'false':
        return False, m.end()
    if w == b'null':
        return None, m.end()
    return Op(w.decode('latin-1')), m.end()


# ===================================================================== filtros

def _flate(b):
    try:
        return zlib.decompress(b)
    except zlib.error:
        pass
    for wbits in (15, -15, 31):
        try:
            return zlib.decompressobj(wbits).decompress(b, LIMITE_STREAM)
        except zlib.error:
            continue
    # dados truncados/corrompidos: recupera o que der, em blocos
    saida = []
    dobj = zlib.decompressobj()
    try:
        for i in range(0, len(b), 1024):
            saida.append(dobj.decompress(b[i:i + 1024]))
    except zlib.error:
        pass
    return b''.join(saida)


def _lzw(data, early=1):
    saida = bytearray()
    tabela = [bytes([i]) for i in range(256)] + [b'', b'']
    bits = 9
    buf = 0
    nbits = 0
    anterior = None
    for byte in data:
        buf = ((buf << 8) | byte) & 0xFFFFFFFF
        nbits += 8
        while nbits >= bits:
            codigo = (buf >> (nbits - bits)) & ((1 << bits) - 1)
            nbits -= bits
            if codigo == 256:
                tabela = tabela[:258]
                bits = 9
                anterior = None
                continue
            if codigo == 257:
                return bytes(saida)
            if anterior is None:
                entrada = tabela[codigo] if codigo < len(tabela) else b''
            elif codigo < len(tabela):
                entrada = tabela[codigo]
                tabela.append(anterior + entrada[:1])
            else:
                entrada = anterior + anterior[:1]
                tabela.append(entrada)
            saida += entrada
            anterior = entrada
            n = len(tabela) + early
            if n >= 4096:
                bits = 12
            elif n >= 2048:
                bits = 12
            elif n >= 1024:
                bits = 11
            elif n >= 512:
                bits = 10
    return bytes(saida)


def _a85(b):
    b = re.sub(rb'\s', b'', b)
    if b.startswith(b'<~'):
        b = b[2:]
    fim = b.find(b'~>')
    if fim >= 0:
        b = b[:fim]
    saida = bytearray()
    grupo = []
    for c in b:
        if c == ord('z') and not grupo:
            saida += b'\x00\x00\x00\x00'
            continue
        if c < 33 or c > 117:
            continue
        grupo.append(c - 33)
        if len(grupo) == 5:
            n = 0
            for x in grupo:
                n = n * 85 + x
            saida += (n & 0xFFFFFFFF).to_bytes(4, 'big')
            grupo = []
    if grupo:
        k = len(grupo)
        grupo += [84] * (5 - k)
        n = 0
        for x in grupo:
            n = n * 85 + x
        saida += (n & 0xFFFFFFFF).to_bytes(4, 'big')[:k - 1]
    return bytes(saida)


def _ahx(b):
    fim = b.find(b'>')
    if fim >= 0:
        b = b[:fim]
    h = _RE_HEX.sub(b'', b)
    if len(h) % 2:
        h += b'0'
    return bytes.fromhex(h.decode('ascii'))


def _rl(b):
    saida = bytearray()
    i = 0
    n = len(b)
    while i < n:
        L = b[i]
        if L == 128:
            break
        if L < 128:
            saida += b[i + 1:i + 2 + L]
            i += 2 + L
        else:
            if i + 1 < n:
                saida += bytes([b[i + 1]]) * (257 - L)
            i += 2
    return bytes(saida)


def _png(data, parms):
    cores = int(parms.get('Colors', 1) or 1)
    bpc = int(parms.get('BitsPerComponent', 8) or 8)
    colunas = int(parms.get('Columns', 1) or 1)
    bpp = max(1, cores * bpc // 8)
    tam = (cores * bpc * colunas + 7) // 8
    saida = bytearray()
    ant = bytearray(tam)
    i = 0
    n = len(data)
    while i < n:
        ft = data[i]
        linha = bytearray(data[i + 1:i + 1 + tam])
        i += 1 + tam
        if len(linha) < tam:
            linha += bytes(tam - len(linha))
        if ft == 1:
            for j in range(bpp, tam):
                linha[j] = (linha[j] + linha[j - bpp]) & 255
        elif ft == 2:
            for j in range(tam):
                linha[j] = (linha[j] + ant[j]) & 255
        elif ft == 3:
            for j in range(tam):
                esq = linha[j - bpp] if j >= bpp else 0
                linha[j] = (linha[j] + ((esq + ant[j]) >> 1)) & 255
        elif ft == 4:
            for j in range(tam):
                a = linha[j - bpp] if j >= bpp else 0
                b = ant[j]
                c = ant[j - bpp] if j >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                linha[j] = (linha[j] + pr) & 255
        saida += linha
        ant = linha
    return bytes(saida)


# ===================================================================== codificações e glifos

_LATIN1_NOMES = (
    'exclamdown cent sterling currency yen brokenbar section dieresis copyright ordfeminine '
    'guillemotleft logicalnot hyphen registered macron degree plusminus twosuperior threesuperior '
    'acute mu paragraph periodcentered cedilla onesuperior ordmasculine guillemotright onequarter '
    'onehalf threequarters questiondown Agrave Aacute Acircumflex Atilde Adieresis Aring AE Ccedilla '
    'Egrave Eacute Ecircumflex Edieresis Igrave Iacute Icircumflex Idieresis Eth Ntilde Ograve Oacute '
    'Ocircumflex Otilde Odieresis multiply Oslash Ugrave Uacute Ucircumflex Udieresis Yacute Thorn '
    'germandbls agrave aacute acircumflex atilde adieresis aring ae ccedilla egrave eacute ecircumflex '
    'edieresis igrave iacute icircumflex idieresis eth ntilde ograve oacute ocircumflex otilde odieresis '
    'divide oslash ugrave uacute ucircumflex udieresis yacute thorn ydieresis').split()
_ASCII_NOMES = (
    'space exclam quotedbl numbersign dollar percent ampersand quotesingle parenleft parenright '
    'asterisk plus comma hyphen period slash zero one two three four five six seven eight nine colon '
    'semicolon less equal greater question at').split()
GLIFOS = {}
for _i, _n in enumerate(_ASCII_NOMES):
    GLIFOS[_n] = chr(0x20 + _i)
for _c in range(26):
    GLIFOS[chr(65 + _c)] = chr(65 + _c)
    GLIFOS[chr(97 + _c)] = chr(97 + _c)
GLIFOS.update({'bracketleft': '[', 'backslash': '\\', 'bracketright': ']', 'asciicircum': '^',
               'underscore': '_', 'grave': '`', 'braceleft': '{', 'bar': '|', 'braceright': '}',
               'asciitilde': '~', 'quoteright': '’', 'quoteleft': '‘', 'nbspace': ' ', 'nonbreakingspace': ' ',
               'bullet': '•', 'endash': '–', 'emdash': '—', 'quotedblleft': '“', 'quotedblright': '”',
               'quotesinglbase': '‚', 'quotedblbase': '„', 'ellipsis': '…', 'Euro': '€', 'trademark': '™',
               'fi': 'fi', 'fl': 'fl', 'ff': 'ff', 'ffi': 'ffi', 'ffl': 'ffl', 'florin': 'ƒ', 'dagger': '†',
               'daggerdbl': '‡', 'perthousand': '‰', 'guilsinglleft': '‹', 'guilsinglright': '›', 'OE': 'Œ',
               'oe': 'œ', 'Scaron': 'Š', 'scaron': 'š', 'Zcaron': 'Ž', 'zcaron': 'ž', 'Ydieresis': 'Ÿ',
               'circumflex': 'ˆ', 'tilde': '˜', 'minus': '−', 'fraction': '⁄', 'dotlessi': 'ı', 'Lslash': 'Ł',
               'lslash': 'ł', 'caron': 'ˇ', 'breve': '˘', 'dotaccent': '˙', 'ring': '˚', 'ogonek': '˛',
               'hungarumlaut': '˝', 'sfthyphen': '-', 'periodcentered': '·', 'middot': '·', 'mu': 'µ',
               'Omega': 'Ω', 'Delta': 'Δ', 'degree': '°', 'ordfeminine': 'ª', 'ordmasculine': 'º',
               'zerooldstyle': '0', 'oneoldstyle': '1', 'twooldstyle': '2', 'threeoldstyle': '3',
               'fouroldstyle': '4', 'fiveoldstyle': '5', 'sixoldstyle': '6', 'sevenoldstyle': '7',
               'eightoldstyle': '8', 'nineoldstyle': '9', 'at.case': '@', 'space.alt': ' '})
for _i, _n in enumerate(_LATIN1_NOMES):
    GLIFOS.setdefault(_n, chr(0xA1 + _i))
GLIFOS['hyphen'] = '-'


def glifo_para_unicode(nome):
    if not nome:
        return ''
    if nome in GLIFOS:
        return GLIFOS[nome]
    base = nome.split('.', 1)[0]
    if base != nome and base in GLIFOS:
        return GLIFOS[base]
    if '_' in base:
        partes = [glifo_para_unicode(p) for p in base.split('_')]
        if all(partes):
            return ''.join(partes)
    m = re.match(r'^uni((?:[0-9A-Fa-f]{4})+)$', base)
    if m:
        h = m.group(1)
        try:
            return ''.join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h), 4))
        except ValueError:
            return ''
    m = re.match(r'^u([0-9A-Fa-f]{4,6})$', base)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return ''
    return ''


def _tabela(codec):
    t = []
    for i in range(256):
        try:
            t.append(bytes([i]).decode(codec))
        except UnicodeDecodeError:
            t.append('')
    return t


WIN_ANSI = _tabela('cp1252')
MAC_ROMAN = _tabela('mac_roman')
STANDARD = [''] * 256
for _i in range(32, 127):
    STANDARD[_i] = chr(_i)
STANDARD[0x27] = '’'
STANDARD[0x60] = '‘'
for _cod, _ch in ((0xA1, '¡'), (0xA2, '¢'), (0xA3, '£'), (0xA4, '⁄'), (0xA5, '¥'), (0xA6, 'ƒ'), (0xA7, '§'),
                  (0xA8, '¤'), (0xA9, "'"), (0xAA, '“'), (0xAB, '«'), (0xAC, '‹'), (0xAD, '›'), (0xAE, 'fi'),
                  (0xAF, 'fl'), (0xB1, '–'), (0xB2, '†'), (0xB3, '‡'), (0xB4, '·'), (0xB6, '¶'), (0xB7, '•'),
                  (0xB8, '‚'), (0xB9, '„'), (0xBA, '”'), (0xBB, '»'), (0xBC, '…'), (0xBD, '‰'), (0xBF, '¿'),
                  (0xC1, '`'), (0xC2, '´'), (0xC3, 'ˆ'), (0xC4, '˜'), (0xC5, '¯'), (0xC6, '˘'), (0xC7, '˙'),
                  (0xC8, '¨'), (0xCA, '˚'), (0xCB, '¸'), (0xCD, '˝'), (0xCE, '˛'), (0xCF, 'ˇ'), (0xD0, '—'),
                  (0xE1, 'Æ'), (0xE3, 'ª'), (0xE8, 'Ł'), (0xE9, 'Ø'), (0xEA, 'Œ'), (0xEB, 'º'), (0xF1, 'æ'),
                  (0xF5, 'ı'), (0xF8, 'ł'), (0xF9, 'ø'), (0xFA, 'œ'), (0xFB, 'ß')):
    STANDARD[_cod] = _ch
PDFDOC = _tabela('latin-1')
for _cod, _ch in zip(range(0x80, 0xA1), '•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž�€'):
    PDFDOC[_cod] = _ch
BASES = {'WinAnsiEncoding': WIN_ANSI, 'MacRomanEncoding': MAC_ROMAN, 'StandardEncoding': STANDARD,
         'PDFDocEncoding': PDFDOC, 'MacExpertEncoding': STANDARD}


def texto_pdf(b):
    """String de texto do PDF (UTF-16BE com BOM, UTF-8 com BOM ou PDFDocEncoding)."""
    if not isinstance(b, bytes):
        return str(b) if b is not None else ''
    if b.startswith(b'\xfe\xff'):
        return b[2:].decode('utf-16-be', 'replace')
    if b.startswith(b'\xff\xfe'):
        return b[2:].decode('utf-16-le', 'replace')
    if b.startswith(b'\xef\xbb\xbf'):
        return b[3:].decode('utf-8', 'replace')
    return ''.join(PDFDOC[c] for c in b)


# Larguras (1/1000 em) da Helvetica para estimar fontes sem /Widths.
_HELV = dict(zip(
    ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~',
    (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278) + (556,) * 10 +
    (278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
     722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556,
     556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556,
     500, 722, 500, 500, 500, 334, 260, 334, 584)))


# ===================================================================== CMaps (ToUnicode)

_RE_CMAP_TOK = re.compile(rb'<([0-9A-Fa-f\s]*)>|\[|\]|(begincodespacerange|endcodespacerange|beginbfchar|'
                          rb'endbfchar|beginbfrange|endbfrange|begincidrange|endcidrange|begincidchar|endcidchar)'
                          rb'|(-?\d+)|/[^\s/<>\[\]()]+|\([^)]*\)')


def _utf16(b):
    try:
        return b.decode('utf-16-be')
    except UnicodeDecodeError:
        return b.decode('utf-16-be', 'replace')


def ler_cmap(dados):
    """Retorna (mapa {(nbytes, codigo): texto}, espacos [(nbytes, lo, hi)])."""
    mapa = {}
    espacos = []
    modo = None
    pilha = []
    arr = None
    for m in _RE_CMAP_TOK.finditer(dados):
        hx, palavra, num = m.group(1), m.group(2), m.group(3)
        t = m.group(0)
        if palavra:
            modo = palavra.decode()
            pilha = []
            continue
        if t == b'[':
            arr = []
            continue
        if t == b']':
            pilha.append(arr)
            arr = None
        elif hx is not None:
            v = bytes.fromhex((re.sub(rb'\s', b'', hx) + (b'0' if len(re.sub(rb'\s', b'', hx)) % 2 else b''))
                              .decode('ascii'))
            if arr is not None:
                arr.append(v)
                continue
            pilha.append(v)
        elif num is not None:
            if arr is not None:
                continue
            pilha.append(int(num))
        else:
            continue
        if modo == 'begincodespacerange' and len(pilha) == 2:
            lo, hi = pilha
            if isinstance(lo, bytes) and isinstance(hi, bytes):
                espacos.append((len(lo), int.from_bytes(lo, 'big'), int.from_bytes(hi, 'big')))
            pilha = []
        elif modo == 'beginbfchar' and len(pilha) == 2:
            src, dst = pilha
            if isinstance(src, bytes):
                if isinstance(dst, bytes):
                    mapa[(len(src), int.from_bytes(src, 'big'))] = _utf16(dst)
            pilha = []
        elif modo == 'beginbfrange' and len(pilha) == 3:
            lo, hi, dst = pilha
            if isinstance(lo, bytes) and isinstance(hi, bytes):
                nb = len(lo)
                a, z = int.from_bytes(lo, 'big'), int.from_bytes(hi, 'big')
                if z - a > 65535:
                    z = a + 65535
                if isinstance(dst, list):
                    for k, item in enumerate(dst[:z - a + 1]):
                        if isinstance(item, bytes):
                            mapa[(nb, a + k)] = _utf16(item)
                elif isinstance(dst, bytes) and dst:
                    base = int.from_bytes(dst, 'big')
                    tam = len(dst)
                    for k in range(z - a + 1):
                        try:
                            mapa[(nb, a + k)] = _utf16((base + k).to_bytes(tam, 'big'))
                        except OverflowError:
                            break
            pilha = []
        elif modo in ('begincidrange', 'begincidchar'):
            if (modo == 'begincidrange' and len(pilha) == 3) or (modo == 'begincidchar' and len(pilha) == 2):
                lo = pilha[0]
                if isinstance(lo, bytes):
                    espacos_n = len(lo)
                    if not any(e[0] == espacos_n for e in espacos):
                        espacos.append((espacos_n, 0, (1 << (8 * espacos_n)) - 1))
                pilha = []
    return mapa, espacos


# ===================================================================== documento PDF

class PDF(object):
    def __init__(self, dados):
        self.d = dados
        self.objs = {}          # num -> [pos, gen, obj, stream_bruto]
        self.avisos = []
        self.dec = None
        self.num_encrypt = None
        self._varrer()
        self.trailer = self._trailer()
        enc = self.trailer.get('Encrypt')
        if enc is not None:
            if isinstance(enc, Ref):
                self.num_encrypt = enc.n
            encd = self.r(enc)
            ids = self.r(self.trailer.get('ID')) or [b'']
            id0 = self.r(ids[0]) if ids else b''
            if not isinstance(id0, bytes):
                id0 = b''
            self.dec = Decifrador(encd, id0)   # pode levantar SenhaNecessaria/FiltroNaoSuportado
            self._decifrar_tudo()
        self._expandir_objstm()

    # ---------------------------------------------------------------- varredura de objetos
    def _varrer(self):
        d = self.d
        pos = 0
        rx = re.compile(rb'(\d+)\s+(\d+)\s+obj\b')
        while True:
            m = rx.search(d, pos)
            if not m:
                break
            num, gen = int(m.group(1)), int(m.group(2))
            try:
                obj, p = objeto(d, m.end())
            except (ValueError, IndexError, RecursionError):
                pos = m.end()
                continue
            bruto = None
            p2 = _pular(d, p)
            if isinstance(obj, dict) and d.startswith(b'stream', p2):
                s = p2 + 6
                if d[s:s + 2] == b'\r\n':
                    s += 2
                elif d[s:s + 1] in (b'\n', b'\r'):
                    s += 1
                fim = None
                L = obj.get('Length')
                if isinstance(L, int) and 0 <= L <= len(d) - s:
                    if re.match(rb'\s*endstream', d[s + L:s + L + 40]):
                        fim = s + L
                if fim is None:
                    e = d.find(b'endstream', s)
                    fim = e if e >= 0 else len(d)
                    if d[fim - 2:fim] == b'\r\n':
                        fim -= 2
                    elif d[fim - 1:fim] in (b'\n', b'\r'):
                        fim -= 1
                bruto = d[s:fim]
                p = fim + 9
            self.objs[num] = [m.start(), gen, obj, bruto]
            pos = max(p, m.end())

    def _trailer(self):
        cand = []
        for m in re.finditer(rb'trailer\s*<<', self.d):
            try:
                t, _ = objeto(self.d, m.end() - 2)
                if isinstance(t, dict):
                    cand.append((m.start(), t))
            except (ValueError, IndexError):
                continue
        for num, (pos, gen, obj, bruto) in self.objs.items():
            if isinstance(obj, dict) and obj.get('Type') == 'XRef':
                cand.append((pos, obj))
        cand.sort(key=lambda x: x[0])
        tr = {}
        for _, t in cand:
            for k in ('Root', 'Encrypt', 'ID', 'Info'):
                if k in t and t[k] is not None:
                    tr[k] = t[k]
        if 'Root' not in tr:
            for num, (pos, gen, obj, bruto) in self.objs.items():
                if isinstance(obj, dict) and obj.get('Type') == 'Catalog':
                    tr['Root'] = Ref(num, gen)
        return tr

    def _decifrar_tudo(self):
        for num, item in self.objs.items():
            if num == self.num_encrypt:
                continue
            pos, gen, obj, bruto = item
            if isinstance(obj, dict) and obj.get('Type') == 'XRef':
                continue
            item[2] = self._decifrar_obj(obj, num, gen)
            if bruto is not None:
                if isinstance(obj, dict) and obj.get('Type') == 'Metadata' and not self.dec.encrypt_metadata:
                    continue
                filtros = obj.get('Filter') if isinstance(obj, dict) else None
                if filtros == 'Crypt' or (isinstance(filtros, list) and 'Crypt' in filtros):
                    continue
                item[3] = self.dec.decifrar(bruto, num, gen, True)

    def _decifrar_obj(self, obj, num, gen):
        if isinstance(obj, bytes):
            return self.dec.decifrar(obj, num, gen, False)
        if isinstance(obj, list):
            return [self._decifrar_obj(x, num, gen) for x in obj]
        if isinstance(obj, dict):
            return {k: self._decifrar_obj(v, num, gen) for k, v in obj.items()}
        return obj

    def _expandir_objstm(self):
        conteineres = [(num, item) for num, item in self.objs.items()
                       if isinstance(item[2], dict) and item[2].get('Type') == 'ObjStm' and item[3] is not None]
        for num, (pos, gen, obj, bruto) in sorted(conteineres, key=lambda x: x[1][0]):
            dados = self.decodificar(obj, bruto)
            if not dados:
                continue
            n = int(self.r(obj.get('N', 0)) or 0)
            primeiro = int(self.r(obj.get('First', 0)) or 0)
            cab = re.findall(rb'\d+', dados[:primeiro])
            pares = [(int(cab[i]), int(cab[i + 1])) for i in range(0, min(len(cab), 2 * n) - 1, 2)]
            for onum, off in pares:
                try:
                    o, _ = objeto(dados, primeiro + off)
                except (ValueError, IndexError, RecursionError):
                    continue
                atual = self.objs.get(onum)
                if atual is None or atual[0] <= pos:
                    self.objs[onum] = [pos + 0.5, 0, o, None]

    # ---------------------------------------------------------------- acesso
    def r(self, x, prof=0):
        while isinstance(x, Ref) and prof < 32:
            item = self.objs.get(x.n)
            x = item[2] if item else None
            prof += 1
        return x

    def stream_de(self, ref_ou_obj):
        """(dicionario, dados_decodificados) de um stream referenciado."""
        if isinstance(ref_ou_obj, Ref):
            item = self.objs.get(ref_ou_obj.n)
            if not item or item[3] is None:
                return None, None
            return item[2], self.decodificar(item[2], item[3])
        return None, None

    def decodificar(self, dic, dados):
        filtros = self.r(dic.get('Filter')) if isinstance(dic, dict) else None
        parms = self.r(dic.get('DecodeParms')) if isinstance(dic, dict) else None
        if filtros is None:
            return dados
        if not isinstance(filtros, list):
            filtros = [filtros]
            parms = [parms]
        elif not isinstance(parms, list):
            parms = [parms] * len(filtros)
        for i, f in enumerate(filtros):
            f = self.r(f)
            p = self.r(parms[i]) if i < len(parms) else None
            p = p if isinstance(p, dict) else {}
            if f in ('FlateDecode', 'Fl'):
                dados = _flate(dados)
            elif f in ('LZWDecode', 'LZW'):
                dados = _lzw(dados, int(p.get('EarlyChange', 1) or 0))
            elif f in ('ASCIIHexDecode', 'AHx'):
                dados = _ahx(dados)
                continue
            elif f in ('ASCII85Decode', 'A85'):
                dados = _a85(dados)
                continue
            elif f in ('RunLengthDecode', 'RL'):
                dados = _rl(dados)
                continue
            elif f == 'Crypt':
                continue
            else:
                return None     # imagem (DCT/JPX/JBIG2/CCITT) ou desconhecido
            pred = int(p.get('Predictor', 1) or 1)
            if pred >= 10:
                dados = _png(dados, p)
        return dados

    # ---------------------------------------------------------------- páginas
    def paginas(self):
        raiz = self.r(self.trailer.get('Root'))
        saida = []
        if isinstance(raiz, dict):
            vistos = set()

            def andar(no_ref, herdado, prof):
                no = self.r(no_ref)
                if not isinstance(no, dict) or prof > 64:
                    return
                chave = no_ref.n if isinstance(no_ref, Ref) else id(no)
                if chave in vistos:
                    return
                vistos.add(chave)
                h = dict(herdado)
                for k in ('Resources', 'MediaBox', 'Rotate'):
                    if k in no:
                        h[k] = no[k]
                kids = self.r(no.get('Kids'))
                if no.get('Type') == 'Pages' or (kids and no.get('Type') != 'Page'):
                    for k in kids or []:
                        andar(k, h, prof + 1)
                else:
                    pg = dict(no)
                    for k, v in h.items():
                        pg.setdefault(k, v)
                    saida.append(pg)
            andar(raiz.get('Pages'), {}, 0)
        if not saida:
            for num in sorted(self.objs):
                o = self.objs[num][2]
                if isinstance(o, dict) and o.get('Type') == 'Page':
                    saida.append(o)
        return saida

    def conteudo(self, pagina):
        c = pagina.get('Contents')
        partes = []
        refs = self.r(c) if isinstance(c, Ref) and isinstance(self.r(c), list) else c
        if not isinstance(refs, list):
            refs = [refs]
        for ref in refs:
            _, dados = self.stream_de(ref)
            if dados:
                partes.append(dados)
        return b'\n'.join(partes)


# ===================================================================== fontes

class Fonte(object):
    def __init__(self, pdf, fd):
        self.subtipo = fd.get('Subtype')
        self.composta = self.subtipo == 'Type0'
        self.mapa = {}
        self.espacos = []
        self.larguras = {}
        self.largura_padrao = None
        self.fm = 0.001
        self.cod = None
        self.monoespacada = False
        self.sem_unicode = False
        nome_base = str(fd.get('BaseFont') or '')
        self.monoespacada = 'Courier' in nome_base or 'Mono' in nome_base
        tu = fd.get('ToUnicode')
        if isinstance(tu, Ref):
            _, dados = pdf.stream_de(tu)
            if dados:
                self.mapa, self.espacos = ler_cmap(dados)
        if self.composta:
            enc = fd.get('Encoding')
            if isinstance(enc, Ref):
                _, dados = pdf.stream_de(enc)
                if dados:
                    _, esp = ler_cmap(dados)
                    if esp:
                        self.espacos = esp
            desc = pdf.r(fd.get('DescendantFonts'))
            if isinstance(desc, list) and desc:
                df = pdf.r(desc[0])
                if isinstance(df, dict):
                    dw = pdf.r(df.get('DW'))
                    self.largura_padrao = float(dw) if isinstance(dw, (int, float)) else 1000.0
                    w = pdf.r(df.get('W'))
                    if isinstance(w, list):
                        self._ler_w([pdf.r(x) for x in w])
            if self.largura_padrao is None:
                self.largura_padrao = 1000.0
            if not self.espacos:
                self.espacos = [(2, 0, 0xFFFF)]
            if not self.mapa:
                self.sem_unicode = True
        else:
            self.cod = list(STANDARD) if self.subtipo == 'Type1' else list(WIN_ANSI)
            enc = pdf.r(fd.get('Encoding'))
            if isinstance(enc, str) and enc in BASES:
                self.cod = list(BASES[enc])
            elif isinstance(enc, dict):
                base = enc.get('BaseEncoding')
                if base in BASES:
                    self.cod = list(BASES[base])
                dif = pdf.r(enc.get('Differences'))
                if isinstance(dif, list):
                    codigo = 0
                    for x in dif:
                        x = pdf.r(x)
                        if isinstance(x, (int, float)):
                            codigo = int(x)
                        elif isinstance(x, str):
                            if 0 <= codigo < 256:
                                u = glifo_para_unicode(x)
                                self.cod[codigo] = u
                            codigo += 1
            fc = pdf.r(fd.get('FirstChar'))
            ws = pdf.r(fd.get('Widths'))
            if isinstance(fc, (int, float)) and isinstance(ws, list):
                for i, wv in enumerate(ws):
                    wv = pdf.r(wv)
                    if isinstance(wv, (int, float)):
                        self.larguras[int(fc) + i] = float(wv)
            fmx = pdf.r(fd.get('FontMatrix'))
            if self.subtipo == 'Type3' and isinstance(fmx, list) and fmx:
                try:
                    self.fm = float(pdf.r(fmx[0]))
                except (TypeError, ValueError):
                    self.fm = 0.001

    def _ler_w(self, w):
        i = 0
        while i < len(w):
            ini = w[i]
            if i + 1 < len(w) and isinstance(w[i + 1], list):
                for k, v in enumerate(w[i + 1]):
                    if isinstance(v, (int, float)):
                        self.larguras[int(ini) + k] = float(v)
                i += 2
            elif i + 2 < len(w):
                a, b, v = w[i], w[i + 1], w[i + 2]
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and isinstance(v, (int, float)):
                    if b - a <= 65535:
                        for c in range(int(a), int(b) + 1):
                            self.larguras[c] = float(v)
                i += 3
            else:
                break

    def codigos(self, s):
        """Divide a string em (codigo, nbytes)."""
        if not self.composta:
            return [(b, 1) for b in s]
        saida = []
        i = 0
        n = len(s)
        esp = self.espacos
        while i < n:
            achou = False
            for nb in (1, 2, 3, 4):
                if i + nb > n:
                    break
                v = int.from_bytes(s[i:i + nb], 'big')
                for (enb, lo, hi) in esp:
                    if enb == nb and lo <= v <= hi:
                        saida.append((v, nb))
                        i += nb
                        achou = True
                        break
                if achou:
                    break
            if not achou:
                nb = 2 if i + 2 <= n else 1
                saida.append((int.from_bytes(s[i:i + nb], 'big'), nb))
                i += nb
        return saida

    def decodificar(self, s):
        """[(texto, largura_em_unidades_de_texto, eh_espaco)]"""
        saida = []
        for cod, nb in self.codigos(s):
            u = self.mapa.get((nb, cod))
            if u is None:
                if self.composta:
                    u = '�'
                else:
                    u = self.cod[cod] if cod < 256 else ''
            if cod in self.larguras:
                w = self.larguras[cod] * self.fm
            elif self.largura_padrao is not None:
                w = self.largura_padrao * self.fm
            elif self.monoespacada:
                w = 0.6
            else:
                w = (_HELV.get(u[:1], 556) if u else 556) / 1000.0
            saida.append((u, w, nb == 1 and cod == 32))
        return saida


# ===================================================================== interpretação do conteúdo

_RE_CONTEUDO = re.compile(
    rb'(?P<ws>[\s\x00]+)|(?P<com>%[^\r\n]*)|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))|(?P<nome>/[^\s()<>\[\]{}/%]*)'
    rb'|(?P<lit>\()|(?P<hex><(?!<)[0-9A-Fa-f\s]*>)|(?P<dab><<)|(?P<dfe>>>)|(?P<aab>\[)|(?P<afe>\])'
    rb'|(?P<op>[^\s()<>\[\]{}/%]+)|(?P<outro>.)', re.S)
_ID_FIM = re.compile(rb'\sEI(?=[\s\x00]|$)')

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mult(m1, m2):
    a, b, c, d, e, f = m1
    A, B, C, D, E, F = m2
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


class _Run(object):
    __slots__ = ('x0', 'y0', 'x1', 'y1', 'txt', 'h')


class _FontePadrao(Fonte):
    def __init__(self):
        self.composta = False
        self.mapa = {}
        self.espacos = []
        self.larguras = {}
        self.largura_padrao = None
        self.fm = 0.001
        self.cod = list(WIN_ANSI)
        self.monoespacada = False
        self.sem_unicode = False


_FONTE_PADRAO = _FontePadrao()


class Interpretador(object):
    def __init__(self, pdf):
        self.pdf = pdf
        self.fontes_cache = {}
        self.runs = []
        self.tem_imagem = False
        self.caracteres_ruins = 0
        self.caracteres = 0
        self.invisiveis = 0       # caracteres com Tr 3/7 (camada de OCR de PDF pesquisável)
        self.imagens = []         # (num_objeto, ctm) de cada imagem desenhada
        self.tr = 0

    def _fonte(self, recursos, nome):
        fontes = self.pdf.r(recursos.get('Font')) if isinstance(recursos, dict) else None
        if not isinstance(fontes, dict):
            return None
        ref = fontes.get(nome)
        chave = ref.n if isinstance(ref, Ref) else (id(recursos), nome)
        if chave in self.fontes_cache:
            return self.fontes_cache[chave]
        fd = self.pdf.r(ref)
        f = Fonte(self.pdf, fd) if isinstance(fd, dict) else None
        self.fontes_cache[chave] = f
        return f

    def executar(self, dados, recursos, ctm=IDENT, prof=0, vistos=None):
        pdf = self.pdf
        recursos = pdf.r(recursos) if recursos is not None else {}
        if not isinstance(recursos, dict):
            recursos = {}
        vistos = vistos or set()
        pilha_g = []
        fonte, tam, Tc, Tw, Th, TL, Ts = None, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0
        self.tr = 0
        Tm = Tlm = IDENT
        ops = []
        pilha_arr = []
        n = len(dados)
        p = 0
        rx = _RE_CONTEUDO
        while p < n:
            m = rx.match(dados, p)
            if not m:
                p += 1
                continue
            g = m.lastgroup
            p = m.end()
            if g == 'ws' or g == 'com' or g == 'outro':
                continue
            if g == 'num':
                t = m.group()
                try:
                    v = float(t)
                except ValueError:
                    v = 0.0
                (pilha_arr[-1] if pilha_arr else ops).append(v)
                continue
            if g == 'nome':
                (pilha_arr[-1] if pilha_arr else ops).append(Nome(m.group()[1:].decode('latin-1')))
                continue
            if g == 'lit':
                s, p = _literal(dados, m.start())
                (pilha_arr[-1] if pilha_arr else ops).append(s)
                continue
            if g == 'hex':
                h = _RE_HEX.sub(b'', m.group()[1:-1])
                if len(h) % 2:
                    h += b'0'
                (pilha_arr[-1] if pilha_arr else ops).append(bytes.fromhex(h.decode('ascii')))
                continue
            if g == 'aab':
                pilha_arr.append([])
                continue
            if g == 'afe':
                if pilha_arr:
                    a = pilha_arr.pop()
                    (pilha_arr[-1] if pilha_arr else ops).append(a)
                continue
            if g in ('dab', 'dfe'):
                continue
            op = m.group()
            if op == b'BI':
                k = dados.find(b'ID', p)
                if k < 0:
                    break
                fim = _ID_FIM.search(dados, k + 3)
                p = fim.end() if fim else n
                self.tem_imagem = True
                ops = []
                continue
            # ---------------------------------------------------------------- operadores
            try:
                if op == b'Tj' or op == b"'" or op == b'"':
                    if op == b"'":
                        Tlm = _mult((1, 0, 0, 1, 0, -TL), Tlm)
                        Tm = Tlm
                    elif op == b'"':
                        if len(ops) >= 3:
                            Tw, Tc = float(ops[-3]), float(ops[-2])
                        Tlm = _mult((1, 0, 0, 1, 0, -TL), Tlm)
                        Tm = Tlm
                    if ops and isinstance(ops[-1], bytes):
                        Tm = self._mostrar(ops[-1], fonte, tam, Tc, Tw, Th, Ts, Tm, ctm)
                elif op == b'TJ':
                    if ops and isinstance(ops[-1], list):
                        for el in ops[-1]:
                            if isinstance(el, bytes):
                                Tm = self._mostrar(el, fonte, tam, Tc, Tw, Th, Ts, Tm, ctm)
                            elif isinstance(el, (int, float)):
                                Tm = _mult((1, 0, 0, 1, -el / 1000.0 * tam * Th, 0), Tm)
                elif op == b'Td' and len(ops) >= 2:
                    Tlm = _mult((1, 0, 0, 1, float(ops[-2]), float(ops[-1])), Tlm)
                    Tm = Tlm
                elif op == b'TD' and len(ops) >= 2:
                    TL = -float(ops[-1])
                    Tlm = _mult((1, 0, 0, 1, float(ops[-2]), float(ops[-1])), Tlm)
                    Tm = Tlm
                elif op == b'Tm' and len(ops) >= 6:
                    Tm = Tlm = tuple(float(x) for x in ops[-6:])
                elif op == b'T*':
                    Tlm = _mult((1, 0, 0, 1, 0, -TL), Tlm)
                    Tm = Tlm
                elif op == b'Tf' and len(ops) >= 2:
                    fonte = self._fonte(recursos, ops[-2])
                    tam = float(ops[-1])
                elif op == b'Tc' and ops:
                    Tc = float(ops[-1])
                elif op == b'Tw' and ops:
                    Tw = float(ops[-1])
                elif op == b'Tz' and ops:
                    Th = float(ops[-1]) / 100.0
                elif op == b'TL' and ops:
                    TL = float(ops[-1])
                elif op == b'Ts' and ops:
                    Ts = float(ops[-1])
                elif op == b'BT':
                    Tm = Tlm = IDENT
                elif op == b'Tr' and ops:
                    self.tr = int(ops[-1])
                elif op == b'q':
                    pilha_g.append((ctm, fonte, tam, Tc, Tw, Th, TL, Ts, self.tr))
                elif op == b'Q':
                    if pilha_g:
                        ctm, fonte, tam, Tc, Tw, Th, TL, Ts, self.tr = pilha_g.pop()
                elif op == b'cm' and len(ops) >= 6:
                    ctm = _mult(tuple(float(x) for x in ops[-6:]), ctm)
                elif op == b'Do' and ops and isinstance(ops[-1], str):
                    self._xobjeto(recursos, ops[-1], ctm, prof, vistos)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            ops = []

    def _xobjeto(self, recursos, nome, ctm, prof, vistos):
        xs = self.pdf.r(recursos.get('XObject'))
        if not isinstance(xs, dict):
            return
        ref = xs.get(nome)
        if not isinstance(ref, Ref):
            return
        item = self.pdf.objs.get(ref.n)
        if not item:
            return
        dic = item[2]
        if not isinstance(dic, dict):
            return
        sub = dic.get('Subtype')
        if sub == 'Image':
            self.tem_imagem = True
            self.imagens.append((ref.n, ctm))
            return
        if sub != 'Form' or prof >= PROFUNDIDADE_FORM or ref.n in vistos:
            return
        _, dados = self.pdf.stream_de(ref)
        if not dados:
            return
        mat = self.pdf.r(dic.get('Matrix'))
        if isinstance(mat, list) and len(mat) == 6:
            try:
                ctm = _mult(tuple(float(self.pdf.r(x)) for x in mat), ctm)
            except (TypeError, ValueError):
                pass
        rec = dic.get('Resources')
        self.executar(dados, rec if rec is not None else recursos, ctm, prof + 1, vistos | {ref.n})

    def _mostrar(self, s, fonte, tam, Tc, Tw, Th, Ts, Tm, ctm):
        if fonte is None:
            fonte = _FONTE_PADRAO       # texto sem Tf (PDF malformado): lê como WinAnsi
            tam = tam or 10.0
        glifos = fonte.decodificar(s)
        M = _mult(Tm, ctm)
        x0, y0 = M[4], M[5]
        avanco = 0.0
        partes = []
        for u, w, espaco in glifos:
            partes.append(u)
            avanco += (w * tam + Tc + (Tw if espaco else 0.0)) * Th
        Tm = _mult((1, 0, 0, 1, avanco, 0), Tm)
        M2 = _mult(Tm, ctm)
        txt = ''.join(partes)
        self.caracteres += len(txt)
        if self.tr in (3, 7):
            self.invisiveis += len(txt.strip())
        self.caracteres_ruins += txt.count('�')
        r = _Run()
        r.x0, r.y0, r.x1, r.y1, r.txt = x0, y0, M2[4], M2[5], txt
        escala = math.hypot(M[2], M[3]) or 1.0
        r.h = abs(tam) * escala or 1.0
        self.runs.append(r)
        return Tm

    def texto(self):
        partes = []
        ant = None
        for r in self.runs:
            if not r.txt:
                continue
            if ant is not None:
                if r.txt == ant.txt and abs(r.x0 - ant.x0) < 1.5 and abs(r.y0 - ant.y0) < 1.5:
                    continue    # texto sobreposto ("negrito falso")
                h = max(ant.h, r.h, 1.0)
                dy = abs(r.y0 - ant.y1)
                if dy > 0.5 * h:
                    partes.append('\n')
                else:
                    gap = r.x0 - ant.x1
                    if (gap > 0.15 * h or gap < -2.0 * h) and not ant.txt.endswith(' ') \
                            and not r.txt.startswith(' '):
                        partes.append(' ')
            partes.append(r.txt)
            ant = r
        return ''.join(partes)


# ===================================================================== entrada

def ler_pdf(doc, dados):
    try:
        pdf = PDF(dados)
    except SenhaNecessaria:
        doc.aviso('PDF protegido por SENHA DE ABERTURA: não é possível ler sem a senha. '
                  'Abra-o com a senha e salve/imprima uma cópia em PDF sem proteção.')
        return
    except FiltroNaoSuportado as e:
        doc.aviso('PDF com proteção não suportada (%s)' % e)
        return
    if pdf.dec is not None:
        doc.info['criptografia'] = 'R%d (senha de abertura vazia)' % pdf.dec.R
    paginas = pdf.paginas()
    doc.info['paginas'] = len(paginas)
    if not paginas:
        doc.aviso('Estrutura de páginas do PDF não encontrada: arquivo truncado ou corrompido')
    elif b'%%EOF' not in dados[-2048:]:
        doc.aviso('PDF sem marcador de fim (%%EOF): arquivo possivelmente incompleto')
    sem_texto, ilegiveis, camada_ocr = [], [], []
    imagens = []          # (pagina, num_objeto, ctm) candidatas a OCR, sem repetição de objeto
    vistas = set()
    for i, pg in enumerate(paginas, 1):
        it = Interpretador(pdf)
        try:
            it.executar(pdf.conteudo(pg), pg.get('Resources'))
        except RecursionError:
            doc.aviso('pág. %d: conteúdo com aninhamento excessivo (lido parcialmente)' % i)
        texto = it.texto()
        anot = _anotacoes(pdf, pg)
        if texto.strip():
            doc.texto('pág. %d' % i, texto)
        if it.invisiveis >= 20 and it.invisiveis >= 0.5 * it.caracteres:
            camada_ocr.append(i)        # PDF pesquisável: a camada de OCR já existe, não refaz
        else:
            for num, ctm in it.imagens:
                if num not in vistas:
                    vistas.add(num)
                    imagens.append((i, num, ctm))
        if not texto.strip() and (it.tem_imagem or not anot):
            sem_texto.append(i)
        if it.caracteres and it.caracteres_ruins > 0.2 * it.caracteres:
            ilegiveis.append(i)
        if anot:
            doc.texto('pág. %d, anotações' % i, anot)
    if camada_ocr:
        doc.info['camada_ocr'] = 'já existente nas págs. %s' % _faixas(camada_ocr)
    lidas_por_ocr = _ocr_paginas(pdf, doc, imagens, sem_texto)
    sem_texto = [p for p in sem_texto if p not in lidas_por_ocr]
    if sem_texto:
        from . import ocr
        if ocr.ativo():
            motivo = 'o OCR não encontrou texto legível'
        elif ocr.motor() is not None:
            motivo = 'OCR desligado'
        else:
            motivo = 'instale o Tesseract para ativar o OCR (ver README)'
        doc.aviso('Página(s) sem camada de texto (provável digitalização): %s — %s' % (_faixas(sem_texto), motivo))
    if ilegiveis:
        doc.aviso('Página(s) com fonte sem mapa Unicode — texto pode sair incorreto: %s' % _faixas(ilegiveis))
    _formulario(pdf, doc)
    _info(pdf, doc)


# ===================================================================== imagens para OCR

_FILTROS_IMAGEM = {'DCTDecode': 'jpeg', 'DCT': 'jpeg', 'JPXDecode': 'jp2', 'CCITTFaxDecode': 'ccitt',
                   'CCF': 'ccitt', 'JBIG2Decode': 'jbig2'}


def _ocr_paginas(pdf, doc, imagens, sem_texto):
    """Faz OCR das imagens relevantes. Retorna o conjunto de páginas que ganharam texto."""
    from . import ocr
    if not imagens:
        return set()
    tarefas, nomes, erros, ignoradas = [], {}, [], 0
    for pagina, num, ctm in imagens:
        dic = pdf.objs[num][2]
        w = _int(pdf.r(dic.get('Width')))
        h = _int(pdf.r(dic.get('Height')))
        if not ocr.vale_ocr(w, h):
            continue
        if not ocr.ativo():
            if pagina not in sem_texto:
                ignoradas += 1      # figura dentro de página com texto (ex.: print colado num relatório)
            continue
        try:
            img = imagem_para_ocr(pdf, num)
        except Exception as e:      # imagem corrompida não derruba a leitura
            img, motivo = None, '%s: %s' % (type(e).__name__, e)
        else:
            img, motivo = img
        if img is None:
            erros.append('pág. %d: %s' % (pagina, motivo))
            continue
        larg_pt = math.hypot(ctm[0], ctm[1])
        alt_pt = math.hypot(ctm[2], ctm[3])
        dpi = None
        if larg_pt > 1 and alt_pt > 1:
            dpi = (w * 72.0 / larg_pt + h * 72.0 / alt_pt) / 2
        chave = (pagina, num)
        tarefas.append((chave, img, dpi))
        nomes[chave] = pagina
    if ignoradas:
        doc.aviso('%d imagem(ns) dentro de páginas com texto não lida(s): %s' % (
            ignoradas, 'OCR desligado' if ocr.motor() is not None else 'Tesseract não instalado (ver README)'))
    for e in erros[:10]:
        doc.aviso('Imagem não lida por OCR — ' + e)
    if not tarefas:
        return set()
    resultado = ocr.ler_lote(tarefas, 'imagem do PDF')
    com_texto = set()
    por_pagina = {}
    for chave, _, _ in tarefas:
        texto, erro = resultado.get(chave, ('', 'sem resultado'))
        if erro:
            doc.aviso('OCR falhou na pág. %d: %s' % (chave[0], erro))
            continue
        if texto.strip():
            por_pagina.setdefault(chave[0], []).append(texto)
            com_texto.add(chave[0])
    for pagina in sorted(por_pagina):
        doc.texto('pág. %d (OCR)' % pagina, '\n'.join(por_pagina[pagina]), ocr=True)
    vazias = sorted(set(nomes.values()) - com_texto)
    if vazias:
        doc.aviso('OCR não reconheceu texto nas imagens das págs. %s' % _faixas(vazias))
    doc.info['ocr'] = '%s: %d imagem(ns) em %d página(s)' % (ocr.motor().descricao(), len(tarefas),
                                                             len(set(nomes.values())))
    return com_texto


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _espaco_cor(pdf, cs):
    """('gray'|'rgb'|'cmyk'|'sep'|'indexed', componentes, extra) ou (None, 0, motivo)."""
    cs = pdf.r(cs)
    nome = pdf.r(cs[0]) if isinstance(cs, list) and cs else cs
    if nome in ('DeviceGray', 'G', 'CalGray'):
        return 'gray', 1, None
    if nome in ('DeviceRGB', 'RGB', 'CalRGB'):
        return 'rgb', 3, None
    if nome in ('DeviceCMYK', 'CMYK'):
        return 'cmyk', 4, None
    if nome == 'ICCBased' and isinstance(cs, list) and len(cs) > 1:
        item = pdf.objs.get(cs[1].n) if isinstance(cs[1], Ref) else None
        n = _int(pdf.r(item[2].get('N'))) if item and isinstance(item[2], dict) else 0
        return {1: ('gray', 1, None), 3: ('rgb', 3, None), 4: ('cmyk', 4, None)}.get(n, (None, 0, 'ICC N=%d' % n))
    if nome in ('Indexed', 'I') and isinstance(cs, list) and len(cs) >= 4:
        base = _espaco_cor(pdf, cs[1])
        hival = _int(pdf.r(cs[2]))
        tabela = cs[3]
        if isinstance(tabela, Ref):
            _, tabela = pdf.stream_de(tabela)
        tabela = pdf.r(tabela)
        if not isinstance(tabela, bytes) or base[0] not in ('gray', 'rgb', 'cmyk'):
            return None, 0, 'paleta não suportada'
        n = base[1]
        tabela = tabela[:n * (hival + 1)]
        if base[0] == 'gray':
            tabela = bytes(b for v in tabela for b in (v, v, v))
        elif base[0] == 'cmyk':
            from .ocr import cmyk_para_rgb
            tabela = cmyk_para_rgb(tabela)
        return 'indexed', 1, tabela
    if nome == 'Separation':
        return 'sep', 1, None
    if nome == 'DeviceN' and isinstance(cs, list) and len(cs) > 1 and isinstance(pdf.r(cs[1]), list) \
            and len(pdf.r(cs[1])) == 1:
        return 'sep', 1, None
    return None, 0, 'espaço de cor %s não suportado' % nome


def imagem_para_ocr(pdf, num):
    """(bytes_de_imagem, None) prontos para o Tesseract, ou (None, motivo)."""
    from . import ocr
    dic, bruto = pdf.objs[num][2], pdf.objs[num][3]
    if bruto is None:
        return None, 'imagem sem dados'
    w, h = _int(pdf.r(dic.get('Width'))), _int(pdf.r(dic.get('Height')))
    filtros = pdf.r(dic.get('Filter'))
    parms = pdf.r(dic.get('DecodeParms'))
    if not isinstance(filtros, list):
        filtros = [filtros] if filtros is not None else []
        parms = [parms]
    elif not isinstance(parms, list):
        parms = [parms] * len(filtros)
    filtros = [pdf.r(f) for f in filtros]
    idx = next((k for k, f in enumerate(filtros) if f in _FILTROS_IMAGEM), None)
    decode = pdf.r(dic.get('Decode'))
    invertido = isinstance(decode, list) and len(decode) >= 2 and _num(pdf.r(decode[0])) > _num(pdf.r(decode[1]))
    mascara = pdf.r(dic.get('ImageMask')) is True
    if idx is not None:
        anteriores = {'Filter': filtros[:idx], 'DecodeParms': parms[:idx]} if idx else {}
        dados = pdf.decodificar(anteriores, bruto) if idx else bruto
        if dados is None:
            return None, 'filtro não suportado'
        tipo = _FILTROS_IMAGEM[filtros[idx]]
        if tipo in ('jpeg', 'jp2'):
            return dados, None
        if tipo == 'jbig2':
            return None, 'imagem JBIG2 (formato de compressão não suportado pelo OCR)'
        p = pdf.r(parms[idx]) if idx < len(parms) else None
        p = p if isinstance(p, dict) else {}
        k = _int(pdf.r(p.get('K', 0)))
        colunas = _int(pdf.r(p.get('Columns', 1728))) or w
        linhas = _int(pdf.r(p.get('Rows', 0))) or h
        preto1 = pdf.r(p.get('BlackIs1')) is True
        return ocr.tiff_ccitt(colunas, linhas, dados, k, preto1 != invertido), None
    dados = pdf.decodificar(dic, bruto)
    if dados is None:
        return None, 'filtro não suportado'
    bpc = 1 if mascara else (_int(pdf.r(dic.get('BitsPerComponent'))) or 8)
    if mascara:
        espaco, n, extra = 'gray', 1, None
    else:
        espaco, n, extra = _espaco_cor(pdf, dic.get('ColorSpace'))
    if espaco is None:
        return None, extra
    if espaco in ('gray', 'sep'):
        if bpc not in (1, 2, 4, 8, 16):
            return None, '%d bits por componente' % bpc
        if (espaco == 'sep') != invertido:
            dados = dados.translate(ocr.INVERTER)
        return ocr.png(w, h, bpc, 0, dados), None
    if espaco == 'rgb':
        if bpc not in (8, 16):
            return None, 'RGB com %d bits' % bpc
        return ocr.png(w, h, bpc, 2, dados), None
    if espaco == 'cmyk':
        if bpc != 8:
            return None, 'CMYK com %d bits' % bpc
        return ocr.png(w, h, 8, 2, ocr.cmyk_para_rgb(dados[:w * h * 4])), None
    if espaco == 'indexed':
        if bpc not in (1, 2, 4, 8):
            return None, 'paleta com %d bits' % bpc
        return ocr.png(w, h, bpc, 3, dados, paleta=extra), None
    return None, 'formato de imagem não suportado'


def _num(v):
    return float(v) if isinstance(v, (int, float)) else 0.0


def _faixas(nums):
    saida = []
    ini = ant = nums[0]
    for x in nums[1:] + [None]:
        if x is not None and x == ant + 1:
            ant = x
            continue
        saida.append(str(ini) if ini == ant else '%d-%d' % (ini, ant))
        if x is not None:
            ini = ant = x
    return ', '.join(saida)


def _anotacoes(pdf, pg):
    anots = pdf.r(pg.get('Annots'))
    linhas = []
    if not isinstance(anots, list):
        return ''
    for a in anots:
        a = pdf.r(a)
        if not isinstance(a, dict):
            continue
        for chave in ('T', 'Contents'):
            v = pdf.r(a.get(chave))
            if isinstance(v, bytes) and v.strip():
                linhas.append(texto_pdf(v))
        acao = pdf.r(a.get('A'))
        if isinstance(acao, dict):
            uri = pdf.r(acao.get('URI'))
            if isinstance(uri, bytes) and uri.lower().startswith((b'mailto:', b'tel:')):
                linhas.append(texto_pdf(uri))
    return '\n'.join(linhas)


def _formulario(pdf, doc):
    raiz = pdf.r(pdf.trailer.get('Root'))
    if not isinstance(raiz, dict):
        return
    af = pdf.r(raiz.get('AcroForm'))
    if not isinstance(af, dict):
        return
    linhas = []
    vistos = set()

    def andar(ref, prefixo, prof):
        no = pdf.r(ref)
        if not isinstance(no, dict) or prof > 32:
            return
        if isinstance(ref, Ref):
            if ref.n in vistos:
                return
            vistos.add(ref.n)
        t = pdf.r(no.get('T'))
        nome = prefixo + ('.' if prefixo and t else '') + (texto_pdf(t) if isinstance(t, bytes) else '')
        v = pdf.r(no.get('V'))
        if isinstance(v, bytes) and v.strip():
            linhas.append('%s: %s' % (nome or 'campo', texto_pdf(v)))
        elif isinstance(v, str) and v not in ('Off',):
            linhas.append('%s: %s' % (nome or 'campo', v))
        for k in pdf.r(no.get('Kids')) or []:
            andar(k, nome, prof + 1)
    for f in pdf.r(af.get('Fields')) or []:
        andar(f, '', 0)
    if linhas:
        doc.texto('campos do formulário', '\n'.join(linhas))


def _info(pdf, doc):
    info = pdf.r(pdf.trailer.get('Info'))
    if not isinstance(info, dict):
        return
    linhas = []
    for k in ('Title', 'Author', 'Subject', 'Keywords', 'Creator', 'Producer'):
        v = pdf.r(info.get(k))
        if isinstance(v, bytes) and v.strip():
            linhas.append('%s: %s' % (k, texto_pdf(v)))
    if linhas:
        doc.texto('metadados do PDF', '\n'.join(linhas))
