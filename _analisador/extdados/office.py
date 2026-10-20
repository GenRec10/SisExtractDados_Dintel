# -*- coding: utf-8 -*-
"""Formatos Office Open XML (.docx, .xlsx, .pptx) e OpenDocument (.odt, .ods, .odp)."""
import posixpath
import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PR = '{http://schemas.openxmlformats.org/package/2006/relationships}'
TXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
TAB = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
OFF = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
DRAW = '{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}'

LIMITE_REPETICAO = 1000   # células/linhas repetidas (ODS) expandidas no máximo


def numero_texto(v):
    """'3.52099001761481E+14' -> '352099001761481'; '45123.5' -> '45123.5'. Sem notação científica."""
    v = (v or '').strip()
    if not v:
        return ''
    try:
        d = Decimal(v)
    except InvalidOperation:
        return v
    if d == d.to_integral_value() and abs(d) < Decimal('1e20'):
        return '%d' % int(d)
    t = format(d.normalize(), 'f')
    return t


def _ler_xml(z, nome):
    try:
        return ET.fromstring(z.read(nome))
    except (KeyError, ET.ParseError):
        return None


def metadados(doc, z):
    """docProps/core.xml e app.xml: autor, último a modificar, empresa (podem conter nomes)."""
    linhas = []
    for parte, campos in (('docProps/core.xml', ('creator', 'lastModifiedBy', 'title', 'subject',
                                                  'description', 'keywords', 'category')),
                          ('docProps/app.xml', ('Company', 'Manager')),
                          ('meta.xml', ('initial-creator', 'creator', 'title', 'subject', 'description'))):
        raiz = _ler_xml(z, parte)
        if raiz is None:
            continue
        for el in raiz.iter():
            nome = el.tag.rsplit('}', 1)[-1]
            if nome in campos and el.text and el.text.strip():
                linhas.append('%s: %s' % (nome, el.text.strip()))
    if linhas:
        doc.texto('metadados do arquivo', '\n'.join(linhas))


_PASTAS_MIDIA = ('word/media/', 'ppt/media/', 'xl/media/', 'Pictures/')
_EXT_IMAGEM = ('.png', '.jpg', '.jpeg', '.jpe', '.gif', '.bmp', '.tif', '.tiff', '.webp', '.jp2')


def imagens_embutidas(doc, z):
    """OCR das imagens coladas no documento (prints, digitalizações). Ícones pequenos são ignorados."""
    from . import ocr
    nomes = sorted(n for n in z.namelist()
                   if n.startswith(_PASTAS_MIDIA) and n.lower().endswith(_EXT_IMAGEM))
    tarefas = []
    for nome in nomes:
        info = z.getinfo(nome)
        if info.file_size > 64 * 1024 * 1024:
            continue
        dados = z.read(nome)
        dim = ocr.dimensoes(dados)
        if dim and not ocr.vale_ocr(*dim):
            continue
        tarefas.append((nome, dados, None))
    if not tarefas:
        return
    if not ocr.ativo():
        doc.aviso('%d imagem(ns) dentro do documento não lida(s): %s' % (
            len(tarefas), 'OCR desligado' if ocr.motor() is not None else 'Tesseract não instalado (ver README)'))
        return
    resultado = ocr.ler_lote(tarefas, 'imagem do documento')
    for nome, _, _ in tarefas:
        texto, erro = resultado.get(nome, ('', 'sem resultado'))
        if erro:
            doc.aviso('OCR falhou em %s: %s' % (nome, erro))
        elif texto.strip():
            doc.texto('imagem %s (OCR)' % nome.rsplit('/', 1)[-1], texto, ocr=True)
    doc.info['ocr'] = '%s: %d imagem(ns)' % (ocr.motor().descricao(), len(tarefas))


def _finalizar(doc, z):
    imagens_embutidas(doc, z)
    metadados(doc, z)


# ===================================================================== DOCX

def _texto_paragrafo_w(p):
    partes = []
    for el in p.iter():
        tag = el.tag
        if tag == W + 't':
            partes.append(el.text or '')
        elif tag == W + 'tab':
            partes.append('\t')
        elif tag in (W + 'br', W + 'cr'):
            partes.append('\n')
        elif tag == W + 'noBreakHyphen':
            partes.append('-')
    return ''.join(partes)


def _celula_w(tc):
    textos = []
    for p in tc.iter(W + 'p'):
        t = _texto_paragrafo_w(p).strip()
        if t:
            textos.append(t)
    return ' '.join(textos)


def _corpo_w(doc, raiz, nome_base):
    corpo = raiz.find(W + 'body')
    if corpo is None:
        corpo = raiz
    buffer = []
    n_tab = [0]

    def despejar():
        if buffer:
            doc.texto(nome_base, '\n'.join(buffer))
            del buffer[:]

    def andar(el):
        for filho in el:
            if filho.tag == W + 'p':
                buffer.append(_texto_paragrafo_w(filho))
            elif filho.tag == W + 'tbl':
                despejar()
                n_tab[0] += 1
                linhas = []
                for tr in filho.findall(W + 'tr'):
                    linhas.append([_celula_w(tc) for tc in tr.findall(W + 'tc')])
                doc.tabela('%s, tabela %d' % (nome_base, n_tab[0]) if nome_base != 'corpo'
                           else 'tabela %d' % n_tab[0], linhas)
            elif filho.tag in (W + 'sdt', W + 'sdtContent', W + 'customXml', W + 'ins', W + 'smartTag'):
                andar(filho)
    andar(corpo)
    despejar()


def ler_docx(doc, z):
    raiz = _ler_xml(z, 'word/document.xml')
    if raiz is None:
        doc.aviso('document.xml ausente ou inválido')
        return
    _corpo_w(doc, raiz, 'corpo')
    nomes = sorted(z.namelist())
    for nome in nomes:
        m = re.match(r'word/(header|footer)(\d*)\.xml$', nome)
        if m:
            r = _ler_xml(z, nome)
            if r is not None:
                _corpo_w(doc, r, 'cabeçalho' if m.group(1) == 'header' else 'rodapé')
    for nome, rotulo in (('word/footnotes.xml', 'notas de rodapé'), ('word/endnotes.xml', 'notas de fim'),
                         ('word/comments.xml', 'comentários')):
        r = _ler_xml(z, nome)
        if r is not None:
            textos = [_texto_paragrafo_w(p) for p in r.iter(W + 'p')]
            doc.texto(rotulo, '\n'.join(t for t in textos if t.strip()))
    _finalizar(doc, z)


# ===================================================================== PPTX

def ler_pptx(doc, z):
    slides = sorted((n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                    key=lambda n: int(re.search(r'(\d+)\.xml$', n).group(1)))
    for nome in slides:
        num = re.search(r'(\d+)\.xml$', nome).group(1)
        raiz = _ler_xml(z, nome)
        if raiz is None:
            continue
        linhas = []
        for tbl in raiz.iter(A + 'tbl'):
            tab = []
            for tr in tbl.iter(A + 'tr'):
                tab.append([' '.join(t.text or '' for t in tc.iter(A + 't')) for tc in tr.iter(A + 'tc')])
            doc.tabela('slide %s, tabela' % num, tab)
        for p in raiz.iter(A + 'p'):
            partes = []
            for el in p.iter():
                if el.tag == A + 't':
                    partes.append(el.text or '')
                elif el.tag == A + 'br':
                    partes.append('\n')
            linhas.append(''.join(partes))
        doc.texto('slide %s' % num, '\n'.join(linhas))
        notas = _ler_xml(z, 'ppt/notesSlides/notesSlide%s.xml' % num)
        if notas is not None:
            doc.texto('slide %s, notas' % num, '\n'.join(
                ''.join(t.text or '' for t in p.iter(A + 't')) for p in notas.iter(A + 'p')))
    _finalizar(doc, z)


# ===================================================================== XLSX

def _rels(z, caminho_parte):
    pasta, arquivo = posixpath.split(caminho_parte)
    raiz = _ler_xml(z, posixpath.join(pasta, '_rels', arquivo + '.rels'))
    mapa = {}
    if raiz is None:
        return mapa
    for rel in raiz.iter(PR + 'Relationship'):
        alvo = rel.get('Target', '')
        if alvo.startswith('/'):
            alvo = alvo[1:]
        else:
            alvo = posixpath.normpath(posixpath.join(pasta, alvo))
        mapa[rel.get('Id')] = alvo
    return mapa


def _col_indice(ref):
    n = 0
    for ch in ref:
        if 'A' <= ch <= 'Z':
            n = n * 26 + (ord(ch) - 64)
        else:
            break
    return n - 1


def _shared_strings(z, caminho):
    try:
        dados = z.open(caminho)
    except KeyError:
        return []
    saida = []
    for evento, el in ET.iterparse(dados, events=('end',)):
        if el.tag == S + 'si':
            saida.append(''.join(t.text or '' for t in el.iter(S + 't')))
            el.clear()
    return saida


def ler_xlsx(doc, z):
    wb = _ler_xml(z, 'xl/workbook.xml')
    if wb is None:
        doc.aviso('workbook.xml ausente ou inválido')
        return
    rels = _rels(z, 'xl/workbook.xml')
    sst_caminho = next((v for v in rels.values() if v.endswith('sharedStrings.xml')), 'xl/sharedStrings.xml')
    sst = _shared_strings(z, sst_caminho)
    folhas = wb.find(S + 'sheets')
    if folhas is None:
        return
    for folha in folhas.findall(S + 'sheet'):
        nome = folha.get('name', 'planilha')
        alvo = rels.get(folha.get(R + 'id'))
        if not alvo or alvo not in z.namelist():
            continue
        if folha.get('state') in ('hidden', 'veryHidden'):
            doc.aviso('Aba oculta "%s" também foi lida' % nome)
        linhas = []
        for evento, el in ET.iterparse(z.open(alvo), events=('end',)):
            if el.tag != S + 'row':
                continue
            r = el.get('r')
            if r and r.isdigit():
                while len(linhas) < int(r) - 1:
                    linhas.append([])
            celulas = []
            for c in el.findall(S + 'c'):
                ref = c.get('r') or ''
                ci = _col_indice(ref) if ref else len(celulas)
                while len(celulas) < ci:
                    celulas.append('')
                t = c.get('t')
                v = c.find(S + 'v')
                val = v.text if v is not None and v.text is not None else ''
                if t == 's':
                    try:
                        val = sst[int(val)]
                    except (ValueError, IndexError):
                        val = ''
                elif t == 'inlineStr':
                    val = ''.join(x.text or '' for x in c.iter(S + 't'))
                elif t in ('str', 'e'):
                    pass
                elif t == 'b':
                    val = 'VERDADEIRO' if val == '1' else 'FALSO'
                else:
                    val = numero_texto(val)
                celulas.append(val)
            linhas.append(celulas)
            el.clear()
        doc.tabela('aba ' + nome, linhas)
    _finalizar(doc, z)


# ===================================================================== OpenDocument

def _texto_odf(el):
    """Texto de um text:p/text:h, com text:s (espaços), text:tab e text:line-break."""
    partes = []

    def andar(e):
        tag = e.tag
        if tag == TXT + 's':
            partes.append(' ' * int(e.get(TXT + 'c', '1') or 1))
        elif tag == TXT + 'tab':
            partes.append('\t')
        elif tag == TXT + 'line-break':
            partes.append('\n')
        elif tag == OFF + 'annotation':
            pass
        else:
            if e.text:
                partes.append(e.text)
            for f in e:
                andar(f)
                if f.tail:
                    partes.append(f.tail)
    if el.text:
        partes.append(el.text)
    for f in el:
        andar(f)
        if f.tail:
            partes.append(f.tail)
    return ''.join(partes)


def _tabela_odf(tbl):
    linhas = []
    for row in tbl.iter(TAB + 'table-row'):
        rep_l = min(int(row.get(TAB + 'number-rows-repeated', '1') or 1), LIMITE_REPETICAO)
        celulas = []
        for cel in row:
            if cel.tag not in (TAB + 'table-cell', TAB + 'covered-table-cell'):
                continue
            rep_c = int(cel.get(TAB + 'number-columns-repeated', '1') or 1)
            tipo = cel.get(OFF + 'value-type')
            if tipo in ('float', 'currency', 'percentage') and cel.get(OFF + 'value'):
                val = numero_texto(cel.get(OFF + 'value'))
            else:
                val = '\n'.join(_texto_odf(p) for p in cel.iter(TXT + 'p'))
            if not val:
                celulas.extend([''] * min(rep_c, LIMITE_REPETICAO))
            else:
                celulas.extend([val] * min(rep_c, LIMITE_REPETICAO))
        while celulas and not celulas[-1]:
            celulas.pop()
        if not celulas:
            linhas.extend([[]] * rep_l)
        else:
            linhas.extend([celulas] * rep_l)
    return linhas


def ler_ods(doc, z):
    raiz = _ler_xml(z, 'content.xml')
    if raiz is None:
        doc.aviso('content.xml ausente ou inválido')
        return
    for tbl in raiz.iter(TAB + 'table'):
        doc.tabela('aba ' + tbl.get(TAB + 'name', 'planilha'), _tabela_odf(tbl))
    _finalizar(doc, z)


def ler_odt(doc, z):
    raiz = _ler_xml(z, 'content.xml')
    if raiz is None:
        doc.aviso('content.xml ausente ou inválido')
        return
    corpo = raiz.find(OFF + 'body')
    corpo = corpo if corpo is not None else raiz
    buffer = []
    n_tab = [0]
    n_pag = [0]

    def despejar(nome='corpo'):
        if buffer:
            doc.texto(nome, '\n'.join(buffer))
            del buffer[:]

    def andar(el):
        for f in el:
            if f.tag in (TXT + 'p', TXT + 'h'):
                buffer.append(_texto_odf(f))
            elif f.tag == TAB + 'table':
                despejar()
                n_tab[0] += 1
                doc.tabela('tabela %d' % n_tab[0], _tabela_odf(f))
            elif f.tag == DRAW + 'page':
                despejar()
                n_pag[0] += 1
                andar(f)
                despejar('slide %d' % n_pag[0])
            else:
                andar(f)
    andar(corpo)
    despejar()
    _finalizar(doc, z)
