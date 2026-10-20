# -*- coding: utf-8 -*-
"""Regras de nomes próprios."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_analisador'))

from extdados.analise import Unidade, detectar_unidade  # noqa: E402
from extdados.nomes import chave_nome, titulo  # noqa: E402


def nomes(texto, rotulo=None):
    cs, ds = detectar_unidade(Unidade(texto, rotulo, 'x', '', (0, 0, 0) if rotulo else None))
    return [(c.valor, c.status, c.regra) for c in cs if c.tipo == 'nome'], [d for d in ds if d.tipo == 'nome']


def valores(texto, rotulo=None):
    return [v for v, _, _ in nomes(texto, rotulo)[0]]


class TestNomes(unittest.TestCase):
    def test_titulo_e_maiusculas(self):
        self.assertEqual(valores('A vítima Maria José dos Santos relatou que Pedro Henrique Kowalski enviou.'),
                         ['Maria José dos Santos', 'Pedro Henrique Kowalski'])
        self.assertEqual(valores('Sr. JOÃO DA SILVA COMPARECEU NESTA DELEGACIA'), ['JOÃO DA SILVA'])

    def test_rotulo_aceita_sobrenome_raro(self):
        r, _ = nomes('NOME: JOSÉ KOWALSKI NOWICKA CPF: 123.456.789-09')
        self.assertEqual(r, [('JOSÉ KOWALSKI NOWICKA', 'ok', 'R-NOM-01')])
        self.assertEqual(valores('Titular: maria aparecida da silva, CPF 52998224725'), ['maria aparecida da silva'])
        self.assertEqual(valores('Favorecido - ANTONIO CARLOS PEREIRA FILHO | Banco do Brasil'),
                         ['ANTONIO CARLOS PEREIRA FILHO'])

    def test_rotulo_excluido(self):
        self.assertEqual(valores('Nome do banco: BANCO DO BRASIL'), [])
        self.assertEqual(valores('Nome: NÃO INFORMADO'), [])

    def test_coluna_de_nomes(self):
        r, _ = nomes('KOWALSKI WIŚNIEWSKI', rotulo='Nome do Titular')
        self.assertEqual(r, [('KOWALSKI WIŚNIEWSKI', 'ok', 'R-NOM-01')])
        self.assertEqual(valores('BANCO DO BRASIL', rotulo='Nome'), [])

    def test_logradouro_e_estabelecimento(self):
        r, d = nomes('Rua Maria Quitéria, 120, Recife/PE')
        self.assertEqual(r, [])
        self.assertEqual(d[0].regra, 'R-NOM-92')
        self.assertEqual(valores('Escola Estadual José Mariano'), [])

    def test_municipio(self):
        r, d = nomes('nascido em João Pessoa/PB, filho de Francisca Alves de Lima')
        self.assertEqual([x[0] for x in r], ['Francisca Alves de Lima'])
        self.assertEqual(d[0].regra, 'R-NOM-91')
        r, _ = nomes('Assunto: Maria Helena')
        self.assertEqual(r[0][1], 'atencao')    # Maria Helena/PR é município

    def test_enumeracao(self):
        self.assertEqual(valores('Maria e José Ferreira foram ouvidos'), ['José Ferreira'])
        self.assertEqual(valores('Maria da Silva e Souza assinou'), ['Maria da Silva e Souza'])

    def test_iniciais_e_apostrofo(self):
        self.assertEqual(valores('MARIA S SANTOS 81 98765-4321'), ['MARIA S SANTOS'])
        self.assertEqual(valores("Ana Carolina D'Ávila e Lucas Gabriel"), ["Ana Carolina D'Ávila", 'Lucas Gabriel'])

    def test_cauda_incerta_em_maiusculas(self):
        r, _ = nomes('JOÃO SILVA ESTELIONATARIO')
        self.assertEqual(r[0][:2], ('JOÃO SILVA ESTELIONATARIO', 'atencao'))
        self.assertEqual(valores('JOAO SILVA ESTELIONATARIO DE FATO'), ['JOAO SILVA'])

    def test_nao_nomes(self):
        for t in ['POLÍCIA CIVIL DE PERNAMBUCO - TERMO DE DECLARAÇÕES', 'Relatório de Análise Investigativa',
                  'Delegacia de Repressão ao Estelionato', 'Maria', 'Banco do Brasil S/A']:
            self.assertEqual(valores(t), [], t)

    def test_cargo_antes_do_nome(self):
        self.assertEqual(valores('Delegada Ana Paula Cavalcanti assinou'), ['Ana Paula Cavalcanti'])

    def test_rotulo_seguinte_encerra(self):
        self.assertEqual(valores('MARIA SILVA NASCIMENTO: 01/01/1990'), ['MARIA SILVA'])

    def test_chave_e_titulo(self):
        self.assertEqual(chave_nome('João da  Silva'), 'JOAO DA SILVA')
        self.assertEqual(chave_nome('JOÃO DA SILVA'), chave_nome('João da Silva'))
        self.assertEqual(titulo('JOÃO DA SILVA FILHO'), 'João da Silva Filho')
        self.assertEqual(titulo("ANA D'ÁVILA"), "Ana D'Ávila")


if __name__ == '__main__':
    unittest.main()
