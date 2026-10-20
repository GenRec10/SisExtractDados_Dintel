# -*- coding: utf-8 -*-
"""Exportação para XLSX (escritor próprio, biblioteca padrão) e CSV.

No XLSX todos os valores são gravados como TEXTO: o Excel não converte CPF em
número (perdendo o zero à esquerda), não mostra IMEI em notação científica e
não interpreta "+55..." como fórmula.
"""
import csv
import datetime
import io
import re
import zipfile
from xml.sax.saxutils import escape

from . import NOME_SISTEMA, __version__

ROTULO_STATUS = {'ok': 'OK', 'atencao': 'Atenção', 'invalido': 'Inválido'}
TIPOS = (('email', 'E-mails'), ('telefone', 'Telefones'), ('imei', 'IMEIs'), ('cpf', 'CPFs'), ('nome', 'Nomes'))
_ILEGAL_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')

FORMATOS_PADRAO = {'telefone': 'digitos_ddi', 'cpf': 'formatado', 'imei': 'imei15', 'nome': 'original',
                   'email': 'minusculo'}


def valor_formatado(item, formatos=None):
    formatos = formatos or FORMATOS_PADRAO
    f = item.get('formatos') or {}
    chave = formatos.get(item['tipo'], FORMATOS_PADRAO.get(item['tipo']))
    return f.get(chave) or item['valor']


def _info(item):
    ex = item.get('extra') or {}
    partes = []
    if ex.get('uf'):
        partes.append('UF %s' % ex['uf'])
    if ex.get('pais'):
        partes.append(ex['pais'])
    if ex.get('tac'):
        partes.append('TAC %s' % ex['tac'])
    if ex.get('dominio'):
        partes.append(ex['dominio'])
    if ex.get('sem_ddd'):
        partes.append('sem DDD')
    if ex.get('ocr'):
        partes.append('lido por OCR' if ex['ocr'] == 'todas' else 'também lido por OCR')
    return '; '.join(partes)


# ===================================================================== XLSX

def _x(v):
    return escape(_ILEGAL_XML.sub('', '' if v is None else str(v)))


def _col(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _folha(linhas, larguras, estilos_linha=None, coluna_status=None):
    """linhas[0] = cabeçalho. estilos_linha: função(indice_linha) -> índice de estilo ou None."""
    n_col = max(len(l) for l in linhas) if linhas else 1
    xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><cols>']
    for i, w in enumerate(larguras[:n_col]):
        xml.append('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w))
    xml.append('</cols><sheetData>')
    for r, linha in enumerate(linhas):
        xml.append('<row r="%d">' % (r + 1))
        for c, v in enumerate(linha):
            if r > 0 and isinstance(v, int) and not isinstance(v, bool):
                xml.append('<c r="%s%d" s="5"><v>%d</v></c>' % (_col(c), r + 1, v))
                continue
            if r == 0:
                s = 1
            elif coluna_status is not None and c == coluna_status:
                s = {'OK': 2, 'Atenção': 3, 'Inválido': 4}.get(v, 0)
            else:
                s = 0
            xml.append('<c r="%s%d" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'
                       % (_col(c), r + 1, (' s="%d"' % s) if s else '', _x(v)))
        xml.append('</row>')
    xml.append('</sheetData>')
    if len(linhas) > 1:
        xml.append('<autoFilter ref="A1:%s%d"/>' % (_col(n_col - 1), len(linhas)))
    xml.append('</worksheet>')
    return ''.join(xml)


_ESTILOS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="10"/><name val="Calibri"/></font>'
            '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
            '<fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FF16324F"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFD7F0E3"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFCEFC7"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFF8D7D7"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="6"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            '<xf numFmtId="49" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
            '<xf numFmtId="49" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>'
            '<xf numFmtId="49" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>')


def gerar_xlsx(itens, descartes=None, docs=None, formatos=None):
    agora = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    folhas = []
    cab = ['Tipo', 'Valor', 'Status', 'Motivo', 'Observações', 'Ocorrências', 'Arquivo(s)',
           'Primeiro local', 'Trecho', 'Informação', 'Regras']
    linhas = [cab]
    for it in itens:
        a = (it.get('amostras') or [{}])[0]
        linhas.append([it.get('rotulo_tipo', it['tipo']), valor_formatado(it, formatos),
                       ROTULO_STATUS.get(it['status'], it['status']), it.get('motivo', ''),
                       ' | '.join(it.get('obs') or []), int(it.get('n') or 0), '; '.join(it.get('origens') or []),
                       a.get('local', ''), a.get('trecho', ''), _info(it), ', '.join(it.get('regras') or [])])
    folhas.append(('Dados', _folha(linhas, [10, 34, 10, 44, 44, 11, 28, 30, 60, 22, 18], coluna_status=2)))
    for tipo, rotulo in TIPOS:
        sub = [it for it in itens if it['tipo'] == tipo]
        if not sub:
            continue
        ls = [['Valor', 'Status', 'Motivo', 'Ocorrências', 'Arquivo(s)', 'Informação']]
        for it in sub:
            ls.append([valor_formatado(it, formatos), ROTULO_STATUS.get(it['status'], it['status']),
                       it.get('motivo', ''), int(it.get('n') or 0), '; '.join(it.get('origens') or []), _info(it)])
        folhas.append((rotulo, _folha(ls, [34, 10, 44, 11, 30, 22], coluna_status=1)))
    oc = [['Tipo', 'Valor', 'Arquivo', 'Local', 'Trecho', 'Como apareceu']]
    for it in itens:
        for a in it.get('amostras') or []:
            oc.append([it.get('rotulo_tipo', it['tipo']), valor_formatado(it, formatos), a.get('doc', ''),
                       a.get('local', ''), a.get('trecho', ''), a.get('bruto', '')])
    folhas.append(('Ocorrências', _folha(oc, [10, 30, 28, 32, 70, 26])))
    if descartes:
        ds = [['Tentativa', 'Valor', 'Motivo do descarte', 'Regra', 'Ocorrências', 'Arquivo(s)', 'Primeiro local',
               'Trecho']]
        for d in descartes:
            a = (d.get('amostras') or [{}])[0]
            ds.append([d.get('rotulo_tipo', d.get('tipo', '')), d.get('valor', ''), d.get('motivo', ''),
                       d.get('regra', ''), int(d.get('n') or 0), '; '.join(d.get('origens') or []),
                       a.get('local', ''), a.get('trecho', '')])
        folhas.append(('Descartados', _folha(ds, [12, 28, 56, 12, 11, 28, 30, 60])))
    fs = [['Arquivo', 'Tipo detectado', 'Tamanho (bytes)', 'SHA-256', 'Avisos']]
    for d in docs or []:
        fs.append([d.get('nome', ''), d.get('tipo', ''), int(d.get('tamanho') or 0), d.get('sha256', ''),
                   ' | '.join(d.get('avisos') or [])])
    fs.append([])
    fs.append(['Gerado por %s %s em %s' % (NOME_SISTEMA, __version__, agora)])
    folhas.append(('Fontes', _folha(fs, [36, 22, 16, 68, 70])))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        tipos = ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/'
                        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1)
                        for i in range(len(folhas)))
        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/'
                   'vnd.openxmlformats-package.core-properties+xml"/>' + tipos + '</Types>')
        z.writestr('_rels/.rels',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                   'metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr('docProps/core.xml',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
                   'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>%s</dc:creator>'
                   '<dc:title>Extração de identificadores</dc:title></cp:coreProperties>' % _x(NOME_SISTEMA))
        z.writestr('xl/workbook.xml',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
                   ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_x(n), i + 1, i + 1)
                           for i, (n, _) in enumerate(folhas)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                   ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                           'relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
                           for i in range(len(folhas))) +
                   '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/styles" Target="styles.xml"/></Relationships>' % (len(folhas) + 1))
        z.writestr('xl/styles.xml', _ESTILOS)
        for i, (_, xml) in enumerate(folhas):
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), xml)
    return buf.getvalue()


# ===================================================================== CSV

def _seguro(v):
    """Evita injeção de fórmula ao abrir o CSV em planilha (OWASP)."""
    v = '' if v is None else str(v)
    if v[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + v
    return v


def gerar_csv(itens, formatos=None):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow(['Tipo', 'Valor', 'Status', 'Motivo', 'Observações', 'Ocorrências', 'Arquivo(s)', 'Primeiro local',
                'Informação', 'Regras'])
    for it in itens:
        a = (it.get('amostras') or [{}])[0]
        w.writerow([_seguro(x) for x in (
            it.get('rotulo_tipo', it['tipo']), valor_formatado(it, formatos),
            ROTULO_STATUS.get(it['status'], it['status']), it.get('motivo', ''), ' | '.join(it.get('obs') or []),
            it.get('n', ''), '; '.join(it.get('origens') or []), a.get('local', ''), _info(it),
            ', '.join(it.get('regras') or []))])
    return ('﻿' + buf.getvalue()).encode('utf-8')
