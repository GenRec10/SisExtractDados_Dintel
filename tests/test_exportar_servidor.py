# -*- coding: utf-8 -*-
"""Exportação (XLSX/CSV), criptografia e servidor local."""
import io
import json
import os
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, '..', '_analisador'))

from extdados.analise import analisar  # noqa: E402
from extdados.cripto import AES, rc4  # noqa: E402
from extdados.exportar import gerar_csv, gerar_xlsx  # noqa: E402
from extdados.leitores import texto_colado  # noqa: E402
from extdados.servidor import Estado, Servidor  # noqa: E402


class TestCripto(unittest.TestCase):
    def test_aes_fips197(self):
        pt = bytes.fromhex('00112233445566778899aabbccddeeff')
        for chave, esperado in (('000102030405060708090a0b0c0d0e0f', '69c4e0d86a7b0430d8cdb78070b4c55a'),
                                ('000102030405060708090a0b0c0d0e0f1011121314151617', 'dda97ca4864cdfe06eaf70a0ec0d7191'),
                                ('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f',
                                 '8ea2b7ca516745bfeafc49904b496089')):
            a = AES(bytes.fromhex(chave))
            c = struct.pack('>4I', *a.cifrar_bloco(*struct.unpack('>4I', pt)))
            self.assertEqual(c.hex(), esperado)
            self.assertEqual(struct.pack('>4I', *a.decifrar_bloco(*struct.unpack('>4I', c))), pt)

    def test_aes_cbc_ida_e_volta(self):
        a = AES(b'k' * 16)
        dados = bytes(range(256)) * 3
        self.assertEqual(a.decifrar_cbc(b'i' * 16, a.cifrar_cbc(b'i' * 16, dados)), dados)

    def test_rc4_vetor(self):
        self.assertEqual(rc4(b'Key', b'Plaintext').hex(), 'bbf316e8d940af0ad3')


def _resultado():
    d = texto_colado('Texto colado 1', 'CPF 052.982.247-26 e 529.982.247-25\nTel +55 81 98765-4321\n'
                                       'IMEI 352099001761481\nNome: Ana Lima Souza\n=HYPERLINK("x")@x.com')
    d.id = 'd1'
    return d, analisar([d])


class TestExportar(unittest.TestCase):
    def test_xlsx_bem_formado_e_texto(self):
        d, r = _resultado()
        dados = gerar_xlsx(r.itens, r.descartes, [d.resumo()], {'telefone': 'e164'})
        z = zipfile.ZipFile(io.BytesIO(dados))
        for nome in z.namelist():
            if nome.endswith('.xml') or nome.endswith('.rels'):
                ET.fromstring(z.read(nome))   # XML válido
        folha = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        self.assertIn('t="inlineStr"', folha)
        self.assertIn('+5581987654321', folha)
        self.assertNotIn('<f>', folha)
        wb = z.read('xl/workbook.xml').decode('utf-8')
        for aba in ('Dados', 'Telefones', 'CPFs', 'Ocorrências', 'Fontes'):
            self.assertIn('name="%s"' % aba, wb)

    def test_formato_padrao_telefone(self):
        d, r = _resultado()
        txt = gerar_csv(r.itens).decode('utf-8')
        self.assertIn(';5581987654321;', txt)
        z = zipfile.ZipFile(io.BytesIO(gerar_xlsx(r.itens)))
        self.assertIn('>5581987654321<', z.read('xl/worksheets/sheet1.xml').decode('utf-8'))

    def test_csv_bom_e_formula(self):
        d, r = _resultado()
        txt = gerar_csv(r.itens, {'telefone': 'e164'}).decode('utf-8')
        self.assertTrue(txt.startswith('﻿'))
        self.assertIn(";'+5581987654321;", txt)


class TestServidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Servidor(0, Estado(), 'TOKEN123')
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.base = 'http://127.0.0.1:%d' % cls.srv.porta

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, caminho, dados=None, token='TOKEN123', host=None, metodo=None):
        r = urllib.request.Request(self.base + caminho, data=dados, method=metodo)
        if token:
            r.add_header('X-Token', token)
        if host:
            r.add_header('Host', host)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_fluxo_completo(self):
        st, corpo, _ = self.req('/?t=TOKEN123', token=None)
        self.assertEqual(st, 200)
        self.assertIn(b'SisExtractDados', corpo)
        st, corpo, _ = self.req('/api/texto', 'Tel (81) 98765-4321, e-mail a@b.com.br'.encode('utf-8'))
        self.assertEqual(st, 200)
        caminho = os.path.join(AQUI, 'amostras', 'amostra.docx')
        with open(caminho, 'rb') as f:
            st, corpo, _ = self.req('/api/arquivo?nome=amostra.docx', f.read())
        self.assertEqual(json.loads(corpo)['tipo'], 'Word (.docx)')
        st, corpo, _ = self.req('/api/analisar', json.dumps({'nomes': True}).encode())
        res = json.loads(corpo)
        self.assertEqual(st, 200)
        self.assertEqual(len(res['docs']), 2)
        self.assertIn('a@b.com.br', [i['valor'] for i in res['itens']])
        st, corpo, h = self.req('/api/xlsx', json.dumps({'itens': res['itens'], 'docs': res['docs']}).encode())
        self.assertEqual(st, 200)
        self.assertTrue(corpo.startswith(b'PK'))
        self.assertIn('attachment', h.get('Content-Disposition'))
        st, _, _ = self.req('/api/limpar', b'')
        st, corpo, _ = self.req('/api/estado')
        self.assertEqual(json.loads(corpo)['docs'], [])

    def test_seguranca(self):
        self.assertEqual(self.req('/?t=errado', token=None)[0], 403)
        self.assertEqual(self.req('/api/estado', token='errado')[0], 403)
        self.assertEqual(self.req('/api/estado', token=None)[0], 403)
        self.assertEqual(self.req('/api/estado', host='evil.example:80')[0], 403)
        st, _, h = self.req('/?t=TOKEN123', token=None)
        self.assertIn("frame-ancestors 'none'", h.get('Content-Security-Policy'))


if __name__ == '__main__':
    unittest.main()
