# -*- coding: utf-8 -*-
"""Ponto de entrada do SisExtractDados_Dintel (Python 3.8+, só biblioteca padrão)."""
import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write('Python 3.8 ou superior é necessário (encontrado %s).\n' % sys.version.split()[0])
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from extdados.cli import principal  # noqa: E402

if __name__ == '__main__':
    sys.exit(principal())
