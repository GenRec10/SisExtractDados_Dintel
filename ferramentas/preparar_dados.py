# -*- coding: utf-8 -*-
"""Gera os arquivos de apoio em _analisador/extdados/dados/ a partir das fontes públicas.

Não é necessário rodar este script para usar a ferramenta: os arquivos gerados
já estão versionados. Ele existe para registrar, de forma reprodutível, de onde
vieram os dados (rastreabilidade).

Fontes (baixe e informe os caminhos):
  1. Prenomes — IBGE, Censo 2010 ("Nomes no Brasil"), 10 mil mais frequentes por
     sexo, compilados em https://github.com/MedidaSP/nomes-brasileiros-ibge
     (ibge-fem-10000.csv e ibge-mas-10000.csv).
  2. Municípios — https://github.com/kelvins/municipios-brasileiros
     (csv/municipios.csv, licença MIT).

Uso:
  python ferramentas/preparar_dados.py ibge-fem-10000.csv ibge-mas-10000.csv municipios.csv
"""
import csv
import os
import sys
import unicodedata

UF = {11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO',
      21: 'MA', 22: 'PI', 23: 'CE', 24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL',
      28: 'SE', 29: 'BA', 31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP', 41: 'PR',
      42: 'SC', 43: 'RS', 50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF'}

DESTINO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       '_analisador', 'extdados', 'dados')


def sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s)
                   if not unicodedata.combining(c))


def main(fem, mas, municipios):
    freq = {}
    for caminho in (fem, mas):
        with open(caminho, encoding='utf-8') as f:
            for linha in csv.DictReader(f):
                nome = sem_acento(linha['nome']).upper().strip()
                freq[nome] = freq.get(nome, 0) + int(linha['freq'])
    with open(os.path.join(DESTINO, 'prenomes.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Prenomes - IBGE Censo 2010 (Nomes no Brasil). NOME<TAB>FREQUENCIA\n')
        for nome, n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
            f.write('%s\t%d\n' % (nome, n))

    with open(municipios, encoding='utf-8') as f:
        linhas = list(csv.DictReader(f))
    with open(os.path.join(DESTINO, 'municipios.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Municipios - kelvins/municipios-brasileiros (MIT). NOME<TAB>UF<TAB>DDD\n')
        for l in sorted(linhas, key=lambda r: (r['nome'], r['codigo_uf'])):
            f.write('%s\t%s\t%s\n' % (l['nome'], UF[int(l['codigo_uf'])], l['ddd']))
    print('prenomes:', len(freq), '| municipios:', len(linhas))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    main(*sys.argv[1:])
