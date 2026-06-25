"""Fila de revisão em curses (stdlib) — onde o dono despacha o resíduo.

Lista navegável; cada email mostra a ação sugerida pela cascata, a data e o
assunto. O dono decide com d/m/b/a: ao decidir, o email **sai da lista na hora**
(vai pra uma pilha de decisões) — `u` desfaz a última, devolvendo o email à fila
(e removendo a regra, se block/allow tiver acabado de criá-la). Marcar block/allow
grava a regra na hora (rules/writer): é o loop de aprendizado.

A TUI é offline — só lê a fila e escreve regras. O dispatch (mover pra Trash via
IMAP) acontece depois que ela fecha, no cli, com as decisões confirmadas no enter.
"""

import curses
from email.utils import parsedate_to_datetime
from pathlib import Path

from apolo.actions import DispatchItem
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, parse_sender
from apolo.rules.writer import add_rule_entry, remove_rule_entry

_AJUDA = " ↑↓ navegar   d lixeira   m manter   b block   a allow   u desfazer   enter aplicar   q sair "

_TAG = {ACAO_LIXEIRA: "lixeira", ACAO_MANTER: "manter", ACAO_REVISAR: "revisar"}
# pares de cor por ação (definidos em _init_cores)
_COR = {ACAO_LIXEIRA: 2, ACAO_MANTER: 3, ACAO_REVISAR: 4}


def _fmt_data(raw: str) -> str:
    """Header Date -> '24/06 10:03'. Tolera formato estranho (corta o cru)."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return raw[:16]
    return dt.strftime("%d/%m %H:%M") if dt else raw[:16]


def _addrow(stdscr, y: int, w: int, texto: str, attr=0) -> None:
    """Escreve uma linha inteira sem estourar no canto inferior direito."""
    try:
        stdscr.addnstr(y, 0, texto.ljust(w)[: w - 1], w - 1, attr)
    except curses.error:
        pass


def _init_cores() -> None:
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # título
        curses.init_pair(2, curses.COLOR_RED, -1)      # lixeira
        curses.init_pair(3, curses.COLOR_GREEN, -1)    # manter
        curses.init_pair(4, curses.COLOR_YELLOW, -1)   # revisar
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # feedback
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
        self.data = row["data"] or ""
        self.regra = row["regra_casada"] or ""
        self.acao = row["acao_sugerida"] or ACAO_REVISAR  # ação final (editável)


def review_queue(rows, rules_path: Path) -> list[DispatchItem]:
    """Abre a TUI; devolve os itens a despachar (lixeira/manter). Vazio se 'q'."""
    if not rows:
        return []
    itens = [_Item(r) for r in rows]
    decididos = curses.wrapper(_run, itens, Path(rules_path))
    return [
        DispatchItem(
            pasta=it.pasta,
            uidvalidity=it.uidvalidity,
            uid=it.uid,
            message_id=it.message_id,
            acao=it.acao,
        )
        for it in decididos
        if it.acao in (ACAO_LIXEIRA, ACAO_MANTER)
    ]


def _draw_item(stdscr, y: int, w: int, it: _Item, selecionado: bool) -> None:
    """Duas linhas: [tag] remetente ........ data  /  assunto (dim)."""
    base = curses.A_REVERSE if selecionado else curses.A_NORMAL
    marca = "▶" if selecionado else " "
    tag = _TAG.get(it.acao, str(it.acao))[:7]
    data = _fmt_data(it.data)

    linha = f"{marca} {tag:<7}  {it.remetente}"
    if data:
        larg = max(0, w - 1 - len(data) - 1)
        linha = linha[:larg].ljust(larg) + " " + data
    _addrow(stdscr, y, w, linha, base)

    # tag colorida por cima (col. 2)
    cor = curses.color_pair(_COR.get(it.acao, 0)) | curses.A_BOLD
    if selecionado:
        cor |= curses.A_REVERSE
    try:
        stdscr.addnstr(y, 2, f"{tag:<7}", 7, cor)
    except curses.error:
        pass

    _addrow(stdscr, y + 1, w, f"     {it.assunto}", base | curses.A_DIM)


def _run(stdscr, itens: list[_Item], rules_path: Path) -> list[_Item]:
    curses.curs_set(0)
    _init_cores()
    pend = list(itens)              # fila visível
    hist: list[tuple] = []         # (item, idx, acao_anterior, rule_undo)
    sel = 0
    top = 0
    msg = ""
    q_armado = False

    def decidir(acao: str, rule_undo=None) -> None:
        nonlocal sel
        it = pend[sel]
        hist.append((it, sel, it.acao, rule_undo))
        it.acao = acao
        del pend[sel]
        if sel >= len(pend):
            sel = max(0, len(pend) - 1)

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        n_lix = sum(1 for it, *_ in hist if it.acao == ACAO_LIXEIRA)
        n_man = sum(1 for it, *_ in hist if it.acao == ACAO_MANTER)

        _addrow(stdscr, 0, w, " apolo · fila de revisão",
                curses.color_pair(1) | curses.A_BOLD)
        resumo = f" {len(pend)} p/ revisar    decididos: {n_lix} lixeira · {n_man} manter"
        if hist:
            resumo += f"    ({len(hist)} ação(ões) · u desfaz)"
        _addrow(stdscr, 1, w, resumo, curses.A_DIM)

        visiveis = max(1, (h - 4) // 2)
        if sel < top:
            top = sel
        elif sel >= top + visiveis:
            top = sel - visiveis + 1

        if not pend:
            _addrow(stdscr, 3, w, "  ✓ fila vazia — enter aplica as decisões · q sai",
                    curses.color_pair(3) | curses.A_BOLD)
        else:
            y = 2
            for idx in range(top, min(len(pend), top + visiveis)):
                _draw_item(stdscr, y, w, pend[idx], idx == sel)
                y += 2

        if msg:
            _addrow(stdscr, h - 2, w, " " + msg, curses.color_pair(5) | curses.A_BOLD)
        _addrow(stdscr, h - 1, w, _AJUDA, curses.A_REVERSE)
        stdscr.refresh()

        try:
            tecla = stdscr.getch()
        except KeyboardInterrupt:
            return []

        msg = ""

        if tecla in (curses.KEY_DOWN, ord("j")):
            if pend:
                sel = min(len(pend) - 1, sel + 1)
        elif tecla in (curses.KEY_UP, ord("k")):
            if pend:
                sel = max(0, sel - 1)
        elif tecla == ord("d") and pend:
            r = pend[sel].remetente
            decidir(ACAO_LIXEIRA)
            msg = f"→ lixeira: {r}"
        elif tecla == ord("m") and pend:
            r = pend[sel].remetente
            decidir(ACAO_MANTER)
            msg = f"→ manter: {r}"
        elif tecla == ord("b") and pend:
            msg = _aprender(pend[sel], rules_path, "blocklist", ACAO_LIXEIRA, decidir)
        elif tecla == ord("a") and pend:
            msg = _aprender(pend[sel], rules_path, "allowlist", ACAO_MANTER, decidir)
        elif tecla == ord("u"):
            if not hist:
                msg = "nada a desfazer"
            else:
                it, idx, anterior, rule_undo = hist.pop()
                if rule_undo:
                    try:
                        remove_rule_entry(rules_path, lista=rule_undo[0],
                                          tipo=rule_undo[1], valor=rule_undo[2])
                    except Exception as e:  # não derruba a TUI
                        msg = f"(regra não removida: {e}) "
                it.acao = anterior
                idx = min(idx, len(pend))
                pend.insert(idx, it)
                sel = idx
                msg += f"↩ desfeito: {it.remetente}"
        elif tecla in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            return [it for it, *_ in hist]
        elif tecla == ord("q"):
            if hist and not q_armado:
                q_armado = True
                msg = f"{len(hist)} decisão(ões) não aplicada(s) — enter aplica · q de novo descarta"
                continue
            return []

        q_armado = False


def _aprender(item: _Item, rules_path: Path, lista: str, acao: str, decidir) -> str:
    """Grava a regra pro domínio do remetente, decide o item e registra o undo."""
    _, dominio = parse_sender(item.remetente)
    if not dominio:
        return "sem domínio no remetente — regra não criada"
    try:
        status = add_rule_entry(rules_path, lista=lista, tipo="dominio", valor=dominio)
    except Exception as e:  # não derruba a TUI por erro de escrita
        return f"erro ao gravar regra: {e}"
    rule_undo = (lista, "dominio", dominio) if status == "added" else None
    decidir(acao, rule_undo)
    verbo = "já existia" if status == "exists" else "criada"
    return f"{lista}: {dominio} {verbo} → {_TAG[acao]}"
