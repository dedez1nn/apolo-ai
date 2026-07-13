"""Apolo — triador pessoal de emails.

Roda em lote (systemd timer + oneshot), reduz ruído de forma determinística
e enfileira o resíduo pra revisão manual. Veja apolo.md pra arquitetura.
"""

from apolo.logging_setup import setup_logging

__version__ = "0.1.0"

setup_logging()
