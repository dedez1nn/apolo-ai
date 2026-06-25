"""Interface gráfica do Apolo em Textual.

Importada de forma *lazy* (só quando a UI é aberta), pra que `apolo run` — o
caminho do timer — continue 100% stdlib e sem depender do Textual. O núcleo
segue zero-dep; só esta camada usa a biblioteca externa.
"""

from apolo.ui.app import run_ui

__all__ = ["run_ui"]
