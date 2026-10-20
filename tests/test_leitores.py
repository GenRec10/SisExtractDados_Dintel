# -*- coding: utf-8 -*-
"""Leitura de cada formato contra o gabarito das amostras sintéticas."""
import io
import json
import os
import sys
import unittest
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, '..', '_analisador'))

from extdados.analise import analisar  # noqa: E402
from extdados.leitores import decodificar, ler_arquivo, ler_bytes, rtf_para_texto, texto_colado  # noqa: E402
from extdados.nomes import chave_nome  # noqa: E402

AMOSTRAS = os.path.join(AQUI, 'amostras')
with open(os.path.join(AMOSTRAS, 'gabarito.json'), encoding='utf-8') as _f:
    GABARITO = json.load(_f)
TIPOS = ('email', 'telefone', 'cpf', 'imei', 'nome')


def chave(it):
    t, f = it['tipo'], it['formatos']
    if t == 'telefone':
        return f['e164']
    if t == 'cpf':
        return ('M:' + f['digitos']) if '*' in f['digitos'] else f['digitos']
    if t == 'imei':
        return f['imei14']
    if t == 'nome':
        return chave_nome(it['valor'])
    return it['valor']


def extrair(nome):
    doc = ler_arquivo(os.path.join(AMOSTRAS, nome))
    doc.id = 'd'
    res = analisar([doc])
    achados = {t: set() for t in TIPOS}
    for it in res.itens:
        achados[it['tipo']].add(chave(it))
    return doc, achados


# Arquivos que contêm o texto completo (parágrafos + tabela)
COMPLETOS = ['amostra.docx', 'amostra.doc', 'amostra.odt', 'amostra.rtf', 'amostra.xlsx', 'amostra.xls',
             'amostra.ods', 'amostra.txt', 'amostra.json', 'amostra.html', 'amostra.eml', 'amostra.zip',
             'amostra_reportlab.pdf']
# PDFs do LibreOffice: as células estreitas da tabela quebram linha (o próprio PDF separa o texto)
PDFS_TABELA_QUEBRADA = ['amostra.pdf', 'amostra_rc4.pdf', 'amostra_aes128.pdf', 'amostra_aes256.pdf',
                        'amostra_objstm.pdf']


class TestFormatos(unittest.TestCase):
    def test_completos(self):
        for nome in COMPLETOS:
            doc, achados = extrair(nome)
            for t in TIPOS:
                self.assertEqual(achados[t], set(GABARITO[t]), '%s / %s (%s)' % (nome, t, doc.avisos))

    def test_pdfs(self):
        for nome in PDFS_TABELA_QUEBRADA:
            doc, achados = extrair(nome)
            for t in ('telefone', 'cpf'):
                self.assertEqual(achados[t], set(GABARITO[t]), '%s / %s' % (nome, t))
            self.assertTrue({'maria.santos@exemplo.com.br', 'fulano.teste@mailinator.com'} <= achados['email'], nome)
            self.assertTrue({'MARIA APARECIDA DOS SANTOS', 'JOSE CARLOS KOWALSKI', 'ANA PAULA CAVALCANTI DE LIMA'}
                            <= achados['nome'], nome)

    def test_pdf_criptografado_informa_metodo(self):
        doc, _ = extrair('amostra_aes256.pdf')
        self.assertIn('R6', doc.info.get('criptografia', ''))
        doc, _ = extrair('amostra_rc4.pdf')
        self.assertIn('R3', doc.info.get('criptografia', ''))

    def test_pdf_com_senha_de_abertura(self):
        doc, achados = extrair('amostra_com_senha.pdf')
        self.assertTrue(any('SENHA' in a for a in doc.avisos))
        self.assertFalse(any(achados.values()))

    def test_somente_tabela(self):
        for nome in ('amostra.csv', 'amostra_html_disfarcado.xls'):
            _, achados = extrair(nome)
            self.assertEqual(achados['email'], {'pedro.alves@exemplo.org', 'luana.lins@teste.com.br'}, nome)
            self.assertEqual(achados['nome'], {'PEDRO HENRIQUE ALVES', 'LUANA FERREIRA LINS'}, nome)

    def test_pptx(self):
        _, achados = extrair('amostra.pptx')
        self.assertIn('+5581987654321', achados['telefone'])
        self.assertIn('JOSE CARLOS KOWALSKI', achados['nome'])

    def test_tipo_por_conteudo_nao_por_extensao(self):
        doc = ler_arquivo(os.path.join(AMOSTRAS, 'amostra_html_disfarcado.xls'))
        self.assertEqual(doc.tipo, 'HTML')

    def test_planilha_preserva_numeros_longos(self):
        doc = ler_arquivo(os.path.join(AMOSTRAS, 'amostra.xlsx'))
        celulas = [c for b in doc.blocos if b.tipo == 'tabela' for l in b.linhas for c in l]
        self.assertIn('356938035643809', celulas)    # IMEI gravado como número no Excel
        self.assertIn('7068093868', celulas)         # CPF que perdeu o zero (restaurado na análise)

    def test_sha256(self):
        doc = ler_arquivo(os.path.join(AMOSTRAS, 'amostra.docx'))
        self.assertEqual(len(doc.sha256), 64)


class TestTexto(unittest.TestCase):
    def test_colado_com_tabulacao_vira_tabela(self):
        d = texto_colado('t', 'Nome\tCelular\nMaria Souza\t81987654321\nJoão Lima\t81912345678')
        self.assertEqual(d.blocos[0].tipo, 'tabela')

    def test_codificacoes(self):
        self.assertEqual(decodificar('ação'.encode('utf-8'))[0], 'ação')
        self.assertEqual(decodificar('ação'.encode('cp1252'))[0], 'ação')
        self.assertEqual(decodificar('ação'.encode('utf-16'))[0], 'ação')
        self.assertEqual(decodificar('aç\x00'.encode('latin-1') + b'\x01\x02' * 50)[0], None)

    def test_rtf_unicode(self):
        t = rtf_para_texto(b'{\\rtf1\\ansi\\ansicpg1252 Jo\\\'e3o \\u' + b'8212? CPF\\par 529.982.247-25}')
        self.assertIn('João', t)
        self.assertIn('—', t)
        self.assertIn('529.982.247-25', t)

    def test_zip_aninhado_e_binario(self):
        interno = io.BytesIO()
        with zipfile.ZipFile(interno, 'w') as z:
            z.writestr('a.txt', 'tel (81) 98765-4321')
        externo = io.BytesIO()
        with zipfile.ZipFile(externo, 'w') as z:
            z.writestr('dentro.zip', interno.getvalue())
            z.writestr('lixo.bin', b'\x00\x01\x02' * 100)
        doc = ler_bytes('pacote.zip', externo.getvalue())
        doc.id = 'z'
        its = analisar([doc]).itens
        self.assertEqual([i['formatos']['e164'] for i in its], ['+5581987654321'])
        self.assertIn('dentro.zip', its[0]['amostras'][0]['local'])

    def test_imagem_avisa(self):
        doc = ler_bytes('foto.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 50)
        self.assertTrue(any('OCR' in a for a in doc.avisos))

    def test_json_registros(self):
        doc = ler_bytes('x.json', json.dumps([{'nome': 'Ana Lima', 'contato': {'cel': '81987654321'}}]).encode())
        self.assertEqual(doc.blocos[0].tipo, 'tabela')
        self.assertEqual(doc.blocos[0].linhas[0], ['nome', 'contato.cel'])


if __name__ == '__main__':
    unittest.main()
