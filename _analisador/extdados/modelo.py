# -*- coding: utf-8 -*-
"""Estruturas intermediárias: documento -> blocos (texto corrido ou tabela)."""


class Bloco(object):
    """tipo = 'texto' (texto corrido, linha a linha) ou 'tabela' (lista de linhas de células)."""
    __slots__ = ('tipo', 'nome', 'texto', 'linhas', 'ocr')

    def __init__(self, tipo, nome='', texto='', linhas=None, ocr=False):
        self.tipo = tipo
        self.nome = nome
        self.texto = texto
        self.linhas = linhas or []
        self.ocr = ocr      # texto obtido por OCR (sem verificação de dígito para telefone/e-mail/nome)

    def tamanho(self):
        if self.tipo == 'texto':
            return len(self.texto)
        return sum(len(c) for linha in self.linhas for c in linha if c)


class Documento(object):
    __slots__ = ('id', 'nome', 'tipo', 'tamanho', 'blocos', 'avisos', 'info', 'sha256')

    def __init__(self, nome, tipo='', tamanho=0, sha256=''):
        self.id = ''
        self.nome = nome
        self.tipo = tipo
        self.tamanho = tamanho
        self.blocos = []
        self.avisos = []
        self.info = {}
        self.sha256 = sha256

    def texto(self, nome, texto, ocr=False):
        if texto and texto.strip():
            self.blocos.append(Bloco('texto', nome, texto, ocr=ocr))

    def tabela(self, nome, linhas):
        linhas = [l for l in linhas if any(c for c in l)]
        if linhas:
            self.blocos.append(Bloco('tabela', nome, linhas=linhas))

    def aviso(self, msg):
        if msg not in self.avisos:
            self.avisos.append(msg)

    def caracteres(self):
        return sum(b.tamanho() for b in self.blocos)

    def resumo(self):
        return {
            'id': self.id, 'nome': self.nome, 'tipo': self.tipo, 'tamanho': self.tamanho,
            'sha256': self.sha256, 'avisos': list(self.avisos), 'info': dict(self.info),
            'caracteres': self.caracteres(),
            'blocos': len(self.blocos),
        }
