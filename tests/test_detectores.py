# -*- coding: utf-8 -*-
"""Regras de e-mail, telefone, IMEI e CPF."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_analisador'))

from extdados.analise import Unidade, analisar, detectar_unidade  # noqa: E402
from extdados.detectores import cpf_valido, dv_luhn  # noqa: E402
from extdados.leitores import texto_colado  # noqa: E402


def det(texto, rotulo=None, anterior=''):
    cs, ds = detectar_unidade(Unidade(texto, rotulo, 'x', anterior, (0, 0, 0) if rotulo else None), com_nomes=False)
    return cs, ds


def tipos(texto, **kw):
    cs, _ = det(texto, **kw)
    return [(c.tipo, c.valor, c.status) for c in cs]


def um(texto, **kw):
    cs, _ = det(texto, **kw)
    assert len(cs) == 1, (texto, cs)
    return cs[0]


class TestValidacoes(unittest.TestCase):
    def test_cpf(self):
        self.assertTrue(cpf_valido('52998224725'))
        self.assertTrue(cpf_valido('11144477735'))
        self.assertFalse(cpf_valido('52998224724'))
        self.assertFalse(cpf_valido('11111111111'))
        self.assertFalse(cpf_valido('5299822472'))

    def test_luhn_imei(self):
        self.assertEqual(dv_luhn('49015420323751'), '8')   # exemplo clássico (Wikipedia)
        self.assertEqual(dv_luhn('35209900176148'), '1')


class TestEmail(unittest.TestCase):
    def test_basico_e_minusculas(self):
        cs, _ = det('Contato: Maria.Silva@Gmail.COM, outro: x_y+z@sub.dominio.gov.br.')
        self.assertEqual([c.valor for c in cs], ['maria.silva@gmail.com', 'x_y+z@sub.dominio.gov.br'])
        self.assertEqual(cs[0].formatos['original'], 'Maria.Silva@gmail.com')

    def test_nome_de_arquivo_descartado(self):
        cs, ds = det('logo@2x.png')
        self.assertEqual(cs, [])
        self.assertEqual(ds[0].regra, 'R-EML-91')

    def test_ofuscado(self):
        c = um('escreva para fulano [at] gmail [dot] com')
        self.assertEqual((c.valor, c.status, c.regra), ('fulano@gmail.com', 'atencao', 'R-EML-02'))
        self.assertEqual(um('fulano arroba hotmail ponto com ponto br').valor, 'fulano@hotmail.com.br')

    def test_descartavel(self):
        c = um('a@mailinator.com')
        self.assertEqual(c.status, 'atencao')

    def test_pontos_invalidos(self):
        cs, _ = det('joao..silva@gmail.com')
        self.assertEqual(cs, [])


class TestTelefone(unittest.TestCase):
    def e164(self, texto, **kw):
        return [c.formatos['e164'] for c in det(texto, **kw)[0] if c.tipo == 'telefone']

    def test_formatos_com_ddd(self):
        for t in ['(81) 98765-4321', '(81)98765-4321', '81 98765-4321', '81 9 8765-4321', '(81) 9 8765-4321',
                  '81-98765-4321', '81.98765.4321', '(081) 98765-4321', '081 98765-4321',
                  '+55 81 98765-4321', '+55 (81) 98765-4321', '+5581987654321', '55 81 98765-4321',
                  '5581987654321', '081987654321', '+55 81 9 8765 4321']:
            self.assertEqual(self.e164(t), ['+5581987654321'], t)

    def test_onze_digitos_sem_formatacao(self):
        self.assertEqual(self.e164('81987654321'), ['+5581987654321'])

    def test_fixo(self):
        self.assertEqual(self.e164('(81) 3333-4444'), ['+558133334444'])
        self.assertEqual(self.e164('81 3333-4444'), ['+558133334444'])
        # 10 dígitos colados só com contexto (evita timestamp Unix como 1729440000)
        self.assertEqual(self.e164('1729440000'), [])
        self.assertEqual(self.e164('telefone 1729440000'), ['+551729440000'])
        # separado só por espaço = formatação fraca: fixo exige contexto
        self.assertEqual(self.e164('12 3456 7890'), [])
        self.assertEqual(self.e164('fone 12 3456 7890'), ['+551234567890'])

    def test_sem_ddd(self):
        c = um('ligue 98765-4321')
        self.assertEqual((c.valor, c.status, c.regra), ('98765-4321', 'atencao', 'R-TEL-05'))
        self.assertEqual(tipos('código 3333-4444'), [])
        self.assertEqual(um('Tel. 3333-4444').valor, '3333-4444')
        self.assertEqual(tipos('conta 98765-4321'), [])

    def test_ddd_inexistente(self):
        cs, ds = det('(20) 98765-4321')
        self.assertEqual(cs, [])
        self.assertEqual(ds[0].regra, 'R-TEL-94')

    def test_formato_antigo(self):
        c = um('cel (81) 8765-4321')
        self.assertEqual((c.status, c.formatos['e164']), ('atencao', '+558187654321'))

    def test_internacional(self):
        cs, _ = det('+1 (555) 234-5678 / +351 912 345 678 / +44 20 7946 0958')
        self.assertEqual([c.formatos['e164'] for c in cs], ['+15552345678', '+351912345678', '+442079460958'])
        self.assertEqual(cs[0].status, 'ok')
        self.assertEqual(cs[1].extra['pais'], 'Portugal')

    def test_servico(self):
        c = um('SAC 0800 123 4567')
        self.assertEqual((c.regra, c.status), ('R-TEL-07', 'atencao'))

    def test_contexto_negativo(self):
        cs, ds = det('conta 81987654321')
        self.assertEqual(cs, [])
        self.assertEqual(ds[0].regra, 'R-TEL-93')

    def test_whatsapp_id(self):
        self.assertEqual(self.e164('WhatsApp ID: 5581987654321'), ['+5581987654321'])

    def test_formato_padrao_de_copia_e164(self):
        self.assertEqual(um('(81) 98765-4321').formatos['e164'], '+5581987654321')
        self.assertEqual(um('81987654321', anterior='Tel').formatos['e164'], '+5581987654321')
        self.assertEqual(um('fone (81) 3333-4444').formatos['e164'], '+558133334444')
        self.assertEqual(um('+1 (555) 234-5678').formatos['e164'], '+15552345678')
        self.assertEqual(um('+351 912 345 678').formatos['e164'], '+351912345678')
        # sem DDD ou 0800 não há DDI/DDD a acrescentar: só os dígitos, sem "+" nem traço
        self.assertEqual(um('ligue 98765-4321').formatos['e164'], '987654321')
        self.assertEqual(um('SAC 0800 123 4567').formatos['e164'], '08001234567')
        for c in det('(81) 98765-4321 / +55 81 3333-4444 / +1 555 234 5678')[0]:
            self.assertRegex(c.formatos['e164'], r'^\+\d+$')

    def test_uf_do_ddd(self):
        self.assertEqual(um('(83) 99999-0000').extra['uf'], 'PB')

    def test_varios_na_mesma_cadeia(self):
        self.assertEqual(self.e164('Tel: (81) 98765-4321 / 81 3333-4444'), ['+5581987654321', '+558133334444'])
        # contexto vindo do fim da linha anterior (rótulo acima do valor)
        self.assertEqual(self.e164('3333-4444 81 98765-4321', anterior='Telefones:'), ['33334444', '+5581987654321'])


class TestFronteiras(unittest.TestCase):
    def test_nao_extrai_de_sequencias_maiores(self):
        for t in ['Chave NFe 26201012345678000190550010000123451000123456',
                  'E2E E18236120202310191254s0123456789', 'Valor R$ 81.987.654,32',
                  'CNPJ 12.345.678/0001-90 em 20/10/2026 às 13:26', 'IP 191.247.9.12 porta 64067',
                  'Processo 0001234-56.2023.8.17.0001', 'boleto 34191.79001 01043.510047 91020.150008 1 96610000025000',
                  'cartão 4111 1111 1111 1111', 'ID123456789012345']:
            self.assertEqual(tipos(t), [], t)

    def test_colado_em_letras(self):
        self.assertEqual(tipos('abc81987654321'), [])
        self.assertEqual(tipos('81987654321xyz'), [])


class TestCPF(unittest.TestCase):
    def test_formatado(self):
        c = um('CPF 529.982.247-25')
        self.assertEqual((c.valor, c.status, c.regra), ('529.982.247-25', 'ok', 'R-CPF-01'))
        c = um('CPF 529.982.247-24')
        self.assertEqual((c.status, c.regra), ('invalido', 'R-CPF-02'))
        self.assertEqual(um('529982247-25').valor, '529.982.247-25')

    def test_placeholder(self):
        cs, ds = det('CPF 000.000.000-00')
        self.assertEqual(cs, [])
        self.assertEqual(ds[0].regra, 'R-CPF-92')

    def test_sem_formatacao(self):
        self.assertEqual(um('52998224725').regra, 'R-CPF-04')
        c = um('CPF: 52998224724')
        self.assertEqual((c.status, c.regra), ('invalido', 'R-CPF-03'))

    def test_zero_a_esquerda_restaurado(self):
        c = um('1234567890', rotulo='CPF')
        self.assertEqual((c.valor, c.regra), ('012.345.678-90', 'R-CPF-05'))

    def test_mascarado(self):
        c = um('PIX de ***.456.789-** recebido')
        self.assertEqual((c.valor, c.regra), ('***.456.789-**', 'R-CPF-06'))
        self.assertEqual(um('•••.456.789-••').valor, '***.456.789-**')

    def test_contexto_mais_proximo_vence(self):
        cs, _ = det('CPF: 123.456.789-09 e 81987654321')
        self.assertEqual([c.tipo for c in cs], ['cpf', 'telefone'])

    def test_ambiguo(self):
        cs, _ = det('81987654099')   # passa no DV de CPF e é celular válido
        self.assertEqual((cs[0].tipo, cs[0].regra, cs[0].alternativa.tipo), ('cpf', 'R-AMB-01', 'telefone'))


class TestIMEI(unittest.TestCase):
    def test_valido(self):
        for t in ['352099001761481', '35-209900-176148-1', '35209900 176148 1', '352099-00-176148-1']:
            c = um('IMEI ' + t)
            self.assertEqual((c.valor, c.status, c.regra), ('352099001761481', 'ok', 'R-IMEI-01'), t)

    def test_dv_zerado(self):
        c = um('352099001761480')
        self.assertEqual((c.valor, c.status, c.regra), ('352099001761481', 'atencao', 'R-IMEI-02'))

    def test_14_digitos_com_contexto(self):
        self.assertEqual(um('IMEI: 35209900176148').valor, '352099001761481')
        self.assertEqual(tipos('35209900176148'), [])

    def test_imeisv(self):
        c = um('IMEISV 3520990017614803')
        self.assertEqual((c.valor, c.regra), ('352099001761481', 'R-IMEI-05'))

    def test_dv_invalido_com_contexto(self):
        c = um('IMEI 352099001761482')
        self.assertEqual((c.status, c.regra), ('invalido', 'R-IMEI-03'))

    def test_id_facebook_nao_e_imei(self):
        self.assertEqual(tipos('100004567891234'), [])

    def test_imsi(self):
        cs, ds = det('724051234567890')
        self.assertEqual(cs, [])
        self.assertEqual(ds[0].regra, 'R-IMEI-91')


class TestResolucao(unittest.TestCase):
    def itens(self, texto):
        d = texto_colado('t', texto)
        d.id = 't'
        return analisar([d], com_nomes=False).itens

    def test_ambiguo_resolvido_por_outra_ocorrencia(self):
        its = self.itens('81987654099\nTelefone: (81) 98765-4099')
        self.assertEqual([(i['tipo'], i['status']) for i in its], [('telefone', 'ok')])
        its = self.itens('81987654099\nCPF 819.876.540-99')
        self.assertEqual([(i['tipo'], i['status']) for i in its], [('cpf', 'ok')])

    def test_ambiguo_sem_evidencia_vira_cpf(self):
        # decisão do usuário: 11 dígitos contíguos com DV válido = CPF
        its = self.itens('81987654099')
        self.assertEqual([(i['tipo'], i['status'], i['regras']) for i in its], [('cpf', 'ok', ['R-AMB-04'])])
        self.assertIn('celular', its[0]['motivo'])
        # DV inválido continua telefone
        its = self.itens('81987654098')
        self.assertEqual([(i['tipo'], i['regras']) for i in its], [('telefone', ['R-TEL-03'])])
        # com espaços não é "contíguo": segue as regras de telefone
        for t in ('81 98765 4099', '81 98765-4099', '(81)98765-4099'):
            self.assertEqual([i['tipo'] for i in self.itens(t)], ['telefone'], t)

    def test_contexto_de_telefone_prevalece(self):
        its = self.itens('Tel: 81987654099')
        self.assertEqual([i['tipo'] for i in its], ['telefone'])
        self.assertIn('DV de CPF', its[0]['motivo'])

    def test_ambiguo_resolvido_pela_coluna(self):
        texto = 'Coluna1\tData\n' + '\n'.join('8198765%04d\t01/01' % k for k in (1, 2, 3, 4)) + '\n81987654099\t02/01'
        d = texto_colado('t', texto)
        d.id = 't'
        its = analisar([d], com_nomes=False).itens
        alvo = [i for i in its if i['formatos'].get('digitos') == '81987654099']
        self.assertEqual([(i['tipo'], i['regras']) for i in alvo], [('telefone', ['R-AMB-03'])])

    def test_deduplicacao_e_contagem(self):
        its = self.itens('(81) 98765-4321\n81987654321\n+55 81 98765-4321')
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]['n'], 3)

    def test_imei_formas_diferentes_mesmo_aparelho(self):
        its = self.itens('IMEI 352099001761481\n352099001761480')
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]['status'], 'ok')
        self.assertIn('Formas encontradas', ' '.join(its[0]['obs']))

    def test_cpf_mascarado_compativel(self):
        its = self.itens('CPF 529.982.247-25\nPIX de ***.982.247-**')
        masc = [i for i in its if '*' in i['valor']][0]
        self.assertEqual(masc['extra']['compativeis'], ['529.982.247-25'])

    def test_sem_ddd_relacionado(self):
        its = self.itens('Tel: (81) 98765-4321\nrecado no 98765-4321')
        local = [i for i in its if i['extra'].get('sem_ddd')][0]
        self.assertIn('(81) 98765-4321', ' '.join(local['obs']))


if __name__ == '__main__':
    unittest.main()
