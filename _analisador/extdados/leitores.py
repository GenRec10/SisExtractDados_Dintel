# -*- coding: utf-8 -*-
"""Leitura de arquivos -> Documento (blocos de texto e tabelas).

O tipo é decidido pelo CONTEÚDO (assinatura dos bytes), não pela extensão:
um ".xls" que na verdade é HTML (comum em exportações de bancos) é lido como
HTML; um ".txt" com colunas separadas por tabulação vira tabela.
"""
import codecs
import csv
import email
import email.policy
import gzip
import hashlib
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from .modelo import Documento

LIMITE_ARQUIVO = 512 * 1024 * 1024        # 512 MB por arquivo
LIMITE_DESCOMPACTADO = 1024 * 1024 * 1024  # 1 GB somados dentro de um .zip
PROFUNDIDADE_MAX = 4                        # zip dentro de zip, anexo dentro de e-mail...
SEP = ' › '

ASSINATURAS_IMAGEM = (
    (b'\x89PNG\r\n\x1a\n', 'imagem PNG'), (b'\xff\xd8\xff', 'imagem JPEG'), (b'GIF8', 'imagem GIF'),
    (b'II*\x00', 'imagem TIFF'), (b'MM\x00*', 'imagem TIFF'), (b'BM', 'imagem BMP'))


# ===================================================================== entrada principal

def ler_arquivo(caminho):
    with open(caminho, 'rb') as f:
        dados = f.read(LIMITE_ARQUIVO + 1)
    import os
    return ler_bytes(os.path.basename(caminho), dados)


def ler_bytes(nome, dados, profundidade=0):
    doc = Documento(nome, tamanho=len(dados), sha256=hashlib.sha256(dados).hexdigest())
    if len(dados) > LIMITE_ARQUIVO:
        doc.tipo = 'grande demais'
        doc.aviso('Arquivo maior que %d MB: não processado' % (LIMITE_ARQUIVO // 1048576))
        return doc
    try:
        _despachar(doc, nome, dados, profundidade)
    except Exception as e:  # nunca derrubar o lote por causa de um arquivo
        doc.aviso('Falha ao ler o arquivo (%s: %s). Tente salvar em outro formato ou colar o texto.'
                  % (type(e).__name__, str(e)[:200]))
    if not doc.blocos and not doc.avisos:
        doc.aviso('Nenhum texto encontrado no arquivo')
    return doc


def texto_colado(nome, texto):
    """Texto colado da área de transferência. Colunas separadas por TAB viram tabela."""
    doc = Documento(nome, tipo='texto colado', tamanho=len(texto.encode('utf-8')),
                    sha256=hashlib.sha256(texto.encode('utf-8')).hexdigest())
    _texto_ou_tabela(doc, '', texto, delimitadores=('\t',))
    if not doc.blocos:
        doc.aviso('Texto vazio')
    return doc


def _despachar(doc, nome, dados, prof):
    ini = dados[:8]
    baixo = nome.lower()
    if dados[:1024].find(b'%PDF-') != -1:
        from . import pdf
        doc.tipo = 'PDF'
        pdf.ler_pdf(doc, dados)
        return
    if ini[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'):
        _zip_ou_office(doc, nome, dados, prof)
        return
    if ini == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        from . import ole
        ole.ler_ole(doc, dados)
        return
    if dados[:5] == b'{\\rtf':
        doc.tipo = 'RTF'
        doc.texto('', rtf_para_texto(dados))
        return
    if ini[:2] == b'\x1f\x8b':
        doc.tipo = 'GZIP'
        if prof >= PROFUNDIDADE_MAX:
            doc.aviso('Arquivo compactado dentro de outro além do limite de profundidade')
            return
        interno = gzip.GzipFile(fileobj=io.BytesIO(dados)).read(LIMITE_DESCOMPACTADO + 1)
        nome_int = nome[:-3] if baixo.endswith('.gz') else nome + '.descompactado'
        _incorporar(doc, ler_bytes(nome_int, interno, prof + 1), '')
        return
    for assinatura, descricao in ASSINATURAS_IMAGEM:
        if dados.startswith(assinatura):
            doc.tipo = descricao
            _imagem(doc, dados)
            return
    if dados[:4] == b'RIFF' and dados[8:12] == b'WEBP':
        doc.tipo = 'imagem WebP'
        _imagem(doc, dados)
        return
    if dados[4:12] in (b'ftypheic', b'ftypheix', b'ftypmif1', b'ftyphevc', b'ftypavif'):
        doc.tipo = 'imagem HEIC/AVIF'
        doc.aviso('Formato HEIC/AVIF (fotos de iPhone) não é lido pelo OCR: converta para JPEG ou PNG')
        return
    if dados[:4] == b'RIFF' and dados[8:12] in (b'WAVE', b'AVI '):
        doc.tipo = 'mídia'
        doc.aviso('Arquivo de áudio/vídeo: sem texto para extrair')
        return
    if dados[4:8] == b'ftyp' or dados[:3] == b'ID3' or dados[:4] == b'OggS':
        doc.tipo = 'mídia'
        doc.aviso('Arquivo de áudio/vídeo: sem texto para extrair')
        return
    if dados[:6] == b'7z\xbc\xaf\x27\x1c' or dados[:4] == b'Rar!':
        doc.tipo = '7z/RAR'
        doc.aviso('Formato compactado não suportado (7z/RAR). Extraia e envie os arquivos.')
        return
    texto, codificacao = decodificar(dados)
    if texto is None:
        doc.tipo = 'binário'
        if prof == 0:
            doc.aviso('Formato não reconhecido: extraídas apenas as sequências de texto legíveis')
            doc.texto('sequências de texto', extrair_strings(dados))
        else:
            doc.aviso('Arquivo binário não reconhecido: ignorado')
        return
    doc.info['codificacao'] = codificacao
    if baixo.endswith('.eml') or _parece_email(texto):
        doc.tipo = 'e-mail (.eml)'
        _eml(doc, dados, prof)
        return
    _texto(doc, nome, texto)


def _imagem(doc, dados):
    from . import ocr
    dim = ocr.dimensoes(dados)
    if dim:
        doc.info['dimensoes'] = '%d x %d px' % dim
        if dim[0] * dim[1] > ocr.LIMITE_PIXELS:
            doc.aviso('Imagem grande demais para OCR (%d x %d px)' % dim)
            return
    if not ocr.ativo():
        doc.aviso('Imagem não lida: %s' % ('OCR desligado' if ocr.motor() is not None
                                           else 'instale o Tesseract para ativar o OCR (ver README)'))
        return
    texto, erro = ocr.ler_lote([(0, dados, None)], 'imagem').get(0, ('', 'sem resultado'))
    if erro:
        doc.aviso('OCR falhou: %s' % erro)
        return
    doc.info['ocr'] = ocr.motor().descricao()
    if texto.strip():
        doc.texto('imagem (OCR)', texto, ocr=True)
    else:
        doc.aviso('OCR não reconheceu texto na imagem')


def _incorporar(doc, sub, prefixo):
    """Copia os blocos e avisos de um sub-documento (membro de zip, anexo) para doc."""
    for b in sub.blocos:
        b.nome = prefixo + (SEP + b.nome if b.nome and prefixo else b.nome)
        doc.blocos.append(b)
    if sub.info.get('ocr'):
        doc.info['ocr'] = sub.info['ocr']
    for a in sub.avisos:
        doc.aviso((prefixo + ': ' if prefixo else '') + a)


# ===================================================================== texto puro e derivados

def decodificar(dados):
    """Retorna (texto, codificação) ou (None, None) se parecer binário."""
    if dados.startswith(codecs.BOM_UTF8):
        return dados[3:].decode('utf-8', 'replace'), 'UTF-8 (BOM)'
    if dados.startswith(codecs.BOM_UTF16_LE) or dados.startswith(codecs.BOM_UTF16_BE):
        return dados.decode('utf-16', 'replace').lstrip('\ufeff'), 'UTF-16'
    amostra = dados[:65536]
    if len(amostra) >= 4:
        nul_par = amostra[1::2].count(0)
        nul_impar = amostra[0::2].count(0)
        meio = len(amostra) // 2
        if nul_par > meio * 0.3 and nul_impar < meio * 0.05:
            return dados.decode('utf-16-le', 'replace'), 'UTF-16LE'
        if nul_impar > meio * 0.3 and nul_par < meio * 0.05:
            return dados.decode('utf-16-be', 'replace'), 'UTF-16BE'
    controle = sum(1 for b in amostra if b < 32 and b not in (9, 10, 12, 13))
    if amostra and (controle > len(amostra) * 0.05 or b'\x00' in amostra):
        return None, None
    try:
        return dados.decode('utf-8'), 'UTF-8'
    except UnicodeDecodeError:
        return dados.decode('cp1252', 'replace'), 'Windows-1252'


def _texto(doc, nome, texto):
    baixo = nome.lower()
    cabeca = texto[:4096].lstrip().lower()
    if 'urn:schemas-microsoft-com:office:spreadsheet' in texto[:20000]:
        doc.tipo = 'planilha XML (Excel 2003)'
        _spreadsheetml(doc, texto)
        return
    if cabeca.startswith('<!doctype html') or cabeca.startswith('<html') or \
            re.search(r'<(?:table|body|div|html|meta)\b', cabeca) or baixo.endswith(('.htm', '.html', '.mht')):
        doc.tipo = 'HTML'
        html_para_doc(doc, texto)
        return
    if cabeca.startswith(('{', '[')) or baixo.endswith('.json'):
        try:
            obj = json.loads(texto)
        except ValueError:
            obj = None
        if obj is not None:
            doc.tipo = 'JSON'
            _json(doc, obj)
            return
    if cabeca.startswith('<?xml') or baixo.endswith('.xml'):
        doc.tipo = 'XML'
        _xml(doc, texto)
        return
    if baixo.endswith(('.csv', '.tsv')):
        doc.tipo = 'CSV/TSV'
        _texto_ou_tabela(doc, '', texto, delimitadores=(';', ',', '\t', '|'))
        return
    doc.tipo = 'texto'
    _texto_ou_tabela(doc, '', texto, delimitadores=('\t',))


def _texto_ou_tabela(doc, nome_bloco, texto, delimitadores):
    texto = texto.replace('\r\n', '\n').replace('\r', '\n')
    linhas = [l for l in texto.split('\n') if l.strip()]
    delim = _farejar_delimitador(linhas[:50], delimitadores)
    if delim is None:
        doc.texto(nome_bloco, texto)
        return
    try:
        leitor = csv.reader(io.StringIO(texto), delimiter=delim, quotechar='"')
        tabela = [linha for linha in leitor]
    except csv.Error:
        doc.texto(nome_bloco, texto)
        return
    doc.info['delimitador'] = {'\t': 'TAB', ';': ';', ',': ',', '|': '|'}[delim]
    doc.tabela(nome_bloco or 'tabela', tabela)


def _farejar_delimitador(linhas, candidatos):
    """Delimitador presente com a MESMA contagem (>=1) em >= 80% das linhas (mín. 2 linhas)."""
    if len(linhas) < 2:
        return None
    melhor, melhor_n = None, 0
    for d in candidatos:
        contagens = [l.count(d) for l in linhas]
        moda = max(set(contagens), key=contagens.count)
        if moda >= 1 and contagens.count(moda) >= 0.8 * len(linhas) and moda > melhor_n:
            melhor, melhor_n = d, moda
    return melhor


def extrair_strings(dados, minimo=6):
    """Como o utilitário 'strings': sequências ASCII/Latin-1 e UTF-16LE legíveis."""
    saida = []
    for m in re.finditer(rb'[\x20-\x7e\xa0-\xff]{%d,}' % minimo, dados):
        saida.append(m.group().decode('latin-1'))
    for m in re.finditer(rb'(?:[\x20-\x7e\xa0-\xff]\x00){%d,}' % minimo, dados):
        saida.append(m.group().decode('utf-16-le'))
    return '\n'.join(saida)


# ===================================================================== JSON / XML

def _json(doc, obj):
    registros = None
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:50]):
        registros = obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and len(v) >= 2 and all(isinstance(x, dict) for x in v[:50]):
                registros = v
                break
    if registros is not None:
        planos = [dict(_achatar(r, '')) for r in registros]
        colunas = []
        for p in planos:
            for k in p:
                if k not in colunas and len(colunas) < 300:
                    colunas.append(k)
        tabela = [colunas] + [[p.get(c, '') for c in colunas] for p in planos]
        doc.tabela('registros', tabela)
        restante = obj if not isinstance(obj, dict) else {k: v for k, v in obj.items() if v is not registros}
        if isinstance(restante, dict) and restante:
            doc.texto('demais campos', '\n'.join('%s: %s' % kv for kv in _achatar(restante, '')))
        return
    doc.texto('', '\n'.join('%s: %s' % kv for kv in _achatar(obj, '')))


def _achatar(obj, prefixo):
    if isinstance(obj, dict):
        for k, v in obj.items():
            for item in _achatar(v, '%s.%s' % (prefixo, k) if prefixo else str(k)):
                yield item
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for item in _achatar(v, '%s[%d]' % (prefixo, i)):
                yield item
    elif obj is not None:
        if isinstance(obj, bool):
            obj = 'true' if obj else 'false'
        elif isinstance(obj, float) and obj.is_integer() and abs(obj) < 1e17:
            obj = '%d' % obj
        yield prefixo, str(obj)


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _xml(doc, texto):
    linhas = []
    try:
        raiz = ET.fromstring(texto.encode('utf-8'))
    except ET.ParseError:
        doc.aviso('XML malformado: lido como texto (marcações removidas)')
        doc.texto('', re.sub(r'<[^>]+>', ' ', texto))
        return

    def andar(el, caminho):
        nome = _local(el.tag)
        for k, v in el.attrib.items():
            if v and v.strip():
                linhas.append('%s@%s: %s' % (nome, _local(k), v.strip()))
        if el.text and el.text.strip():
            linhas.append('%s: %s' % (nome, el.text.strip()))
        for f in el:
            andar(f, caminho + '/' + nome)
            if f.tail and f.tail.strip():
                linhas.append(f.tail.strip())
    andar(raiz, '')
    doc.texto('', '\n'.join(linhas))


def _spreadsheetml(doc, texto):
    try:
        raiz = ET.fromstring(texto.encode('utf-8'))
    except ET.ParseError as e:
        doc.aviso('Planilha XML malformada (%s): lida como texto' % e)
        doc.texto('', re.sub(r'<[^>]+>', ' ', texto))
        return
    for ws in raiz.iter():
        if _local(ws.tag) != 'Worksheet':
            continue
        nome = next((v for k, v in ws.attrib.items() if _local(k) == 'Name'), 'planilha')
        linhas = []
        for row in ws.iter():
            if _local(row.tag) != 'Row':
                continue
            idx_attr = next((v for k, v in row.attrib.items() if _local(k) == 'Index'), None)
            if idx_attr:
                while len(linhas) < int(idx_attr) - 1:
                    linhas.append([])
            celulas = []
            for cell in row:
                if _local(cell.tag) != 'Cell':
                    continue
                ci = next((v for k, v in cell.attrib.items() if _local(k) == 'Index'), None)
                if ci:
                    while len(celulas) < int(ci) - 1:
                        celulas.append('')
                valor = ''
                for d in cell:
                    if _local(d.tag) == 'Data':
                        valor = ''.join(d.itertext())
                celulas.append(valor)
            linhas.append(celulas)
        doc.tabela('aba ' + nome, linhas)


# ===================================================================== HTML

class _ExtratorHTML(HTMLParser):
    BLOCO = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section',
             'article', 'header', 'footer', 'tr', 'table', 'blockquote', 'pre', 'dt', 'dd', 'hr',
             'form', 'fieldset', 'legend', 'address', 'main', 'nav', 'aside', 'title', 'caption'}
    IGNORAR = {'script', 'style', 'noscript', 'template', 'svg', 'object', 'iframe'}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.texto = []
        self.tabelas = []
        self.pilha_tab = []    # cada item: {'linhas': [], 'linha': None, 'celula': None}
        self.ignorando = 0

    def _escrever(self, s):
        if self.pilha_tab and self.pilha_tab[-1]['celula'] is not None:
            self.pilha_tab[-1]['celula'].append(s)
        else:
            self.texto.append(s)

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self.ignorando += 1
            return
        if self.ignorando:
            return
        a = dict(attrs)
        href = (a.get('href') or '').strip()
        if href.lower().startswith(('mailto:', 'tel:', 'sms:', 'callto:')):
            self._escrever(' [%s] ' % href.split('?')[0])
        if tag == 'table':
            self.pilha_tab.append({'linhas': [], 'linha': None, 'celula': None})
        elif tag == 'tr' and self.pilha_tab:
            t = self.pilha_tab[-1]
            self._fechar_linha(t)
            t['linha'] = []
        elif tag in ('td', 'th') and self.pilha_tab:
            t = self.pilha_tab[-1]
            if t['linha'] is None:
                t['linha'] = []
            self._fechar_celula(t)
            t['celula'] = []
            colspan = a.get('colspan')
            t['colspan'] = int(colspan) if colspan and colspan.isdigit() and int(colspan) < 50 else 1
        elif tag in self.BLOCO:
            self._escrever('\n')
        elif tag == 'input' and a.get('value') and a.get('type', 'text') in ('text', 'email', 'tel', 'hidden'):
            self._escrever(' %s ' % a['value'])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def _fechar_celula(self, t):
        if t['celula'] is not None and t['linha'] is not None:
            t['linha'].append(re.sub(r'\s+', ' ', ''.join(t['celula'])).strip())
            for _ in range(t.get('colspan', 1) - 1):
                t['linha'].append('')
        t['celula'] = None

    def _fechar_linha(self, t):
        self._fechar_celula(t)
        if t['linha'] is not None:
            t['linhas'].append(t['linha'])
        t['linha'] = None

    def handle_endtag(self, tag):
        if tag in self.IGNORAR:
            self.ignorando = max(0, self.ignorando - 1)
            return
        if self.ignorando:
            return
        if tag == 'table' and self.pilha_tab:
            t = self.pilha_tab.pop()
            self._fechar_linha(t)
            self.tabelas.append(t['linhas'])
        elif tag in ('td', 'th') and self.pilha_tab:
            self._fechar_celula(self.pilha_tab[-1])
        elif tag == 'tr' and self.pilha_tab:
            self._fechar_linha(self.pilha_tab[-1])
        elif tag in self.BLOCO:
            self._escrever('\n')

    def handle_data(self, data):
        if not self.ignorando:
            self._escrever(data)


def html_para_doc(doc, texto):
    p = _ExtratorHTML()
    p.feed(texto)
    p.close()
    while p.pilha_tab:
        t = p.pilha_tab.pop()
        p._fechar_linha(t)
        p.tabelas.append(t['linhas'])
    corpo = ''.join(p.texto)
    corpo = re.sub(r'[ \t\r\f\v]+', ' ', corpo)
    corpo = re.sub(r'\n\s*\n+', '\n', corpo)
    doc.texto('texto', corpo)
    for i, t in enumerate(p.tabelas, 1):
        doc.tabela('tabela %d' % i, t)


def html_para_texto(texto):
    d = Documento('x')
    html_para_doc(d, texto)
    partes = []
    for b in d.blocos:
        if b.tipo == 'texto':
            partes.append(b.texto)
        else:
            partes.extend('\t'.join(l) for l in b.linhas)
    return '\n'.join(partes)


# ===================================================================== e-mail (.eml)

_CABECALHO_EMAIL = re.compile(r'^(?:Received|From|To|Subject|Date|Message-ID|MIME-Version|Return-Path|Delivered-To)'
                              r':', re.I | re.M)


def _parece_email(texto):
    cab = texto[:3000]
    return len(set(m.group(0).lower() for m in _CABECALHO_EMAIL.finditer(cab))) >= 3


def _eml(doc, texto_ou_bytes, prof):
    if isinstance(texto_ou_bytes, str):
        msg = email.message_from_string(texto_ou_bytes, policy=email.policy.default)
    else:
        msg = email.message_from_bytes(texto_ou_bytes, policy=email.policy.default)
    cab = []
    for campo in ('From', 'Sender', 'Reply-To', 'To', 'Cc', 'Bcc', 'Delivered-To', 'Return-Path',
                  'Subject', 'Date', 'Message-ID', 'X-Originating-IP', 'X-Sender', 'X-Mailer'):
        for v in msg.get_all(campo, []) or []:
            cab.append('%s: %s' % (campo, v))
    doc.texto('cabeçalho do e-mail', '\n'.join(cab))
    n_anexo = 0
    for parte in msg.walk():
        if parte.is_multipart():
            continue
        nome_arq = parte.get_filename()
        tipo = parte.get_content_type()
        if nome_arq or parte.get_content_disposition() == 'attachment':
            n_anexo += 1
            nome_arq = nome_arq or 'anexo_%d' % n_anexo
            conteudo = parte.get_payload(decode=True) or b''
            if prof + 1 > PROFUNDIDADE_MAX:
                doc.aviso('Anexo "%s" além do limite de profundidade: ignorado' % nome_arq)
                continue
            sub = ler_bytes(nome_arq, conteudo, prof + 1)
            _incorporar(doc, sub, 'anexo ' + nome_arq)
            continue
        if tipo == 'text/plain':
            doc.texto('corpo do e-mail', parte.get_content())
        elif tipo == 'text/html':
            sub = Documento('x')
            html_para_doc(sub, parte.get_content())
            _incorporar(doc, sub, 'corpo do e-mail (HTML)')
        elif tipo == 'message/rfc822':
            conteudo = parte.get_payload(decode=True)
            if conteudo:
                sub = Documento('x')
                _eml(sub, conteudo, prof + 1)
                _incorporar(doc, sub, 'e-mail anexado')


# ===================================================================== ZIP e formatos baseados em ZIP

def _zip_ou_office(doc, nome, dados, prof):
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile as e:
        doc.tipo = 'ZIP corrompido'
        doc.aviso('Arquivo compactado corrompido (%s)' % e)
        return
    nomes = set(z.namelist())
    from . import office
    if 'word/document.xml' in nomes:
        doc.tipo = 'Word (.docx)'
        office.ler_docx(doc, z)
    elif 'xl/workbook.xml' in nomes:
        doc.tipo = 'Excel (.xlsx)'
        office.ler_xlsx(doc, z)
    elif 'ppt/presentation.xml' in nomes:
        doc.tipo = 'PowerPoint (.pptx)'
        office.ler_pptx(doc, z)
    elif 'mimetype' in nomes and 'content.xml' in nomes:
        mime = z.read('mimetype').decode('ascii', 'replace').strip()
        if mime.endswith('spreadsheet'):
            doc.tipo = 'OpenDocument planilha (.ods)'
            office.ler_ods(doc, z)
        else:
            doc.tipo = 'OpenDocument (.odt/.odp)'
            office.ler_odt(doc, z)
    else:
        doc.tipo = 'ZIP'
        _zip(doc, z, prof)


def _zip(doc, z, prof):
    if prof >= PROFUNDIDADE_MAX:
        doc.aviso('ZIP dentro de ZIP além do limite de profundidade')
        return
    total = 0
    for info in z.infolist():
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            doc.aviso('"%s" está protegido por senha: ignorado' % info.filename)
            continue
        if info.file_size > LIMITE_ARQUIVO:
            doc.aviso('"%s" grande demais: ignorado' % info.filename)
            continue
        if info.compress_size and info.file_size / float(info.compress_size) > 200 and info.file_size > 50e6:
            doc.aviso('"%s" com taxa de compressão suspeita (zip bomb?): ignorado' % info.filename)
            continue
        total += info.file_size
        if total > LIMITE_DESCOMPACTADO:
            doc.aviso('Limite de %d MB descompactados atingido: demais arquivos ignorados'
                      % (LIMITE_DESCOMPACTADO // 1048576))
            break
        try:
            conteudo = z.read(info)
        except Exception as e:
            doc.aviso('"%s": falha ao descompactar (%s)' % (info.filename, e))
            continue
        nome_membro = info.filename
        try:
            nome_membro = info.filename.encode('cp437').decode('utf-8') if not (info.flag_bits & 0x800) \
                else info.filename
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        sub = ler_bytes(nome_membro.rsplit('/', 1)[-1], conteudo, prof + 1)
        _incorporar(doc, sub, nome_membro)
    doc.info['membros'] = len(z.infolist())


# ===================================================================== RTF

_RTF_DESTINOS_IGNORADOS = {
    'fonttbl', 'colortbl', 'stylesheet', 'listtable', 'listoverridetable', 'revtbl', 'rsidtbl',
    'generator', 'pict', 'object', 'objdata', 'datastore', 'themedata', 'colorschememapping',
    'latentstyles', 'xmlnstbl', 'mmathPr', 'pgdsctbl', 'filetbl', 'fldinst', 'shppict',
    'nonshppict', 'blipuid', 'bkmkstart', 'bkmkend', 'wgrffmtfilter', 'listtext', 'pntext',
    'pntxta', 'pntxtb', 'xe', 'tc', 'private', 'protusertbl', 'docvar', 'userprops'}
_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([\r\n]+)|([^\\{}\r\n]+)")


def rtf_para_texto(dados):
    """Conversor RTF -> texto (grupos, \\'hh na página de código, \\uN, \\par, \\cell)."""
    pilha = []
    ignorar = False
    uc = 1
    pular = 0
    codepage = 'cp1252'
    saida = []
    estrela = False
    for m in _RTF_TOKEN.finditer(dados):
        palavra, arg, hexa, simbolo, chave, quebra, texto = m.groups()
        if chave:
            if chave == b'{':
                pilha.append((ignorar, uc))
                estrela = False
            else:
                if pilha:
                    ignorar, uc = pilha.pop()
            continue
        if quebra:
            continue
        if palavra is not None:
            p = palavra.decode('ascii')
            if pular and p not in ('par', 'line'):
                pular -= 1
                continue
            if estrela or p in _RTF_DESTINOS_IGNORADOS:
                ignorar = True
                estrela = False
                continue
            if ignorar:
                continue
            if p == 'ansicpg' and arg:
                codepage = 'cp' + arg.decode()
            elif p == 'uc' and arg:
                uc = int(arg)
            elif p == 'u' and arg:
                n = int(arg)
                if n < 0:
                    n += 65536
                saida.append(chr(n))
                pular = uc
            elif p in ('par', 'line', 'row', 'sect', 'page'):
                saida.append('\n')
            elif p in ('tab', 'cell'):
                saida.append('\t')
            elif p in ('emdash', 'endash'):
                saida.append('-')
            elif p in ('lquote', 'rquote'):
                saida.append("'")
            elif p in ('ldblquote', 'rdblquote'):
                saida.append('"')
            elif p == 'bullet':
                saida.append('•')
            continue
        if hexa is not None:
            if pular:
                pular -= 1
                continue
            if not ignorar:
                try:
                    saida.append(bytes([int(hexa, 16)]).decode(codepage, 'replace'))
                except LookupError:
                    saida.append(bytes([int(hexa, 16)]).decode('cp1252', 'replace'))
            continue
        if simbolo is not None:
            s = simbolo.decode('latin-1')
            if s == '*':
                estrela = True
            elif ignorar:
                pass
            elif s in '\\{}':
                saida.append(s)
            elif s == '~':
                saida.append(' ')
            elif s in '_-':
                saida.append('-' if s == '_' else '')
            elif s in '\r\n':
                saida.append('\n')
            continue
        if texto is not None:
            if pular:
                n = min(pular, len(texto))
                texto = texto[n:]
                pular -= n
            if not ignorar and texto:
                saida.append(texto.decode(codepage if codepage != 'cp0' else 'cp1252', 'replace'))
    return ''.join(saida)
