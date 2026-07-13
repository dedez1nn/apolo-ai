"""Hub — a tela inicial que abre no clique da Waybar.

Layout mestre-detalhe: menu navegável (↑↓ + Enter) à esquerda; à direita, um
painel de prévia ao vivo que muda conforme o cursor — mostra os primeiros emails
da fila, as regras, os contadores etc. sem precisar entrar na sub-tela.
"""

from __future__ import annotations

import contextlib
import io
from datetime import datetime

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, ListItem, ListView, Static

from apolo.ui.model import ACAO_COR, ACAO_ICONE, Item, fmt_remetente, fmt_run
from apolo.ui.theme import (
    AZURE_BRT,
    COR_LIXEIRA,
    COR_MANTER,
    COR_REVISAR,
    DIAMOND,
    GUTTER,
    INK_DIM,
    INK_FAINT,
    keybar,
    mesc,
)

# (id, glyph, rótulo) — ordem do menu.
_MENU = [
    ("review",  "✉", "Revisar fila"),
    ("swipe",   "♥", "Revisar no modo swipe (joguinho)"),
    ("add_rule", "+", "Adicionar regra"),
    ("preview", "◎", "Prévia — o que as regras pegariam"),
    ("sugestoes", "✦", "Sugestões (baseado no seu histórico)"),
    ("rules",   "▤", "Regras configuradas"),
    ("run",     "▶", "Rodar agora (uma passada)"),
    ("retry_ia", "↻", "Reclassificar pendentes (IA)"),
    ("gmail",   "G", "Configurar Gmail"),
    ("imap",    "O", "Configurar Outlook/IMAP"),
    ("config",  "⚙", "Configurações"),
    ("status",  "▦", "Status & contadores"),
]


class MenuItem(ListItem):
    def __init__(self, key: str, icone: str, rotulo: str, badge: str):
        super().__init__(classes="menu-item")
        self.key_id = key
        self._icone = icone
        self._rotulo = rotulo
        self._badge = badge

    def compose(self) -> ComposeResult:
        yield Label(self._icone, classes="mi-icone")
        yield Label(self._rotulo, classes="mi-rotulo")
        yield Label(self._badge, classes="mi-badge")


class HubScreen(Screen):
    BINDINGS = [
        Binding("q,escape", "sair", "sair"),
        Binding("enter", "abrir", "abrir", show=True),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
        Binding("i", "toggle_ia", "IA liga/desliga", show=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="hub-root"):
            with Horizontal(id="masthead"):
                yield Static(self._brand(), id="mast-brand")
                yield Static(self._stats(), id="mast-stats")
            with Horizontal(id="hub-body"):
                yield ListView(*self._itens(), id="hub-menu")
                with VerticalScroll(id="hub-detail"):
                    yield Static(id="hub-detail-body", markup=True)
        yield Static(
            keybar([("↑↓", "Navegar"), ("↵", "Abrir"), ("I", "IA on/off"), ("Q", "Sair")]),
            classes="keybar",
        )

    # ----- montagem -----
    def on_mount(self) -> None:
        self.query_one("#hub-menu", ListView).focus()
        self._render_detalhe("review")
        self.set_interval(1.0, self._tick_relogio)

    def _itens(self) -> list[MenuItem]:
        n_fila = len(self.app.queue)
        n_regras = self.app.stats.rules_count
        cfg = self.app.config
        ia_badge = ""
        if cfg is not None:
            ia_badge = f"[{COR_MANTER}]IA[/]" if cfg.ai_enabled else f"[{INK_FAINT}]IA off[/]"
        badges = {
            "review": str(n_fila) if n_fila else "",
            "swipe": str(n_fila) if n_fila else "",
            "rules": str(n_regras) if n_regras else "",
            "config": ia_badge,
        }
        return [MenuItem(key, icone, rotulo, badges.get(key, "")) for key, icone, rotulo in _MENU]

    # ----- masthead -----
    def _brand(self) -> str:
        return f"{DIAMOND} APOLO   [{INK_FAINT}]{'·'}[/]   [{INK_DIM}]triador de emails[/]"

    def _stats(self) -> str:
        # Todo texto vai dentro de um span: o render do Textual descarta um espaço
        # que apareça logo após um [/] seguido de texto puro — com spans, os
        # espaços ficam antes das tags de abertura e são preservados.
        n = len(self.app.queue)
        fila = (
            f"[{AZURE_BRT} b]{n}[/] [{INK_DIM}]na fila[/]" if n else f"[{INK_FAINT}]fila vazia[/]"
        )
        ultima = fmt_run(self.app.stats.last_run)
        agora = datetime.now().strftime("%H:%M")
        sep = f"   [{INK_FAINT}]·[/]   "
        partes = [fila, f"[{INK_DIM}]última {ultima}[/]", f"[{AZURE_BRT}]{agora}[/]"]
        invalidas = getattr(self.app, "contas_invalidas", {})
        if invalidas:
            nomes = ", ".join(sorted(c.removeprefix("gmail:") for c in invalidas))
            partes.append(f"[{COR_LIXEIRA} b]⚠ reautorizar: {mesc(nomes)}[/]")
        return sep.join(partes)

    def _tick_relogio(self) -> None:
        self.query_one("#mast-stats", Static).update(self._stats())

    # ----- detalhe (painel direito) -----
    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, MenuItem):
            self._render_detalhe(event.item.key_id)

    def _render_detalhe(self, key: str) -> None:
        try:
            body = self.query_one("#hub-detail-body", Static)
        except Exception:
            return
        body.update(getattr(self, f"_det_{key}", self._det_vazio)())

    def _titulo_det(self, texto: str) -> str:
        return f"[{AZURE_BRT} b]{texto}[/]\n[{INK_FAINT}]{'─' * 40}[/]\n"

    def _linhas_emails(self, itens: list, limite: int = 14) -> list[str]:
        linhas = []
        for it in itens[:limite]:
            cor = ACAO_COR.get(it.acao, AZURE_BRT)
            rem = mesc(fmt_remetente(it.remetente)[:40])
            assunto = mesc((it.assunto or "")[:46])
            linhas.append(f"[{cor}]{GUTTER}[/] {rem}")
            if assunto:
                linhas.append(f"  [{INK_FAINT}]{assunto}[/]")
        if len(itens) > limite:
            linhas.append(f"\n[{INK_FAINT}]… e mais {len(itens) - limite}[/]")
        return linhas

    def _det_review(self) -> str:
        fila = self.app.queue
        cab = self._titulo_det("Revisar fila")
        if not fila:
            return (
                cab
                + f"\n[{INK_DIM}]Fila vazia — nada para revisar agora.\n\n"
                f"Enter entra na listagem; lá dentro, S sincroniza\n"
                f"(busca tudo) sem travar a tela — os emails novos já\n"
                f"aparecem ao vivo, mesmo antes do Ollama responder.[/]"
            )
        rodape = f"\n[{INK_DIM}]{len(fila)} email(s) aguardando · Enter para revisar · S sincroniza (ao vivo)[/]"
        return cab + "\n".join(self._linhas_emails(fila)) + rodape

    def _det_swipe(self) -> str:
        fila = self.app.queue
        cab = self._titulo_det("Modo swipe")
        if not fila:
            return (
                cab
                + f"\n[{INK_DIM}]Fila vazia — nada para revisar agora.[/]"
            )
        rodape = (
            f"\n[{INK_DIM}]{len(fila)} email(s) aguardando · Enter para começar[/]\n\n"
            f"[{INK_FAINT}]↑ manter · ↓ lixeira · ← bloquear · → permitir\n"
            f"cada seta mostra o carimbo e desliza pra fora — igual\n"
            f"D/M/B/A da listagem normal, só que em forma de jogo.[/]"
        )
        return cab + rodape

    def _det_add_rule(self) -> str:
        n = self.app.stats.rules_count
        return (
            self._titulo_det("Adicionar regra")
            + f"\n[{INK_DIM}]Cria uma entrada na allowlist (manter) ou blocklist\n"
            f"(lixeira) a partir de um domínio ou remetente.\n\n"
            f"A prévia ao vivo mostra o que casaria na fila antes\n"
            f"de salvar.[/]\n\n[{AZURE_BRT} b]{n}[/] [{INK_DIM}]regra(s) configurada(s)[/]"
        )

    def _det_preview(self) -> str:
        from collections import Counter

        from apolo.rules.engine import RuleEngine

        cab = self._titulo_det("Prévia da cascata")
        if not self.app.queue:
            return cab + f"\n[{INK_DIM}]Fila vazia — nada para simular.[/]"
        try:
            engine = RuleEngine.from_file(self.app.rules_path)
            cont: Counter = Counter()
            for it in self.app.queue:
                dec = engine.classify(remetente=it.remetente, assunto=it.assunto, list_unsubscribe="")
                cont[dec.acao_sugerida] += 1
        except Exception as exc:
            return cab + f"\n[{COR_LIXEIRA}]Erro ao simular: {exc}[/]"
        linhas = [
            f"\n[{COR_LIXEIRA}]{GUTTER}[/] lixeira   [b]{cont.get('lixeira', 0)}[/]",
            f"[{COR_MANTER}]{GUTTER}[/] manter    [b]{cont.get('manter', 0)}[/]",
            f"[{COR_REVISAR}]{GUTTER}[/] revisar   [b]{cont.get('revisar', 0)}[/]",
            f"\n[{INK_DIM}]Simulação offline · Enter para o detalhe por regra[/]",
        ]
        return cab + "\n".join(linhas)

    def _det_sugestoes(self) -> str:
        return (
            self._titulo_det("Sugestões")
            + f"\n[{INK_DIM}]Olha o que você já decidiu no passado (lixeira/manter\n"
            f"aplicados de verdade) e propõe promover um padrão\n"
            f"consistente — mesmo domínio, ou domínio com o mesmo\n"
            f"assunto se repetindo — a regra permanente.\n\n"
            f"Nada é aplicado sozinho: cada dica tem um interruptor,\n"
            f"e só vira regra quando você sai da tela.[/]"
        )

    def _det_rules(self) -> str:
        from apolo.rules.writer import list_entries

        cab = self._titulo_det("Regras configuradas")
        try:
            entries = list_entries(self.app.rules_path)
        except Exception as exc:
            return cab + f"\n[{COR_LIXEIRA}]Erro ao ler regras: {exc}[/]"
        if not entries:
            return cab + f"\n[{INK_DIM}]Nenhuma regra ainda — Enter para criar a primeira.[/]"
        linhas = []
        for lista, tipo, valor in entries[:16]:
            cor = COR_MANTER if lista == "allowlist" else COR_LIXEIRA
            linhas.append(f"[{cor}]{GUTTER}[/] [{INK_FAINT}]{tipo:<9}[/] {valor[:42]}")
        if len(entries) > 16:
            linhas.append(f"\n[{INK_FAINT}]… e mais {len(entries) - 16}[/]")
        return cab + "\n".join(linhas)

    def _det_run(self) -> str:
        return (
            self._titulo_det("Rodar agora")
            + f"\n[{INK_DIM}]Busca e classifica os emails uma vez, na hora,\n"
            f"sem abrir nenhuma outra tela.[/]\n\n"
            f"[{INK_DIM}]Última passada:[/] [b]{fmt_run(self.app.stats.last_run)}[/]"
        )

    def _det_retry_ia(self) -> str:
        return (
            self._titulo_det("Reclassificar pendentes (IA)")
            + f"\n[{INK_DIM}]Reenvia pro Ollama os pendentes que a cascata\n"
            f"deixou em 'default' mas que nunca chegaram a passar\n"
            f"pela IA — normalmente porque o Ollama estava fora do\n"
            f"ar na hora em que o email chegou.\n\n"
            f"Não busca emails novos, só tenta de novo os presos.[/]"
        )

    def _det_gmail(self) -> str:
        cab = self._titulo_det("Configurar Gmail")
        cfg = self.app.config
        tem_creds = cfg and cfg.gmail_client_id and cfg.gmail_client_secret
        if not tem_creds:
            return cab + (
                f"\n[{INK_DIM}]Defina no .env:[/]\n"
                f"  APOLO_GMAIL_CLIENT_ID=…\n"
                f"  APOLO_GMAIL_CLIENT_SECRET=…\n\n"
                f"[{INK_DIM}]Depois abra este item para autorizar via browser.[/]"
            )
        from apolo.config import load_accounts
        contas = [a for a in load_accounts(cfg.accounts_path) if a.provider == "gmail"]
        linhas = [f"[{INK_DIM}]Credenciais OAuth2 configuradas no .env.[/]\n"]
        if contas:
            linhas.append(f"[{INK_DIM}]Contas autorizadas:[/]")
            for c in contas:
                tok = cfg.tokens_dir / f"{c.name}.json"
                estado = f"[{COR_MANTER}]✓ token presente[/]" if tok.exists() else f"[{COR_LIXEIRA}]sem token[/]"
                linhas.append(f"  {GUTTER} {c.name}  {estado}")
        else:
            linhas.append(f"[{INK_DIM}]Nenhuma conta adicionada ainda.[/]")
        linhas.append(f"\n[{INK_DIM}]Enter para autorizar uma nova conta.[/]")
        return cab + "\n".join(linhas)

    def _det_imap(self) -> str:
        cab = self._titulo_det("Configurar Outlook/IMAP")
        cfg = self.app.config
        if cfg is None:
            return cab + f"\n[{INK_DIM}]Configuração não carregada.[/]"
        from apolo.config import load_accounts

        contas = [a for a in load_accounts(cfg.accounts_path) if a.provider == "imap"]
        linhas = [f"[{INK_DIM}]Contas IMAP genéricas (senha/senha de app — não OAuth).[/]\n"]
        if contas:
            from apolo import secrets

            linhas.append(f"[{INK_DIM}]Contas configuradas:[/]")
            for c in contas:
                tem_senha = secrets.lookup_account_password(f"imap:{c.name}") is not None
                estado = (
                    f"[{COR_MANTER}]✓ {c.host}:{c.port} ({c.security})[/]"
                    if tem_senha
                    else f"[{COR_LIXEIRA}]sem senha no keyring[/]"
                )
                linhas.append(f"  {GUTTER} {c.name}  {estado}")
        else:
            linhas.append(f"[{INK_DIM}]Nenhuma conta adicionada ainda.[/]")
        linhas.append(f"\n[{INK_DIM}]Enter para adicionar/editar uma conta.[/]")
        return cab + "\n".join(linhas)

    def _det_config(self) -> str:
        cab = self._titulo_det("Configurações")
        cfg = self.app.config
        if cfg is None:
            return cab + f"\n[{INK_DIM}]Ajustes de agendamento, IA, newsletters e credenciais.[/]"
        ia = f"[{COR_MANTER}]ligada[/]" if cfg.ai_enabled else f"[{INK_FAINT}]desligada[/]"
        return cab + (
            f"\n[{INK_DIM}]IA / classificação:[/] {ia}   [{INK_FAINT}](I liga/desliga na hora)[/]\n"
            f"[{INK_DIM}]Modelo:[/] {cfg.ollama_model}\n"
            f"[{INK_DIM}]Pastas:[/] {', '.join(cfg.folders)}\n\n"
            f"[{INK_DIM}]Enter para agendamento, IA, newsletters e senha do Bridge.[/]"
        )

    def _det_status(self) -> str:
        st = self.app.stats
        cab = self._titulo_det("Status & contadores")
        linhas = [
            f"\n[{INK_DIM}]Última passada:[/] [b]{fmt_run(st.last_run)}[/]",
            f"[{INK_DIM}]Na fila:[/] [b]{len(self.app.queue)}[/]",
            f"[{INK_DIM}]Regras:[/] [b]{st.rules_count}[/]",
        ]
        if st.acao_counts:
            linhas.append(f"\n[{INK_FAINT}]ação sugerida[/]")
            for acao, n in sorted(st.acao_counts.items()):
                cor = ACAO_COR.get(acao, AZURE_BRT)
                linhas.append(f"[{cor}]{GUTTER}[/] {acao:<10} [b]{n}[/]")
        return cab + "\n".join(linhas)

    def _det_vazio(self) -> str:
        return ""

    # ----- ações -----
    def _recarregar_fila(self) -> None:
        """Relê a fila do banco — pega o que o timer do systemd (`apolo run`
        em paralelo) inseriu enquanto o dono estava fora da listagem, mesmo que
        ainda não tenha passado pela IA. Seguro: decisões da listagem só saem
        do banco quando aplicadas (Enter), e nesse caso já não aparecem mais
        aqui; as descartadas (Esc/Q) voltam pro `app.queue` sem nunca ter
        tocado o banco.
        """
        if not self.app.config:
            return
        from apolo.storage.db import Storage

        try:
            with Storage(self.app.config.db_path) as store:
                self.app.queue = [Item(r) for r in store.fetch_queue()]
        except Exception:
            pass

    def _apos_listagem(self, _=None) -> None:
        self._recarregar_fila()
        self._atualizar()

    def _atualizar(self) -> None:
        """Refaz masthead + badges + detalhe (a fila pode ter encolhido)."""
        self.query_one("#mast-stats", Static).update(self._stats())
        menu = self.query_one("#hub-menu", ListView)
        idx = menu.index
        menu.clear()
        menu.extend(self._itens())
        if idx is not None:
            menu.index = idx
        item = menu.highlighted_child
        if isinstance(item, MenuItem):
            self._render_detalhe(item.key_id)

    def action_abrir(self) -> None:
        menu = self.query_one("#hub-menu", ListView)
        item = menu.highlighted_child
        if isinstance(item, MenuItem):
            self._rotear(item.key_id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MenuItem):
            self._rotear(event.item.key_id)

    def action_cursor_up(self) -> None:
        self.query_one("#hub-menu", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#hub-menu", ListView).action_cursor_down()

    def action_sair(self) -> None:
        self.app.exit()

    def action_toggle_ia(self) -> None:
        """Liga/desliga o Ollama na hora, sem passar pela tela de Configurações.

        Grava direto no .env (mesmo destino do switch de lá) e recarrega o
        Config em memória — vale já na próxima sincronização/rodada desta
        sessão. Desligada, o resíduo que a cascata não resolveu fica em
        'revisar' pra filtragem manual — nada é enviado ao Ollama.
        """
        if not self.app.config:
            self.notify("Configuração não carregada.", severity="error")
            return
        from apolo.config import Config
        from apolo.config_writer import env_path, set_env_values

        novo_estado = not self.app.config.ai_enabled
        try:
            set_env_values(env_path(), {"APOLO_AI_ENABLED": "true" if novo_estado else "false"})
        except Exception as exc:
            self.notify(f"erro ao salvar: {exc}", severity="error")
            return
        self.app.config = Config.load()
        self._atualizar()
        estado_txt = "ligada" if novo_estado else "desligada — resíduo fica para revisão manual"
        self.notify(f"IA (Ollama) {estado_txt}.", severity="information")

    def _rotear(self, key: str) -> None:
        if key == "review":
            self._recarregar_fila()
            from apolo.ui.queue import QueueScreen

            # Entra mesmo com a fila vazia: é lá dentro que se sincroniza (S)
            # sem travar a tela — a fila pode deixar de estar vazia ao vivo.
            self.app.push_screen(QueueScreen(), self._apos_listagem)
        elif key == "swipe":
            self._recarregar_fila()
            from apolo.ui.swipe_screen import SwipeScreen

            self.app.push_screen(SwipeScreen(), self._apos_listagem)
        elif key == "add_rule":
            from apolo.ui.rules_screen import AddRuleModal

            def _cb_add(resultado) -> None:
                self._atualizar()
                if resultado:
                    lista, tipo, valor, status = resultado
                    verbo = "já existia" if status == "exists" else "adicionada"
                    self.notify(f"{lista}: {tipo} {valor} {verbo}", severity="information")

            self.app.push_screen(AddRuleModal(), _cb_add)
        elif key == "preview":
            from apolo.ui.preview import PreviewScreen

            self.app.push_screen(PreviewScreen())
        elif key == "sugestoes":
            if not self.app.config:
                self.notify("Configuração não carregada.", severity="error")
                return

            def _cb_sugestoes(resultado: str | None) -> None:
                if resultado:
                    self.notify(resultado, title="apolo sugestões")
                self._atualizar()

            from apolo.ui.suggest_screen import SuggestionsScreen

            self.app.push_screen(SuggestionsScreen(), _cb_sugestoes)
        elif key == "rules":
            from apolo.ui.rules_screen import RulesScreen

            self.app.push_screen(RulesScreen(), lambda _=None: self._atualizar())
        elif key == "run":
            if not self.app.config:
                self.notify("Configuração não carregada.", severity="error")
                return

            def _cb_run(resultado: str | None) -> None:
                if resultado:
                    sev = "error" if resultado.startswith("erro:") else "information"
                    self.notify(resultado[:120], title="apolo run", severity=sev)
                self._atualizar()

            self.app.push_screen(RunModal(), _cb_run)
        elif key == "retry_ia":
            if not self.app.config:
                self.notify("Configuração não carregada.", severity="error")
                return

            def _cb_retry(resultado: str | None) -> None:
                if resultado:
                    sev = "error" if resultado.startswith("erro:") else "information"
                    self.notify(resultado[:120], title="apolo retry-ia", severity=sev)
                self._atualizar()

            self.app.push_screen(RetryIaModal(), _cb_retry)
        elif key == "config":
            from apolo.ui.settings import SettingsScreen

            self.app.push_screen(SettingsScreen())
        elif key == "gmail":
            from apolo.ui.gmail_setup import GmailSetupModal

            self.app.push_screen(GmailSetupModal(), lambda _=None: None)
        elif key == "imap":
            from apolo.ui.imap_setup import ImapSetupModal

            self.app.push_screen(ImapSetupModal(), lambda _=None: None)
        elif key == "status":
            from apolo.ui.status import StatusScreen

            self.app.push_screen(StatusScreen())


class RunModal(ModalScreen):
    """Executa `apolo run` numa thread e fecha ao terminar."""

    def compose(self) -> ComposeResult:
        with Vertical(id="run-box"):
            yield Static("[b]Rodar agora[/]", classes="cfg-title")
            yield Static("Buscando emails e classificando…", id="run-msg")
            yield Static(f"[{INK_FAINT}](pode levar alguns segundos)[/]")

    def on_mount(self) -> None:
        self._executar()

    @work(thread=True)
    def _executar(self) -> None:
        from apolo.cli import cmd_run

        buf = io.StringIO()
        resultado = "concluído."
        try:
            with contextlib.redirect_stdout(buf):
                cmd_run(self.app.config, notify_enabled=False)
            saida = buf.getvalue().strip()
            if saida:
                resultado = saida
        except Exception as exc:
            resultado = f"erro: {exc}"

        self.app.call_from_thread(self._apos_run, resultado)

    def _apos_run(self, resultado: str) -> None:
        from apolo.storage.db import Storage

        try:
            with Storage(self.app.config.db_path) as store:
                rows = store.fetch_queue()
                self.app.queue = [Item(r) for r in rows]
                self.app.stats.last_run = store.last_processed_at()
        except Exception:
            pass
        self.dismiss(resultado)


class RetryIaModal(ModalScreen):
    """Executa `apolo retry-ia` numa thread e fecha ao terminar."""

    def compose(self) -> ComposeResult:
        with Vertical(id="run-box"):
            yield Static("[b]Reclassificar pendentes (IA)[/]", classes="cfg-title")
            yield Static("Reenviando pro Ollama os presos sem IA…", id="run-msg")
            yield Static(f"[{INK_FAINT}](pode levar alguns segundos)[/]")

    def on_mount(self) -> None:
        self._executar()

    @work(thread=True)
    def _executar(self) -> None:
        from apolo.cli import cmd_retry_ia

        buf = io.StringIO()
        resultado = "concluído."
        try:
            with contextlib.redirect_stdout(buf):
                cmd_retry_ia(self.app.config)
            saida = buf.getvalue().strip()
            if saida:
                resultado = saida
        except Exception as exc:
            resultado = f"erro: {exc}"

        self.app.call_from_thread(self._apos_retry, resultado)

    def _apos_retry(self, resultado: str) -> None:
        from apolo.storage.db import Storage

        try:
            with Storage(self.app.config.db_path) as store:
                rows = store.fetch_queue()
                self.app.queue = [Item(r) for r in rows]
        except Exception:
            pass
        self.dismiss(resultado)
