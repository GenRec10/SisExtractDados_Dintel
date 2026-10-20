# -*- coding: utf-8 -*-
"""OCR (Tesseract). Os testes que dependem do Tesseract são pulados se ele não estiver instalado."""
import json
import os
import struct
import sys
import unittest
import zlib

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, '..', '_analisador'))

from extdados import ocr  # noqa: E402
from extdados.analise import analisar  # noqa: E402
from extdados.leitores import ler_arquivo, ler_bytes  # noqa: E402

AMOSTRAS = os.path.join(AQUI, 'amostras')
with open(os.path.join(AMOSTRAS, 'gabarito_ocr.json'), encoding='utf-8') as _f:
    GABARITO = json.load(_f)
TEM_TESSERACT = ocr.motor() is not None


def chave(it):
    f = it['formatos']
    return {'telefone': f.get('e164'), 'cpf': f.get('digitos'), 'imei': f.get('imei14')}.get(it['tipo'], it['valor'])


def ler(nome, usar_ocr=True):
    ocr.definir_contexto(usar_ocr, None)
    try:
        doc = ler_arquivo(os.path.join(AMOSTRAS, nome))
    finally:
        ocr.definir_contexto(True, None)
    doc.id = 'd'
    return doc, analisar([doc])


class TestUtilidades(unittest.TestCase):
    def test_png_valido(self):
        dados = ocr.png(3, 2, 8, 0, b'\x00\x80\xff\xff\x80\x00')
        self.assertTrue(dados.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(ocr.dimensoes(dados), (3, 2))
        pos = 8
        blocos = []
        while pos < len(dados):
            n = struct.unpack('>I', dados[pos:pos + 4])[0]
            tipo = dados[pos + 4:pos + 8]
            corpo = dados[pos + 8:pos + 8 + n]
            crc = struct.unpack('>I', dados[pos + 8 + n:pos + 12 + n])[0]
            self.assertEqual(crc, zlib.crc32(tipo + corpo) & 0xFFFFFFFF)
            blocos.append((tipo, corpo))
            pos += 12 + n
        self.assertEqual([b[0] for b in blocos], [b'IHDR', b'IDAT', b'IEND'])
        self.assertEqual(zlib.decompress(blocos[1][1]), b'\x00\x00\x80\xff\x00\xff\x80\x00')

    def test_tiff_ccitt(self):
        t = ocr.tiff_ccitt(1728, 100, b'\x00' * 10, -1, False)
        self.assertEqual(t[:4], b'II*\x00')
        self.assertEqual(ocr.dimensoes(t), (1728, 100))

    def test_dimensoes(self):
        with open(os.path.join(AMOSTRAS, 'ocr_print.png'), 'rb') as f:
            self.assertEqual(ocr.dimensoes(f.read()), (2480, 1100))
        jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' \
               b'\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01'
        self.assertEqual(ocr.dimensoes(jpeg), (640, 480))
        self.assertEqual(ocr.dimensoes(b'GIF89a\x10\x00\x20\x00'), (16, 32))
        self.assertIsNone(ocr.dimensoes(b'nada'))

    def test_limites(self):
        self.assertFalse(ocr.vale_ocr(64, 64))         # ícone
        self.assertTrue(ocr.vale_ocr(2480, 1100))
        self.assertTrue(ocr.vale_ocr(2480, 60))        # faixa estreita de digitalização

    def test_pdf_pesquisavel_usa_camada_existente(self):
        doc, res = ler('ocr_scan_pesquisavel.pdf')
        self.assertIn('camada_ocr', doc.info)
        self.assertNotIn('ocr', doc.info)
        self.assertIn(GABARITO['cpf'], [chave(i) for i in res.itens])

    def test_ocr_desligado_avisa(self):
        doc, res = ler('ocr_scan_jpeg.pdf', usar_ocr=False)
        self.assertEqual(res.itens, [])
        self.assertTrue(any('sem camada de texto' in a for a in doc.avisos))

    def test_sem_tesseract(self):
        original = ocr._candidatos
        ocr._candidatos = lambda: iter(())
        ocr.configurar()
        try:
            self.assertIsNone(ocr.motor())
            self.assertFalse(ocr.diagnostico()['disponivel'])
            with open(os.path.join(AMOSTRAS, 'ocr_print.png'), 'rb') as f:
                doc = ler_bytes('x.png', f.read())
            self.assertTrue(any('Tesseract' in a for a in doc.avisos))
        finally:
            ocr._candidatos = original
            ocr.configurar()


@unittest.skipUnless(TEM_TESSERACT, 'Tesseract não instalado')
class TestComTesseract(unittest.TestCase):
    AMOSTRAS_OCR = ['ocr_print.png', 'ocr_print_no_docx.docx', 'ocr_scan_jpeg.pdf', 'ocr_scan_cinza.pdf',
                    'ocr_scan_paleta.pdf', 'ocr_scan_ccitt.pdf', 'ocr_scan_girado.pdf']

    def test_formatos(self):
        for nome in self.AMOSTRAS_OCR:
            doc, res = ler(nome)
            achados = {(i['tipo'], chave(i)): i for i in res.itens}
            for tipo, valor in GABARITO.items():
                self.assertIn((tipo, valor), achados, '%s: %s não encontrado (%s)' % (nome, tipo, doc.avisos))
            self.assertIn('ocr', doc.info, nome)

    def test_regra_ocr(self):
        _, res = ler('ocr_scan_jpeg.pdf')
        por_tipo = {i['tipo']: i for i in res.itens}
        self.assertEqual(por_tipo['telefone']['status'], 'atencao')
        self.assertEqual(por_tipo['email']['status'], 'atencao')
        self.assertIn('OCR', por_tipo['telefone']['motivo'])
        self.assertEqual(por_tipo['cpf']['status'], 'ok')
        self.assertEqual(por_tipo['imei']['status'], 'ok')
        for i in res.itens:
            self.assertIn('R-OCR-01', i['regras'])
            self.assertEqual(i['extra']['ocr'], 'todas')
            self.assertIn('(OCR)', i['amostras'][0]['local'])

    def test_pagina_mista(self):
        _, res = ler('ocr_misto.pdf')
        por_valor = {chave(i): i for i in res.itens}
        self.assertEqual(por_valor['+5581988776655']['extra']['ocr'], 'todas')     # só no print
        self.assertNotIn('ocr', por_valor['+5581987654321']['extra'])              # só no texto digitado
        self.assertEqual(por_valor[GABARITO['cpf']]['extra']['ocr'], 'algumas')    # nos dois
        self.assertEqual(por_valor[GABARITO['cpf']]['status'], 'ok')

    def test_progresso(self):
        msgs = []
        ocr.definir_contexto(True, msgs.append)
        try:
            ler_arquivo(os.path.join(AMOSTRAS, 'ocr_scan_jpeg.pdf'))
        finally:
            ocr.definir_contexto(True, None)
        self.assertIn('OCR: imagem do PDF 1 de 1', msgs)


if __name__ == '__main__':
    unittest.main()
