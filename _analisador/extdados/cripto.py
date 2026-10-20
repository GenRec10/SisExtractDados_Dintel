# -*- coding: utf-8 -*-
"""RC4, AES (128/256, CBC) e o manipulador de segurança padrão do PDF.

Muitos PDFs de bancos e tribunais vêm "protegidos" apenas contra impressão ou
cópia: são criptografados, mas com senha de abertura VAZIA. Este módulo permite
lê-los sem nenhuma dependência externa. PDFs que exigem senha para ABRIR não
são lidos (e a ferramenta avisa).

AES implementado com tabelas T (FIPS-197), conferido contra os vetores de teste
oficiais nos testes automatizados.
"""
import hashlib
import struct

# ===================================================================== RC4


def rc4(chave, dados):
    S = list(range(256))
    j = 0
    n = len(chave)
    for i in range(256):
        j = (j + S[i] + chave[i % n]) & 0xFF
        S[i], S[j] = S[j], S[i]
    i = j = 0
    saida = bytearray(len(dados))
    for k, b in enumerate(dados):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        saida[k] = b ^ S[(S[i] + S[j]) & 0xFF]
    return bytes(saida)


# ===================================================================== AES

def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _gerar():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (((q << 1) | (q >> 7)) & 0xFF) ^ (((q << 2) | (q >> 6)) & 0xFF) \
            ^ (((q << 3) | (q >> 5)) & 0xFF) ^ (((q << 4) | (q >> 4)) & 0xFF)
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    te0, td0 = [], []
    for x in range(256):
        s = sbox[x]
        te0.append((_mul(s, 2) << 24) | (s << 16) | (s << 8) | _mul(s, 3))
        si = inv[x]
        td0.append((_mul(si, 14) << 24) | (_mul(si, 9) << 16) | (_mul(si, 13) << 8) | _mul(si, 11))

    def ror(t, n):
        return [((v >> n) | (v << (32 - n))) & 0xFFFFFFFF for v in t]
    return sbox, inv, te0, ror(te0, 8), ror(te0, 16), ror(te0, 24), td0, ror(td0, 8), ror(td0, 16), ror(td0, 24)


SBOX, INV, TE0, TE1, TE2, TE3, TD0, TD1, TD2, TD3 = _gerar()


def _sub(w):
    return (SBOX[w >> 24] << 24) | (SBOX[(w >> 16) & 255] << 16) | (SBOX[(w >> 8) & 255] << 8) | SBOX[w & 255]


class AES(object):
    def __init__(self, chave):
        if len(chave) not in (16, 24, 32):
            raise ValueError('tamanho de chave AES inválido')
        nk = len(chave) // 4
        nr = nk + 6
        w = list(struct.unpack('>%dI' % nk, chave))
        rcon = 1
        for i in range(nk, 4 * (nr + 1)):
            t = w[i - 1]
            if i % nk == 0:
                t = _sub(((t << 8) & 0xFFFFFFFF) | (t >> 24)) ^ (rcon << 24)
                rcon = _xtime(rcon)
            elif nk > 6 and i % nk == 4:
                t = _sub(t)
            w.append(w[i - nk] ^ t)
        self.nr = nr
        self.ek = w
        dk = []
        for r in range(nr, -1, -1):
            rk = w[4 * r:4 * r + 4]
            if 0 < r < nr:
                rk = [TD0[SBOX[x >> 24]] ^ TD1[SBOX[(x >> 16) & 255]] ^ TD2[SBOX[(x >> 8) & 255]]
                      ^ TD3[SBOX[x & 255]] for x in rk]
            dk.extend(rk)
        self.dk = dk

    def cifrar_bloco(self, s0, s1, s2, s3):
        w = self.ek
        s0 ^= w[0]
        s1 ^= w[1]
        s2 ^= w[2]
        s3 ^= w[3]
        k = 4
        for _ in range(1, self.nr):
            t0 = TE0[s0 >> 24] ^ TE1[(s1 >> 16) & 255] ^ TE2[(s2 >> 8) & 255] ^ TE3[s3 & 255] ^ w[k]
            t1 = TE0[s1 >> 24] ^ TE1[(s2 >> 16) & 255] ^ TE2[(s3 >> 8) & 255] ^ TE3[s0 & 255] ^ w[k + 1]
            t2 = TE0[s2 >> 24] ^ TE1[(s3 >> 16) & 255] ^ TE2[(s0 >> 8) & 255] ^ TE3[s1 & 255] ^ w[k + 2]
            t3 = TE0[s3 >> 24] ^ TE1[(s0 >> 16) & 255] ^ TE2[(s1 >> 8) & 255] ^ TE3[s2 & 255] ^ w[k + 3]
            s0, s1, s2, s3 = t0, t1, t2, t3
            k += 4
        S = SBOX
        return ((S[s0 >> 24] << 24 | S[(s1 >> 16) & 255] << 16 | S[(s2 >> 8) & 255] << 8 | S[s3 & 255]) ^ w[k],
                (S[s1 >> 24] << 24 | S[(s2 >> 16) & 255] << 16 | S[(s3 >> 8) & 255] << 8 | S[s0 & 255]) ^ w[k + 1],
                (S[s2 >> 24] << 24 | S[(s3 >> 16) & 255] << 16 | S[(s0 >> 8) & 255] << 8 | S[s1 & 255]) ^ w[k + 2],
                (S[s3 >> 24] << 24 | S[(s0 >> 16) & 255] << 16 | S[(s1 >> 8) & 255] << 8 | S[s2 & 255]) ^ w[k + 3])

    def decifrar_bloco(self, s0, s1, s2, s3):
        w = self.dk
        s0 ^= w[0]
        s1 ^= w[1]
        s2 ^= w[2]
        s3 ^= w[3]
        k = 4
        for _ in range(1, self.nr):
            t0 = TD0[s0 >> 24] ^ TD1[(s3 >> 16) & 255] ^ TD2[(s2 >> 8) & 255] ^ TD3[s1 & 255] ^ w[k]
            t1 = TD0[s1 >> 24] ^ TD1[(s0 >> 16) & 255] ^ TD2[(s3 >> 8) & 255] ^ TD3[s2 & 255] ^ w[k + 1]
            t2 = TD0[s2 >> 24] ^ TD1[(s1 >> 16) & 255] ^ TD2[(s0 >> 8) & 255] ^ TD3[s3 & 255] ^ w[k + 2]
            t3 = TD0[s3 >> 24] ^ TD1[(s2 >> 16) & 255] ^ TD2[(s1 >> 8) & 255] ^ TD3[s0 & 255] ^ w[k + 3]
            s0, s1, s2, s3 = t0, t1, t2, t3
            k += 4
        I = INV
        return ((I[s0 >> 24] << 24 | I[(s3 >> 16) & 255] << 16 | I[(s2 >> 8) & 255] << 8 | I[s1 & 255]) ^ w[k],
                (I[s1 >> 24] << 24 | I[(s0 >> 16) & 255] << 16 | I[(s3 >> 8) & 255] << 8 | I[s2 & 255]) ^ w[k + 1],
                (I[s2 >> 24] << 24 | I[(s1 >> 16) & 255] << 16 | I[(s0 >> 8) & 255] << 8 | I[s3 & 255]) ^ w[k + 2],
                (I[s3 >> 24] << 24 | I[(s2 >> 16) & 255] << 16 | I[(s1 >> 8) & 255] << 8 | I[s0 & 255]) ^ w[k + 3])

    def cifrar_cbc(self, iv, dados):
        if len(dados) % 16:
            raise ValueError('dados não múltiplos de 16')
        p = struct.unpack('>4I', iv)
        n = len(dados) // 16
        entrada = struct.unpack('>%dI' % (4 * n), dados)
        saida = []
        for b in range(n):
            o = b * 4
            p = self.cifrar_bloco(entrada[o] ^ p[0], entrada[o + 1] ^ p[1], entrada[o + 2] ^ p[2],
                                  entrada[o + 3] ^ p[3])
            saida.extend(p)
        return struct.pack('>%dI' % (4 * n), *saida)

    def decifrar_cbc(self, iv, dados):
        n = len(dados) // 16
        if n == 0:
            return b''
        dados = dados[:n * 16]
        c = struct.unpack('>%dI' % (4 * n), dados)
        p = struct.unpack('>4I', iv)
        saida = []
        for b in range(n):
            o = b * 4
            d = self.decifrar_bloco(c[o], c[o + 1], c[o + 2], c[o + 3])
            saida.extend((d[0] ^ p[0], d[1] ^ p[1], d[2] ^ p[2], d[3] ^ p[3]))
            p = c[o:o + 4]
        return struct.pack('>%dI' % (4 * n), *saida)


def _sem_padding(dados):
    if dados and 1 <= dados[-1] <= 16 and dados.endswith(bytes([dados[-1]]) * dados[-1]):
        return dados[:-dados[-1]]
    return dados


# ===================================================================== PDF: manipulador padrão

PAD = bytes.fromhex('28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A')


class SenhaNecessaria(Exception):
    pass


class FiltroNaoSuportado(Exception):
    pass


def _hash_r6(senha, sal, udata):
    """Algoritmo 2.B (ISO 32000-2) para R6."""
    K = hashlib.sha256(senha + sal + udata).digest()
    i = 0
    while True:
        K1 = (senha + K + udata) * 64
        E = AES(K[:16]).cifrar_cbc(K[16:32], K1)
        resto = sum(E[:16]) % 3
        K = (hashlib.sha256, hashlib.sha384, hashlib.sha512)[resto](E).digest()
        i += 1
        if i >= 64 and E[-1] <= i - 32:
            break
    return K[:32]


class Decifrador(object):
    """Decifra strings e streams de um PDF com senha de abertura vazia."""

    def __init__(self, enc, id0):
        def b(v):
            return v if isinstance(v, bytes) else (v or b'')
        filtro = enc.get('Filter')
        if filtro != 'Standard':
            raise FiltroNaoSuportado('manipulador de segurança "%s" não suportado' % filtro)
        self.V = int(enc.get('V', 0) or 0)
        self.R = int(enc.get('R', 0) or 0)
        O, U = b(enc.get('O')), b(enc.get('U'))
        P = int(enc.get('P', 0) or 0)
        self.metodo_stm = self.metodo_str = 'RC4'
        n = 5
        if self.V >= 2:
            n = int(enc.get('Length', 40) or 40) // 8
        if self.V >= 4:
            cf = enc.get('CF') or {}
            def metodo(nome):
                if nome in (None, 'Identity'):
                    return 'NADA' if nome == 'Identity' else 'RC4'
                f = cf.get(nome) or {}
                cfm = f.get('CFM', 'None')
                if 'Length' in f and cfm == 'V2':
                    pass
                return {'V2': 'RC4', 'AESV2': 'AES128', 'AESV3': 'AES256', 'None': 'NADA'}.get(cfm, 'NADA')
            self.metodo_stm = metodo(enc.get('StmF'))
            self.metodo_str = metodo(enc.get('StrF'))
            if self.V == 4:
                f = cf.get(enc.get('StmF')) or {}
                comp = f.get('Length')
                if comp and int(comp) in (5, 16):
                    n = int(comp)
                elif comp and int(comp) > 16:
                    n = int(comp) // 8
                else:
                    n = 16
        self.encrypt_metadata = enc.get('EncryptMetadata', True) is not False
        if self.R >= 5:
            UE = b(enc.get('UE'))
            senha = b''
            vsal, ksal = U[32:40], U[40:48]
            if self.R == 5:
                ok = hashlib.sha256(senha + vsal).digest() == U[:32]
                inter = hashlib.sha256(senha + ksal).digest()
            else:
                ok = _hash_r6(senha, vsal, b'') == U[:32]
                inter = _hash_r6(senha, ksal, b'') if ok else b''
            if not ok:
                raise SenhaNecessaria()
            self.chave = AES(inter).decifrar_cbc(b'\x00' * 16, UE[:32])
            return
        h = hashlib.md5(PAD + O[:32] + struct.pack('<i', P if P < 2 ** 31 else P - 2 ** 32) + id0)
        if self.R >= 4 and not self.encrypt_metadata:
            h.update(b'\xff\xff\xff\xff')
        chave = h.digest()[:n]
        if self.R >= 3:
            for _ in range(50):
                chave = hashlib.md5(chave[:n]).digest()[:n]
        self.chave = chave
        if self.R == 2:
            ok = rc4(chave, PAD) == U[:32]
        else:
            x = rc4(chave, hashlib.md5(PAD + id0).digest())
            for i in range(1, 20):
                x = rc4(bytes(c ^ i for c in chave), x)
            ok = x == U[:16]
        if not ok:
            raise SenhaNecessaria()

    def _chave_obj(self, num, gen, aes):
        if self.R >= 5:
            return self.chave
        extra = b'sAlT' if aes else b''
        h = hashlib.md5(self.chave + struct.pack('<I', num)[:3] + struct.pack('<I', gen)[:2] + extra).digest()
        return h[:min(len(self.chave) + 5, 16)]

    def decifrar(self, dados, num, gen, eh_stream):
        metodo = self.metodo_stm if eh_stream else self.metodo_str
        if metodo == 'NADA' or not dados:
            return dados
        if metodo == 'RC4':
            return rc4(self._chave_obj(num, gen, False), dados)
        chave = self._chave_obj(num, gen, True)
        if len(dados) < 32:
            if len(dados) < 16:
                return b''
        iv, corpo = dados[:16], dados[16:]
        return _sem_padding(AES(chave).decifrar_cbc(iv, corpo))
