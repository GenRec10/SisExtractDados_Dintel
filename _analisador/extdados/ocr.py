# -*- coding: utf-8 -*-
"""OCR opcional com o Tesseract (programa externo, gratuito e local).

A ferramenta continua funcionando sem ele; com ele instalado passa a ler:
  * imagens soltas (prints, fotos de documentos): PNG, JPEG, TIFF, BMP, GIF, WebP;
  * páginas de PDF digitalizadas e imagens dentro de PDFs;
  * imagens coladas em DOCX, ODT, PPTX e XLSX.

As imagens são entregues ao Tesseract pela entrada padrão (memória): nada é
gravado em disco, salvo se a versão instalada não aceitar entrada pela memória
(nesse caso usa um arquivo temporário apagado logo em seguida).

Onde o Tesseract é procurado, nesta ordem:
  1. --tesseract CAMINHO (linha de comando) ou variável SISEXTRACT_TESSERACT;
  2. _analisador/ocr/Tesseract-OCR/ (cópia portátil, sem instalar nada);
  3. PATH do sistema;
  4. pastas padrão do instalador no Windows (Arquivos de Programas, AppData).
Modelos de idioma: se existir _analisador/ocr/tessdata/ com arquivos
.traineddata, essa pasta é usada; senão, a do próprio Tesseract.
"""
import concurrent.futures
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import zlib

_AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_OCR = os.path.normpath(os.path.join(_AQUI, '..', 'ocr'))
TEMPO_MAXIMO = 180            # segundos por imagem
AREA_MINIMA = 120000          # pixels: imagens menores (ícones, logotipos pequenos) não passam por OCR
LADO_MINIMO = 50
LIMITE_PIXELS = 80000000      # imagens maiores que isso são ignoradas (proteção de memória)

_contexto = threading.local()
_trava = threading.Lock()
_motor = None
_config = {'caminho': None, 'idioma': None}


# ===================================================================== contexto por requisição

def definir_contexto(ativo=True, progresso=None):
    """Liga/desliga o OCR e define a função de progresso para a thread atual."""
    _contexto.ativo = ativo
    _contexto.progresso = progresso


def ativo():
    return getattr(_contexto, 'ativo', True) and motor() is not None


def informar(msg):
    f = getattr(_contexto, 'progresso', None)
    if f:
        try:
            f(msg)
        except Exception:
            pass


# ===================================================================== localização

def configurar(caminho=None, idioma=None):
    global _motor
    with _trava:
        _config['caminho'] = caminho
        _config['idioma'] = idioma
        _motor = None


def motor():
    """Instância de Motor ou None se o Tesseract não estiver disponível (cacheado)."""
    global _motor
    with _trava:
        if _motor is None:
            m = Motor(_config['caminho'], _config['idioma'])
            _motor = m if m.disponivel else False
        return _motor or None


def diagnostico():
    m = motor()
    if m is None:
        tentativa = Motor(_config['caminho'], _config['idioma'])
        return {'disponivel': False, 'motivo': tentativa.erro}
    return {'disponivel': True, 'versao': m.versao, 'idioma': m.idioma, 'caminho': m.exe,
            'rotacao': m.osd, 'aviso': m.aviso}


def _candidatos():
    exe = 'tesseract.exe' if os.name == 'nt' else 'tesseract'
    if _config['caminho']:
        yield _config['caminho']
    if os.environ.get('SISEXTRACT_TESSERACT'):
        yield os.environ['SISEXTRACT_TESSERACT']
    yield os.path.join(PASTA_OCR, 'Tesseract-OCR', exe)
    yield os.path.join(PASTA_OCR, exe)
    achado = shutil.which('tesseract')
    if achado:
        yield achado
    if os.name == 'nt':
        for var in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
            base = os.environ.get(var)
            if base:
                yield os.path.join(base, 'Tesseract-OCR', exe)
        local = os.environ.get('LOCALAPPDATA')
        if local:
            yield os.path.join(local, 'Programs', 'Tesseract-OCR', exe)
            yield os.path.join(local, 'Tesseract-OCR', exe)


def _sem_janela():
    if os.name == 'nt':
        return {'creationflags': 0x08000000}     # CREATE_NO_WINDOW
    return {}


class Motor(object):
    def __init__(self, caminho=None, idioma=None):
        self.disponivel = False
        self.erro = ''
        self.aviso = ''
        self.exe = None
        self.versao = ''
        self.idioma = ''
        self.osd = False
        self.tessdata = None
        self.stdin_ok = True
        for c in _candidatos():
            if c and os.path.isfile(c):
                self.exe = c
                break
        if not self.exe:
            self.erro = 'Tesseract não encontrado (veja a seção OCR do README)'
            return
        try:
            r = subprocess.run([self.exe, '--version'], capture_output=True, timeout=30, **_sem_janela())
            texto = (r.stdout + r.stderr).decode('utf-8', 'replace')
            m = re.search(r'tesseract\s+v?([\d.]+)', texto, re.I)
            self.versao = m.group(1) if m else '?'
        except (OSError, subprocess.SubprocessError) as e:
            self.erro = 'Tesseract encontrado em %s, mas não executou (%s)' % (self.exe, e)
            return
        local = os.path.join(PASTA_OCR, 'tessdata')
        if os.path.isdir(local) and any(n.endswith('.traineddata') for n in os.listdir(local)):
            self.tessdata = local
        idiomas = self._idiomas()
        if idioma:
            self.idioma = idioma
        elif 'por' in idiomas and 'eng' in idiomas:
            self.idioma = 'por+eng'    # 'por' sozinho confunde "@" com "r" em e-mails
        elif 'por' in idiomas:
            self.idioma = 'por'
        elif 'eng' in idiomas:
            self.idioma = 'eng'
            self.aviso = ('Modelo de português (por.traineddata) não instalado: acentos podem sair errados. '
                          'Veja a seção OCR do README.')
        elif idiomas:
            self.idioma = sorted(idiomas)[0]
        else:
            self.erro = 'Tesseract sem modelos de idioma (.traineddata)'
            return
        self.osd = 'osd' in idiomas
        self.disponivel = True

    def _base(self):
        cmd = [self.exe]
        if self.tessdata:
            cmd += ['--tessdata-dir', self.tessdata]
        return cmd

    def _idiomas(self):
        try:
            r = subprocess.run(self._base() + ['--list-langs'], capture_output=True, timeout=30, **_sem_janela())
        except (OSError, subprocess.SubprocessError):
            return set()
        linhas = (r.stdout + r.stderr).decode('utf-8', 'replace').splitlines()
        return set(l.strip() for l in linhas if re.match(r'^[a-z_A-Z]{3,}(?:_[a-z]+)?$', l.strip()))

    def descricao(self):
        return 'Tesseract %s (%s)' % (self.versao, self.idioma)

    def ler(self, imagem, dpi=None):
        """Texto de uma imagem (bytes de PNG/JPEG/TIFF/BMP/GIF/WebP/JP2)."""
        cmd = self._base() + ['stdin', 'stdout', '-l', self.idioma, '--psm', '1' if self.osd else '3']
        if dpi and 50 <= dpi <= 1200:
            cmd += ['--dpi', str(int(round(dpi)))]
        env = dict(os.environ)
        env['OMP_THREAD_LIMIT'] = '1'
        if self.stdin_ok:
            try:
                r = subprocess.run(cmd, input=imagem, capture_output=True, timeout=TEMPO_MAXIMO, env=env,
                                   **_sem_janela())
                if r.returncode == 0:
                    return r.stdout.decode('utf-8', 'replace')
                erro = r.stderr.decode('utf-8', 'replace')
                if 'stdin' not in erro.lower() and 'read' not in erro.lower():
                    raise RuntimeError(erro.strip()[-300:] or 'código %d' % r.returncode)
                self.stdin_ok = False
            except subprocess.TimeoutExpired:
                raise RuntimeError('tempo esgotado (%d s)' % TEMPO_MAXIMO)
        # versões antigas: arquivo temporário privado, apagado em seguida
        pasta = tempfile.mkdtemp(prefix='sxd_')
        try:
            caminho = os.path.join(pasta, 'img')
            with open(caminho, 'wb') as f:
                f.write(imagem)
            cmd[cmd.index('stdin')] = caminho
            r = subprocess.run(cmd, capture_output=True, timeout=TEMPO_MAXIMO, env=env, **_sem_janela())
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode('utf-8', 'replace').strip()[-300:])
            return r.stdout.decode('utf-8', 'replace')
        finally:
            shutil.rmtree(pasta, ignore_errors=True)


def ler_lote(tarefas, rotulo='imagem'):
    """tarefas: lista de (chave, bytes_imagem, dpi). Retorna {chave: (texto, erro)} em paralelo."""
    m = motor()
    resultado = {}
    if m is None or not tarefas:
        return resultado
    total = len(tarefas)
    feitos = [0]
    trava = threading.Lock()
    avisar = getattr(_contexto, 'progresso', None)    # capturado aqui: as threads do lote não têm o contexto
    trabalhadores = max(1, min(4, (os.cpu_count() or 2) // 2))

    def um(tarefa):
        chave, dados, dpi = tarefa
        try:
            texto, erro = m.ler(dados, dpi), None
        except Exception as e:
            texto, erro = '', str(e)
        with trava:
            feitos[0] += 1
            if avisar:
                try:
                    avisar('OCR: %s %d de %d' % (rotulo, feitos[0], total))
                except Exception:
                    pass
        return chave, texto, erro

    informar('OCR: %s 0 de %d' % (rotulo, total))
    with concurrent.futures.ThreadPoolExecutor(max_workers=trabalhadores) as ex:
        for chave, texto, erro in ex.map(um, tarefas):
            resultado[chave] = (texto, erro)
    return resultado


# ===================================================================== utilidades de imagem

def dimensoes(dados):
    """(largura, altura) lidas do cabeçalho de PNG, JPEG, GIF, BMP, TIFF ou WebP; None se desconhecido."""
    try:
        if dados[:8] == b'\x89PNG\r\n\x1a\n':
            return struct.unpack('>II', dados[16:24])
        if dados[:6] in (b'GIF87a', b'GIF89a'):
            return struct.unpack('<HH', dados[6:10])
        if dados[:2] == b'BM':
            w, h = struct.unpack('<ii', dados[18:26])
            return abs(w), abs(h)
        if dados[:4] == b'RIFF' and dados[8:12] == b'WEBP':
            if dados[12:16] == b'VP8X':
                return (int.from_bytes(dados[24:27], 'little') + 1, int.from_bytes(dados[27:30], 'little') + 1)
            if dados[12:16] == b'VP8 ':
                w, h = struct.unpack('<HH', dados[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if dados[12:16] == b'VP8L':
                b = int.from_bytes(dados[21:25], 'little')
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if dados[:2] == b'\xff\xd8':
            i = 2
            while i + 9 < len(dados):
                if dados[i] != 0xFF:
                    i += 1
                    continue
                marca = dados[i + 1]
                if marca in (0xD8, 0x01) or 0xD0 <= marca <= 0xD7:
                    i += 2
                    continue
                tam = struct.unpack('>H', dados[i + 2:i + 4])[0]
                if 0xC0 <= marca <= 0xCF and marca not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack('>HH', dados[i + 5:i + 9])
                    return w, h
                i += 2 + tam
        if dados[:4] in (b'II*\x00', b'MM\x00*'):
            e = '<' if dados[:2] == b'II' else '>'
            off = struct.unpack(e + 'I', dados[4:8])[0]
            n = struct.unpack(e + 'H', dados[off:off + 2])[0]
            w = h = None
            for k in range(n):
                p = off + 2 + 12 * k
                tag, tipo = struct.unpack(e + 'HH', dados[p:p + 4])
                v = struct.unpack(e + ('H' if tipo == 3 else 'I'), dados[p + 8:p + (10 if tipo == 3 else 12)])[0]
                if tag == 256:
                    w = v
                elif tag == 257:
                    h = v
            if w and h:
                return w, h
    except (struct.error, IndexError, ValueError):
        return None
    return None


def vale_ocr(w, h):
    return bool(w and h and w >= LADO_MINIMO and h >= LADO_MINIMO and w * h >= AREA_MINIMA
                and w * h <= LIMITE_PIXELS)


def _png_bloco(tipo, dados):
    return struct.pack('>I', len(dados)) + tipo + dados + struct.pack('>I', zlib.crc32(tipo + dados) & 0xFFFFFFFF)


def png(largura, altura, bpc, tipo_cor, dados, paleta=None):
    """PNG a partir de linhas já alinhadas em byte (como no PDF). tipo_cor: 0 cinza, 2 RGB, 3 paleta."""
    canais = {0: 1, 2: 3, 3: 1}[tipo_cor]
    por_linha = (largura * canais * bpc + 7) // 8
    esperado = por_linha * altura
    if len(dados) < esperado:
        dados = dados + b'\xff' * (esperado - len(dados))
    linhas = bytearray()
    for y in range(altura):
        linhas.append(0)
        linhas += dados[y * por_linha:(y + 1) * por_linha]
    saida = [b'\x89PNG\r\n\x1a\n',
             _png_bloco(b'IHDR', struct.pack('>IIBBBBB', largura, altura, bpc, tipo_cor, 0, 0, 0))]
    if paleta:
        saida.append(_png_bloco(b'PLTE', paleta))
    saida.append(_png_bloco(b'IDAT', zlib.compress(bytes(linhas), 6)))
    saida.append(_png_bloco(b'IEND', b''))
    return b''.join(saida)


def tiff_ccitt(largura, altura, dados, k, invertido):
    """TIFF de uma faixa com os dados CCITT (G3/G4) do PDF, sem descomprimir."""
    compressao = 4 if k < 0 else 3
    entradas = [(256, 4, largura), (257, 4, altura), (258, 3, 1), (259, 3, compressao),
                (262, 3, 1 if invertido else 0), (273, 4, 0), (277, 3, 1), (278, 4, altura),
                (279, 4, len(dados))]
    if compressao == 3:
        entradas.append((292, 4, 1 if k > 0 else 0))
    entradas.sort()
    n = len(entradas)
    inicio_dados = 8 + 2 + n * 12 + 4
    corpo = struct.pack('<H', n)
    for tag, tipo, valor in entradas:
        if tag == 273:
            valor = inicio_dados
        if tipo == 3:
            corpo += struct.pack('<HHIHH', tag, tipo, 1, valor, 0)
        else:
            corpo += struct.pack('<HHII', tag, tipo, 1, valor)
    corpo += struct.pack('<I', 0)
    return b'II*\x00' + struct.pack('<I', 8) + corpo + dados


def cmyk_para_rgb(dados):
    c, m, y, k = dados[0::4], dados[1::4], dados[2::4], dados[3::4]
    saida = bytearray(len(c) * 3)
    saida[0::3] = bytes(max(0, 255 - ci - ki) for ci, ki in zip(c, k))
    saida[1::3] = bytes(max(0, 255 - mi - ki) for mi, ki in zip(m, k))
    saida[2::3] = bytes(max(0, 255 - yi - ki) for yi, ki in zip(y, k))
    return bytes(saida)


INVERTER = bytes(255 - i for i in range(256))
