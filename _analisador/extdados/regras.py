# -*- coding: utf-8 -*-
"""Constantes, listas e palavras-chave usadas pelas regras de detecção.

Tudo aqui é explícito e auditável: nenhuma regra depende de modelo estatístico.
O README descreve cada regra pelo seu código (R-CPF-01, R-TEL-03...).
"""
import os
import re

from .normaliza import chave_texto

_DADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dados')

# ---------------------------------------------------------------- telefonia
# Os 67 DDDs em uso no Brasil (Anatel), conferidos contra a base de municípios.
DDDS = frozenset((
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35,
    37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64,
    65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88,
    89, 91, 92, 93, 94, 95, 96, 97, 98, 99))

DDD_UF = {
    11: 'SP', 12: 'SP', 13: 'SP', 14: 'SP', 15: 'SP', 16: 'SP', 17: 'SP', 18: 'SP',
    19: 'SP', 21: 'RJ', 22: 'RJ', 24: 'RJ', 27: 'ES', 28: 'ES', 31: 'MG', 32: 'MG',
    33: 'MG', 34: 'MG', 35: 'MG', 37: 'MG', 38: 'MG', 41: 'PR', 42: 'PR/SC',
    43: 'PR', 44: 'PR', 45: 'PR', 46: 'PR', 47: 'SC/PR', 48: 'SC', 49: 'SC/PR',
    51: 'RS', 53: 'RS', 54: 'RS', 55: 'RS', 61: 'DF/GO', 62: 'GO', 63: 'TO',
    64: 'GO', 65: 'MT', 66: 'MT', 67: 'MS', 68: 'AC', 69: 'RO', 71: 'BA', 73: 'BA',
    74: 'BA', 75: 'BA', 77: 'BA', 79: 'SE', 81: 'PE', 82: 'AL', 83: 'PB', 84: 'RN',
    85: 'CE', 86: 'PI', 87: 'PE', 88: 'CE', 89: 'PI', 91: 'PA', 92: 'AM', 93: 'PA',
    94: 'PA', 95: 'RR', 96: 'AP', 97: 'AM', 98: 'MA', 99: 'MA'}

# Códigos de discagem internacional (DDI) -> país. Usados só quando o número
# vem com "+" explícito e não começa por 55.
DDI = {
    '1': 'EUA/Canadá/Caribe (NANP)', '7': 'Rússia/Cazaquistão', '20': 'Egito',
    '27': 'África do Sul', '30': 'Grécia', '31': 'Países Baixos', '32': 'Bélgica',
    '33': 'França', '34': 'Espanha', '36': 'Hungria', '39': 'Itália', '40': 'Romênia',
    '41': 'Suíça', '43': 'Áustria', '44': 'Reino Unido', '45': 'Dinamarca',
    '46': 'Suécia', '47': 'Noruega', '48': 'Polônia', '49': 'Alemanha', '51': 'Peru',
    '52': 'México', '53': 'Cuba', '54': 'Argentina', '56': 'Chile', '57': 'Colômbia',
    '58': 'Venezuela', '60': 'Malásia', '61': 'Austrália', '62': 'Indonésia',
    '63': 'Filipinas', '64': 'Nova Zelândia', '65': 'Singapura', '66': 'Tailândia',
    '81': 'Japão', '82': 'Coreia do Sul', '84': 'Vietnã', '86': 'China', '90': 'Turquia',
    '91': 'Índia', '92': 'Paquistão', '93': 'Afeganistão', '94': 'Sri Lanka',
    '95': 'Mianmar', '98': 'Irã', '211': 'Sudão do Sul', '212': 'Marrocos',
    '213': 'Argélia', '216': 'Tunísia', '218': 'Líbia', '220': 'Gâmbia',
    '221': 'Senegal', '222': 'Mauritânia', '223': 'Mali', '224': 'Guiné',
    '225': 'Costa do Marfim', '226': 'Burkina Faso', '227': 'Níger', '228': 'Togo',
    '229': 'Benin', '230': 'Maurício', '231': 'Libéria', '232': 'Serra Leoa',
    '233': 'Gana', '234': 'Nigéria', '235': 'Chade', '236': 'Rep. Centro-Africana',
    '237': 'Camarões', '238': 'Cabo Verde', '239': 'São Tomé e Príncipe',
    '240': 'Guiné Equatorial', '241': 'Gabão', '242': 'Congo', '243': 'RD Congo',
    '244': 'Angola', '245': 'Guiné-Bissau', '246': 'Diego Garcia', '247': 'Ascensão',
    '248': 'Seicheles', '249': 'Sudão', '250': 'Ruanda', '251': 'Etiópia',
    '252': 'Somália', '253': 'Djibuti', '254': 'Quênia', '255': 'Tanzânia',
    '256': 'Uganda', '257': 'Burundi', '258': 'Moçambique', '260': 'Zâmbia',
    '261': 'Madagascar', '262': 'Reunião/Mayotte', '263': 'Zimbábue', '264': 'Namíbia',
    '265': 'Malawi', '266': 'Lesoto', '267': 'Botsuana', '268': 'Essuatíni',
    '269': 'Comores', '290': 'Santa Helena', '291': 'Eritreia', '297': 'Aruba',
    '298': 'Ilhas Faroé', '299': 'Groenlândia', '350': 'Gibraltar', '351': 'Portugal',
    '352': 'Luxemburgo', '353': 'Irlanda', '354': 'Islândia', '355': 'Albânia',
    '356': 'Malta', '357': 'Chipre', '358': 'Finlândia', '359': 'Bulgária',
    '370': 'Lituânia', '371': 'Letônia', '372': 'Estônia', '373': 'Moldávia',
    '374': 'Armênia', '375': 'Belarus', '376': 'Andorra', '377': 'Mônaco',
    '378': 'San Marino', '380': 'Ucrânia', '381': 'Sérvia', '382': 'Montenegro',
    '383': 'Kosovo', '385': 'Croácia', '386': 'Eslovênia', '387': 'Bósnia',
    '389': 'Macedônia do Norte', '420': 'Tchéquia', '421': 'Eslováquia',
    '423': 'Liechtenstein', '500': 'Malvinas', '501': 'Belize', '502': 'Guatemala',
    '503': 'El Salvador', '504': 'Honduras', '505': 'Nicarágua', '506': 'Costa Rica',
    '507': 'Panamá', '508': 'Saint-Pierre', '509': 'Haiti', '590': 'Guadalupe',
    '591': 'Bolívia', '592': 'Guiana', '593': 'Equador', '594': 'Guiana Francesa',
    '595': 'Paraguai', '596': 'Martinica', '597': 'Suriname', '598': 'Uruguai',
    '599': 'Curaçao', '670': 'Timor-Leste', '672': 'Território Australiano',
    '673': 'Brunei', '674': 'Nauru', '675': 'Papua-Nova Guiné', '676': 'Tonga',
    '677': 'Ilhas Salomão', '678': 'Vanuatu', '679': 'Fiji', '680': 'Palau',
    '681': 'Wallis e Futuna', '682': 'Ilhas Cook', '683': 'Niue', '685': 'Samoa',
    '686': 'Kiribati', '687': 'Nova Caledônia', '688': 'Tuvalu',
    '689': 'Polinésia Francesa', '690': 'Tokelau', '691': 'Micronésia',
    '692': 'Ilhas Marshall', '850': 'Coreia do Norte', '852': 'Hong Kong',
    '853': 'Macau', '855': 'Camboja', '856': 'Laos', '880': 'Bangladesh',
    '886': 'Taiwan', '960': 'Maldivas', '961': 'Líbano', '962': 'Jordânia',
    '963': 'Síria', '964': 'Iraque', '965': 'Kuwait', '966': 'Arábia Saudita',
    '967': 'Iêmen', '968': 'Omã', '970': 'Palestina', '971': 'Emirados Árabes',
    '972': 'Israel', '973': 'Bahrein', '974': 'Catar', '975': 'Butão', '976': 'Mongólia',
    '977': 'Nepal', '992': 'Tajiquistão', '993': 'Turcomenistão', '994': 'Azerbaijão',
    '995': 'Geórgia', '996': 'Quirguistão', '998': 'Uzbequistão'}

# ---------------------------------------------------------------- IMEI
# Reporting Body Identifier (2 primeiros dígitos do IMEI) aceitos SEM contexto.
# "10" fica de fora de propósito: IDs de usuário do Facebook têm 15 dígitos e
# começam por "100", e passariam por IMEI em 1 de cada 10 casos (Luhn).
RBI_AUTOMATICO = frozenset((
    '01', '30', '33', '35', '44', '45', '49', '50', '51', '52', '53', '54',
    '86', '91', '98', '99'))

# ---------------------------------------------------------------- e-mail
DOMINIOS_DESCARTAVEIS = frozenset((
    '10minutemail.com', '20minutemail.com', 'dispostable.com', 'emailondeck.com',
    'fakeinbox.com', 'getnada.com', 'guerrillamail.com', 'guerrillamail.net',
    'guerrillamailblock.com', 'mailinator.com', 'maildrop.cc', 'mintemail.com',
    'mohmal.com', 'mytemp.email', 'sharklasers.com', 'spam4.me', 'temp-mail.org',
    'tempmail.com', 'tempmail.net', 'tempmailo.com', 'tempr.email', 'throwawaymail.com',
    'trashmail.com', 'yopmail.com', 'yopmail.net', 'tmpmail.org', 'tmail.ws',
    'mail.tm', 'emailfake.com', 'inboxkitten.com', 'burnermail.io', 'moakt.com'))

EXTENSOES_ARQUIVO = frozenset((
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico', 'tif', 'tiff',
    'js', 'css', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'mp4', 'mp3'))

# ---------------------------------------------------------------- contexto
# Palavras-chave procuradas ANTES do valor (mesma linha/célula, rótulo da coluna
# ou fim da linha anterior). Vale a palavra-chave mais PRÓXIMA do valor.
# O texto é comparado em minúsculas e sem acento.
CONTEXTO = [
    ('cpf', r'c\.?\s?p\.?\s?f|contribuinte|cadastro de pessoas? fisicas?'),
    ('imei', r'imei(?:sv|s)?|aparelhos?|dispositivos?|devices?|handsets?|equipamentos?'
             r'|meid|esn'),
    ('tel', r'whats\w*\s?(?:id|identifier)|account\s?(?:id|identifier)|target'
            r'|tel|tels|telefones?|fones?|cel|celular(?:es)?|whats\w*|wpp|zap|contatos?'
            r'|msisdn|linhas?|ramal|fixo|movel|terminal|numeros?|num|originador'
            r'|chamad[oa]s?|chamador|interlocutor|sms|ligac\w*|discad\w*|recado'),
    ('neg', r'cnpj|rg|identidade|protocolo|processo|contas?|c/c|cc|agencia|ag|pedido'
            r'|ticket|matricula|boleto|codigo|cod|nis|pis|pasep|nit|titulo|renavam'
            r'|chassi|cnh|registro|imsi|iccid|cep|nota|nfe?|id|identificador|transac\w*'
            r'|e2e|endtoend|autenticac\w*|nsu|inscric\w*|bo|ocorrencia|sei|cartao'
            r'|cartoes|serial|serie|lote|placa|cns|sus|oab|siape|passaporte|valor'
            r'|r\$|us\$|saldo|cod\.? barras|linha digitavel|ip|porta'),
]
_CTX_RE = re.compile('|'.join(
    r'(?P<%s>(?<![a-z0-9])(?:%s)(?![a-z0-9]))' % (nome, padrao) for nome, padrao in CONTEXTO))

# Exceção: "linha digitável" é boleto (neg), mas "linha" sozinha é telefone.
# Como vale a palavra mais próxima e "digitavel" vem depois de "linha", o neg vence.


def categoria_contexto(janela_minuscula):
    """Retorna ('cpf'|'imei'|'tel'|'neg', palavra) da palavra-chave mais próxima do fim."""
    melhor = None
    for m in _CTX_RE.finditer(janela_minuscula):
        melhor = m
    if melhor is None:
        return None, None
    return melhor.lastgroup, melhor.group(0)


# Rótulos que anunciam um nome próprio ("Nome:", "Titular:", coluna "Favorecido"...).
ROTULO_NOME = re.compile(
    r'(?<![a-z0-9])(?:nome(?: completo| civil| social| do titular| do cliente| da mae| do pai'
    r'| do investigado| da investigada| da vitima| do favorecido| do pagador| do recebedor'
    r'| do beneficiario| do remetente| do destinatario| do usuario)?|titular|favorecid[oa]s?'
    r'|pagador(?:a)?|recebedor(?:a)?|beneficiari[oa]|remetente|destinatari[oa]|investigad[oa]s?'
    r'|suspeit[oa]s?|vitimas?|declarante|testemunhas?|noticiante|comunicante|conduzid[oa]'
    r'|condutor(?:a)?|autuad[oa]|indiciad[oa]|requerente|requerid[oa]|genitor(?:a)?|mae|pai'
    r'|filiacao|conjuge|companheir[oa]|cliente|proprietari[oa]|socio|socia|responsavel'
    r'|contratante|correntista|assinante|outorgante|outorgad[oa]|interessad[oa]|parte)(?![a-z0-9])')

ROTULO_NOME_EXCLUIDO = re.compile(
    r'nome (?:d[oa]s? )?(?:banco|empresa|institui\w*|arquivo|estabelecimento|fantasia|loja'
    r'|plano|produto|servico|campo|aplicativo|app|dispositivo|aparelho|rede|grupo|pasta'
    r'|documento|perfil|conta|operadora|pagina|site|dominio|host|computador|maquina|evento'
    r'|cidade|municipio|rua|logradouro|bairro)|razao social|nome fantasia|usuario do sistema')

CONECTIVOS = frozenset(('DE', 'DA', 'DO', 'DAS', 'DOS', 'E', "D'", 'DI', 'DU', 'DEL',
                        'DELLA', 'DELLE', 'VAN', 'VON', 'DER', 'DEN', 'DAL', 'LA', 'LE'))

# ---------------------------------------------------------------- listas de nomes
LIMIAR_PRENOME_INICIAL = 1000      # frequência mínima (Censo 2010) para INICIAR um nome
LIMIAR_SOBRENOME_COMO_INICIAL = 50000

_cache = {}


def _ler_palavras(nome):
    caminho = os.path.join(_DADOS, nome)
    palavras = set()
    with open(caminho, encoding='utf-8') as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith('#'):
                continue
            palavras.update(chave_texto(p) for p in linha.split())
    return frozenset(palavras)


def listas_nomes():
    """(prenomes_iniciais, nomes_conhecidos, bloqueio) — carregadas uma vez."""
    if 'nomes' in _cache:
        return _cache['nomes']
    freq = {}
    with open(os.path.join(_DADOS, 'prenomes.txt'), encoding='utf-8') as f:
        for linha in f:
            if linha.startswith('#'):
                continue
            nome, _, n = linha.rstrip('\n').partition('\t')
            if nome:
                freq[nome] = int(n or 0)
    sobrenomes = _ler_palavras('sobrenomes.txt')
    bloqueio = _ler_palavras('palavras_bloqueio.txt')
    iniciais = set()
    for nome, n in freq.items():
        if n < LIMIAR_PRENOME_INICIAL or len(nome) < 2:
            continue
        if nome in CONECTIVOS or nome in bloqueio:
            continue
        if nome in ('SANTO', 'SANTA', 'SAO'):
            continue
        if nome in sobrenomes and n < LIMIAR_SOBRENOME_COMO_INICIAL:
            continue
        iniciais.add(nome)
    conhecidos = (set(freq) | sobrenomes) - CONECTIVOS - bloqueio
    _cache['nomes'] = (frozenset(iniciais), frozenset(conhecidos), bloqueio)
    return _cache['nomes']


def municipios():
    """{CHAVE_SEM_ACENTO: [(nome, uf), ...]}"""
    if 'mun' in _cache:
        return _cache['mun']
    mun = {}
    with open(os.path.join(_DADOS, 'municipios.txt'), encoding='utf-8') as f:
        for linha in f:
            if linha.startswith('#'):
                continue
            partes = linha.rstrip('\n').split('\t')
            if len(partes) >= 2:
                mun.setdefault(chave_texto(partes[0]), []).append((partes[0], partes[1]))
    _cache['mun'] = mun
    return mun
