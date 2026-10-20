# -*- coding: utf-8 -*-
"""Servidor HTTP local (127.0.0.1) que serve a interface e a API.

Segurança:
  * escuta só em 127.0.0.1 (nenhuma outra máquina da rede alcança);
  * cada execução gera um token aleatório; toda chamada à API precisa dele
    (impede que um site aberto no navegador use o servidor local);
  * o cabeçalho Host é conferido (proteção contra DNS rebinding);
  * nada é gravado em disco: arquivos e resultados ficam só na memória e
    somem quando a janela do programa é fechada.
"""
import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import NOME_SISTEMA, __version__
from .analise import analisar
from .exportar import gerar_csv, gerar_xlsx
from .leitores import LIMITE_ARQUIVO, ler_bytes, texto_colado

_INTERFACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interface', 'index.html')
LIMITE_JSON = 200 * 1024 * 1024


class Estado(object):
    def __init__(self):
        self.docs = []           # ordem de inclusão
        self.trava = threading.Lock()
        self.seq = 0
        self.colados = 0

    def adicionar(self, doc):
        with self.trava:
            self.seq += 1
            doc.id = 'd%d' % self.seq
            self.docs.append(doc)
        return doc

    def remover(self, doc_id):
        with self.trava:
            self.docs = [d for d in self.docs if d.id != doc_id]

    def limpar(self):
        with self.trava:
            self.docs = []

    def lista(self):
        with self.trava:
            return list(self.docs)


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, porta, estado, token):
        self.estado = estado
        self.token = token
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', porta), Manipulador)

    @property
    def porta(self):
        return self.server_address[1]


class Manipulador(BaseHTTPRequestHandler):
    server_version = 'SisExtractDados/' + __version__
    protocol_version = 'HTTP/1.1'

    def log_message(self, formato, *args):
        pass

    # ------------------------------------------------------------ utilidades
    def _host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in ('127.0.0.1:%d' % self.server.porta, 'localhost:%d' % self.server.porta)

    def _token_ok(self):
        t = self.headers.get('X-Token') or ''
        return secrets.compare_digest(t, self.server.token)

    def _enviar(self, codigo, corpo, tipo='application/json; charset=utf-8', extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo, ensure_ascii=False).encode('utf-8')
        elif isinstance(corpo, str):
            corpo = corpo.encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _erro(self, codigo, msg):
        self._enviar(codigo, {'erro': msg})

    def _ler_corpo(self, limite):
        n = int(self.headers.get('Content-Length') or 0)
        if n > limite:
            raise ValueError('Conteúdo maior que o limite de %d MB' % (limite // 1048576))
        partes = []
        resta = n
        while resta > 0:
            bloco = self.rfile.read(min(resta, 1 << 20))
            if not bloco:
                break
            partes.append(bloco)
            resta -= len(bloco)
        return b''.join(partes)

    def _json(self):
        corpo = self._ler_corpo(LIMITE_JSON)
        return json.loads(corpo.decode('utf-8') or '{}')

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if not self._host_ok():
            return self._erro(403, 'Host não permitido')
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/':
            q = urllib.parse.parse_qs(url.query)
            if not secrets.compare_digest((q.get('t') or [''])[0], self.server.token):
                return self._enviar(403, '<h1>Acesso negado</h1><p>Abra a ferramenta pelo atalho '
                                         '(extrair.bat). O endereço muda a cada execução.</p>',
                                    'text/html; charset=utf-8')
            with open(_INTERFACE, 'rb') as f:
                html = f.read()
            return self._enviar(200, html, 'text/html; charset=utf-8', {
                'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                           "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                           "form-action 'none'"})
        if url.path == '/favicon.ico':
            return self._enviar(204, b'', 'image/x-icon')
        if not self._token_ok():
            return self._erro(403, 'Token inválido')
        if url.path == '/api/estado':
            return self._enviar(200, {'sistema': NOME_SISTEMA, 'versao': __version__,
                                      'docs': [d.resumo() for d in self.server.estado.lista()]})
        return self._erro(404, 'Não encontrado')

    # ------------------------------------------------------------ POST
    def do_POST(self):
        if not self._host_ok():
            return self._erro(403, 'Host não permitido')
        if not self._token_ok():
            return self._erro(403, 'Token inválido')
        url = urllib.parse.urlsplit(self.path)
        estado = self.server.estado
        try:
            if url.path == '/api/arquivo':
                nome = urllib.parse.parse_qs(url.query).get('nome', ['arquivo'])[0]
                nome = os.path.basename(nome.replace('\\', '/')) or 'arquivo'
                dados = self._ler_corpo(LIMITE_ARQUIVO)
                doc = estado.adicionar(ler_bytes(nome, dados))
                return self._enviar(200, doc.resumo())
            if url.path == '/api/texto':
                texto = self._ler_corpo(LIMITE_ARQUIVO).decode('utf-8', 'replace')
                with estado.trava:
                    estado.colados += 1
                    n = estado.colados
                doc = estado.adicionar(texto_colado('Texto colado %d' % n, texto))
                return self._enviar(200, doc.resumo())
            if url.path == '/api/remover':
                estado.remover(self._json().get('id'))
                return self._enviar(200, {'ok': True})
            if url.path == '/api/limpar':
                estado.limpar()
                return self._enviar(200, {'ok': True})
            if url.path == '/api/analisar':
                opcoes = self._json()
                docs = estado.lista()
                t0 = time.time()
                res = analisar(docs, com_nomes=bool(opcoes.get('nomes', True))).como_dict()
                res['docs'] = [d.resumo() for d in docs]
                res['segundos'] = round(time.time() - t0, 2)
                return self._enviar(200, res)
            if url.path in ('/api/xlsx', '/api/csv'):
                p = self._json()
                itens, formatos = p.get('itens') or [], p.get('formatos') or None
                carimbo = time.strftime('%Y%m%d_%H%M%S')
                if url.path == '/api/xlsx':
                    corpo = gerar_xlsx(itens, p.get('descartes') or [], p.get('docs') or [], formatos)
                    tipo, ext = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'
                else:
                    corpo = gerar_csv(itens, formatos)
                    tipo, ext = 'text/csv; charset=utf-8', 'csv'
                return self._enviar(200, corpo, tipo, {
                    'Content-Disposition': 'attachment; filename="extracao_%s.%s"' % (carimbo, ext)})
            if url.path == '/api/encerrar':
                self._enviar(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self._erro(404, 'Não encontrado')
        except ValueError as e:
            return self._erro(400, str(e))
        except Exception as e:
            traceback.print_exc()
            return self._erro(500, '%s: %s' % (type(e).__name__, e))


def iniciar(arquivos=(), porta=0, abrir_navegador=True):
    estado = Estado()
    for caminho in arquivos:
        if os.path.isdir(caminho):
            for raiz, _, nomes in os.walk(caminho):
                for n in sorted(nomes):
                    _carregar(estado, os.path.join(raiz, n))
        else:
            _carregar(estado, caminho)
    token = secrets.token_urlsafe(24)
    try:
        srv = Servidor(porta, estado, token)
    except OSError as e:
        print('Não foi possível abrir a porta %s (%s). Tentando outra...' % (porta, e))
        srv = Servidor(0, estado, token)
    url = 'http://127.0.0.1:%d/?t=%s' % (srv.porta, token)
    print('')
    print('  %s %s — processamento 100%% local' % (NOME_SISTEMA, __version__))
    print('  Interface: %s' % url)
    print('  Feche esta janela (ou use "Encerrar" na interface) para finalizar.')
    print('')
    if abrir_navegador:
        threading.Timer(0.6, lambda: _abrir(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print('Encerrado.')


def _abrir(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        print('Abra manualmente no navegador: %s' % url)


def _carregar(estado, caminho):
    from .leitores import ler_arquivo
    t0 = time.time()
    sys.stdout.write('  lendo %s ... ' % caminho)
    sys.stdout.flush()
    try:
        doc = ler_arquivo(caminho)
    except OSError as e:
        print('ERRO: %s' % e)
        return
    estado.adicionar(doc)
    print('%s (%.1fs)%s' % (doc.tipo, time.time() - t0, (' — ' + '; '.join(doc.avisos)) if doc.avisos else ''))
