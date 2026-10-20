# -*- coding: utf-8 -*-
"""Linha de comando.

  extrair.py                              abre a interface no navegador
  extrair.py arq1.pdf pasta/ ...          abre a interface já com os arquivos carregados
  extrair.py --sem-interface arquivos...  processa e grava XLSX + CSV (sem navegador)
"""
import argparse
import os
import sys
import time

from . import NOME_SISTEMA, __version__


def principal(argv=None):
    p = argparse.ArgumentParser(prog='extrair', description='%s %s — extrai e-mail, telefone, IMEI, CPF e '
                                'nomes próprios de arquivos ou texto colado.' % (NOME_SISTEMA, __version__))
    p.add_argument('arquivos', nargs='*', help='arquivos ou pastas a carregar')
    p.add_argument('--sem-interface', action='store_true', help='processa direto e grava XLSX/CSV')
    p.add_argument('--saida', default=None, help='pasta de saída do modo --sem-interface (padrão: _extracao)')
    p.add_argument('--sem-nomes', action='store_true', help='não detectar nomes próprios')
    p.add_argument('--porta', type=int, default=0, help='porta local (padrão: automática)')
    p.add_argument('--sem-ocr', action='store_true', help='não usar OCR em imagens e PDFs digitalizados')
    p.add_argument('--tesseract', default=None, help='caminho do tesseract(.exe), se não for achado sozinho')
    p.add_argument('--ocr-idioma', default=None, help='idioma(s) do OCR, ex.: por+eng (padrão: automático)')
    p.add_argument('--sem-navegador', action='store_true', help='não abrir o navegador automaticamente')
    a = p.parse_args(argv)
    from . import ocr
    ocr.configurar(a.tesseract, a.ocr_idioma)

    if not a.sem_interface:
        from .servidor import iniciar
        iniciar(a.arquivos, a.porta, not a.sem_navegador, not a.sem_ocr)
        return 0

    diag = ocr.diagnostico()
    if a.sem_ocr:
        print('  OCR: desligado')
    elif diag['disponivel']:
        print('  OCR: Tesseract %s (%s)' % (diag['versao'], diag['idioma']))
    else:
        print('  OCR: indisponível — %s' % diag['motivo'])
    ocr.definir_contexto(not a.sem_ocr, lambda m: (sys.stdout.write('\r        %s   ' % m), sys.stdout.flush()))

    from .analise import analisar
    from .exportar import gerar_csv, gerar_xlsx
    from .leitores import ler_arquivo
    caminhos = []
    for c in a.arquivos:
        if os.path.isdir(c):
            for raiz, _, nomes in os.walk(c):
                caminhos.extend(os.path.join(raiz, n) for n in sorted(nomes))
        else:
            caminhos.append(c)
    if not caminhos:
        print('Informe ao menos um arquivo.')
        return 2
    docs = []
    for i, c in enumerate(caminhos, 1):
        t0 = time.time()
        d = ler_arquivo(c)
        d.id = 'd%d' % i
        docs.append(d)
        print('\r  [%d/%d] %s — %s (%.1fs)%s' % (i, len(caminhos), c, d.tipo, time.time() - t0,
                                                (' — ' + d.info['ocr']) if d.info.get('ocr') else ''))
        for av in d.avisos:
            print('        aviso: %s' % av)
    res = analisar(docs, com_nomes=not a.sem_nomes)
    saida = a.saida or os.path.join(os.path.dirname(os.path.abspath(caminhos[0])), '_extracao')
    os.makedirs(saida, exist_ok=True)
    carimbo = time.strftime('%Y%m%d_%H%M%S')
    resumos = [d.resumo() for d in docs]
    caminho_xlsx = os.path.join(saida, 'extracao_%s.xlsx' % carimbo)
    with open(caminho_xlsx, 'wb') as f:
        f.write(gerar_xlsx(res.itens, res.descartes, resumos))
    caminho_csv = os.path.join(saida, 'extracao_%s.csv' % carimbo)
    with open(caminho_csv, 'wb') as f:
        f.write(gerar_csv(res.itens))
    pt = res.resumo['por_tipo']
    print('')
    print('  E-mails: %d | Telefones: %d | IMEIs: %d | CPFs: %d | Nomes: %d | Descartados: %d' % (
        pt['email']['total'], pt['telefone']['total'], pt['imei']['total'], pt['cpf']['total'],
        pt['nome']['total'], len(res.descartes)))
    print('  Gravado: %s' % caminho_xlsx)
    print('  Gravado: %s' % caminho_csv)
    return 0


if __name__ == '__main__':
    sys.exit(principal())
