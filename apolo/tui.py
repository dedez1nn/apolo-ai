"""Fila de revisão em curses (stdlib) — onde o dono despacha o resíduo.

Lista navegável; cada email carrega a ação sugerida pela cascata, que o dono
confirma ou troca. Aqui mora o loop de aprendizado: marcar block/allow grava a
regra na hora (rules/writer) e ajusta a ação do item.

A TUI é offline — só lê a fila e escreve regras. O dispatch (mover pra Trash via
IMAP) acontece depois que ela fecha, no cli. Assim o curses não fala com a rede.
"""

import curses
from pathlib import Path

from apolo.actions import DispatchItem
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, parse_sender
from apolo.rules.writer import add_rule_entry

_AJUDA = "↑/↓ mover   d lixeira   m manter   b block   a allow   enter despachar   q sair"


def _addrow(stdscr, y: int, w: int, texto: str, attr=0) -> None:
    """Escreve uma linha inteira sem estourar no canto inferior direito.

    addnstr no último caractere da tela levanta _curses.error; por isso cortamos
    em w-1 e engolimos o erro residual.
    """
    try:
        stdscr.addnstr(y, 0, texto.ljust(w)[: w - 1], w - 1, attr)
    except curses.error:
        pass


class _Item:
    def __init__(self, row):
        self.pasta = row["pasta"]
        self.uidvalidity = row["uidvalidity"]
        self.uid = row["uid"]
        self.message_id = row["message_id"]
        self.remetente = row["remetente"] or ""
        self.assunto = row["assunto"] or ""
        self.regra = row["regra_casada"] or ""
        self.acao = row["acao_sugerida"] or ACAO_REVISAR  # ação final (editável)


def review_queue(rows, rules_path: Path) -> list[DispatchItem]:
    """Abre a TUI; devolve os itens a despachar (lixeira/manter). Vazio se 'q'."""
    if not rows:
        return []
    itens = [_Item(r) for r in rows]
    despachar = curses.wrapper(_run, itens, Path(rules_path))
    if not despachar:
        return []
    return [
        DispatchItem(
            pasta=it.pasta,
            uidvalidity=it.uidvalidity,
            uid=it.uid,
            message_id=it.message_id,
            acao=it.acao,
        )
        for it in itens
        if it.acao in (ACAO_LIXEIRA, ACAO_MANTER)
    ]


def _run(stdscr, itens: list[_Item], rules_path: Path) -> bool:
    curses.curs_set(0)
    sel = 0
    top = 0
    msg = ""

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        n_lixeira = sum(1 for it in itens if it.acao == ACAO_LIXEIRA)
        n_manter = sum(1 for it in itens if it.acao == ACAO_MANTER)
        n_revisar = sum(1 for it in itens if it.acao == ACAO_REVISAR)

        cabecalho = (
            f" fila de revisão — {len(itens)} email(s)   "
            f"[lixeira {n_lixeira} · manter {n_manter} · revisar {n_revisar}]"
        )
        _addrow(stdscr, 0, w, cabecalho, curses.A_BOLD)

        # Cada email ocupa 2 linhas; reserva 1 (cabeçalho) + 3 (rodapé).
        corpo_linhas = max(1, h - 4)
        por_item = 2
        visiveis = max(1, corpo_linhas // por_item)

        if sel < top:
            top = sel
        elif sel >= top + visiveis:
            top = sel - visiveis + 1

        linha = 1
        for idx in range(top, min(len(itens), top + visiveis)):
            it = itens[idx]
            marca = ">" if idx == sel else " "
            attr = curses.A_REVERSE if idx == sel else curses.A_NORMAL
            cab = f"{marca} [{it.acao:<7}] {it.remetente}"
            sub = f"      {it.assunto}"
            _addrow(stdscr, linha, w, cab, attr)
            _addrow(stdscr, linha + 1, w, sub, attr | curses.A_DIM)
            linha += 2

        if msg:
            _addrow(stdscr, h - 2, w, msg, curses.A_BOLD)
        _addrow(stdscr, h - 1, w, _AJUDA, curses.A_REVERSE)
        stdscr.refresh()

        try:
            tecla = stdscr.getch()
        except KeyboardInterrupt:
            return False

        msg = ""
        atual = itens[sel]

        if tecla in (curses.KEY_DOWN, ord("j")):
            sel = min(len(itens) - 1, sel + 1)
        elif tecla in (curses.KEY_UP, ord("k")):
            sel = max(0, sel - 1)
        elif tecla == ord("d"):
            atual.acao = ACAO_LIXEIRA
        elif tecla == ord("m"):
            atual.acao = ACAO_MANTER
        elif tecla == ord("b"):
            msg = _aprender(atual, rules_path, lista="blocklist", acao=ACAO_LIXEIRA)
        elif tecla == ord("a"):
            msg = _aprender(atual, rules_path, lista="allowlist", acao=ACAO_MANTER)
        elif tecla in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            return True
        elif tecla == ord("q"):
            return False


def _aprender(item: _Item, rules_path: Path, *, lista: str, acao: str) -> str:
    """Loop de aprendizado: grava a regra pro domínio do remetente e ajusta a ação."""
    _, dominio = parse_sender(item.remetente)
    if not dominio:
        return "sem domínio no remetente — regra não criada"
    try:
        status = add_rule_entry(rules_path, lista=lista, tipo="dominio", valor=dominio)
    except Exception as e:  # não derruba a TUI por erro de escrita
        return f"erro ao gravar regra: {e}"
    item.acao = acao
    verbo = "já existia" if status == "exists" else "adicionado"
    return f"{lista}: domínio {dominio} {verbo} → {acao}"
