"""Janela principal do ANTI CANJA."""
from __future__ import annotations

import html
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anticanja import APP_NAME, __version__
from anticanja.core import autostart, discord, latency, logbus, paths, updater, winproc
from anticanja.core.profiles import Profile, ProfileManager
from anticanja.core.settings import Settings
from anticanja.core.tunnel import State, TunnelError, TunnelManager, TunnelStatus
from anticanja.ui import icons, svgicons, theme
from anticanja.ui.widgets import ToggleSwitch, asset_pixmap

log = logbus.get("app")


class WheelLock(QObject):
    """Rolagem do mouse só mexe no widget apontado (lista/registro).

    Ao chegar no fim da lista, o Qt repassaria a rolagem para a página inteira.
    Este filtro consome o evento: a página só rola quando o mouse está fora deles.
    """

    def eventFilter(self, obj, ev):  # noqa: N802
        from PySide6.QtCore import QEvent

        if ev.type() == QEvent.Type.Wheel:
            area = obj.parent()
            bar = area.verticalScrollBar() if hasattr(area, "verticalScrollBar") else None
            if bar is not None:
                steps = ev.angleDelta().y() / 120
                bar.setValue(bar.value() - int(steps * max(1, bar.singleStep()) * 3))
            return True
        return False


class Bridge(QObject):
    """Leva eventos das threads de trabalho para a thread da interface."""

    log_line = Signal(str, str)
    tunnel_changed = Signal(object)
    discord_changed = Signal(object)
    task_done = Signal(str, object)  # (nome, erro ou None)
    pings_done = Signal(object)      # dict[host, PingResult]
    update_checked = Signal(object, object, bool)  # (Release|None, erro|None, manual)
    update_progress = Signal(int, int)
    update_downloaded = Signal(object, object)     # (zip Path|None, erro|None)


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def label(text: str = "", name: str | None = None) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, autostarted: bool = False) -> None:
        super().__init__()
        self.settings = settings
        self.profiles = ProfileManager()
        self.tunnel = TunnelManager(settings)
        self.monitor = discord.DiscordMonitor()
        self.bridge = Bridge()
        self._busy = False
        self._quitting = False
        self._profiles_cache: list[Profile] = []
        self._selftest_result = None
        self._pings: dict = {}
        self._pinging = False
        self._connect_best_after_ping = False
        self._discord_was_running = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(QSize(900, 600))
        self.resize(1400, 1000)

        self._build_ui()
        self._build_tray()

        # eventos
        logbus.setup().subscribe(lambda lvl, msg: self.bridge.log_line.emit(lvl, msg))
        self.bridge.log_line.connect(self._append_log)
        self.bridge.tunnel_changed.connect(self._render_tunnel)
        self.bridge.discord_changed.connect(self._render_discord)
        self.bridge.task_done.connect(self._on_task_done)
        self.bridge.pings_done.connect(self._on_pings_done)
        self.bridge.update_checked.connect(self._on_update_checked)
        self.bridge.update_progress.connect(self._on_update_progress)
        self.bridge.update_downloaded.connect(self._on_update_downloaded)
        self._upd_dialog = None
        self._upd_cancel = False
        self.tunnel.exclude_ips_provider = lambda: [p.endpoint_host for p in self._profiles_cache if p.valid]
        self.tunnel.subscribe(lambda st: self.bridge.tunnel_changed.emit(st))
        self.monitor.subscribe(lambda st: self.bridge.discord_changed.emit(st))

        log.info("%s %s iniciado.", APP_NAME, __version__)
        log.info("Dados: %s", paths.data_dir())
        exe = paths.singbox_exe()
        if exe.exists():
            log.info("Motor do túnel: %s", exe.name)
        else:
            log.error("sing-box.exe não encontrado em %s — rode o build.ps1.", exe.parent)
        if not winproc.is_admin():
            log.warning("Sem privilégios de administrador: não será possível criar o túnel.")

        self._import_bundled_confs()
        self.monitor.start()
        self._render_discord(self.monitor.current)
        self.refresh_profiles()
        self._render_tunnel(self.tunnel.status)
        self._sync_settings_widgets()

        # Política inicial: kill-switch ocioso e/ou conexão automática.
        QTimer.singleShot(300, lambda: self._startup_actions(autostarted))
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._tick)
        self._uptime_timer.start(1000)

    # ================================================================ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)

        # ---------------- barra lateral
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 14, 16, 16)
        sl.setSpacing(6)
        mascot = QLabel()
        mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mp = asset_pixmap("mascote.png", 208, 320)
        if not mp.isNull():
            mascot.setPixmap(mp)
        else:
            mascot.setText(APP_NAME)
            mascot.setObjectName("heroTitle")
        sl.addWidget(mascot)
        sl.addSpacing(10)
        self.pages = QStackedWidget()
        self._nav: list[QPushButton] = []
        for i, (txt, ic) in enumerate((("Principal", "home"), ("Configurações", "gear"), ("Sobre", "info"))):
            b = QPushButton("   " + txt)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(svgicons.icon(ic, theme.RED_HOVER if i else "#ffffff"))
            b.setIconSize(QSize(24, 24))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=i: self._go(n))
            sl.addWidget(b)
            self._nav.append(b)
        sl.addStretch(1)
        body.addWidget(side)

        body.addWidget(self.pages, 1)
        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        main_page = self._page_main()
        main_page.setMinimumSize(1020, 860)
        main_scroll.setWidget(main_page)
        self.pages.addWidget(main_scroll)
        self.pages.addWidget(self._page_settings())
        self.pages.addWidget(self._page_about())
        self._go(0)

        # ---------------- rodapé
        foot = QWidget()
        foot.setObjectName("footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        x = QLabel()
        x.setPixmap(svgicons.pixmap("unplug", 22, theme.RED))
        fl.addWidget(x)
        fl.addWidget(label(APP_NAME, "brand"))
        fl.addWidget(label("•  Sua conexão, do seu jeito.", "muted"))
        fl.addStretch(1)
        fl.addWidget(label(f"v{__version__}", "muted"))
        self.footer_dot = QLabel()
        fl.addWidget(self.footer_dot)
        outer.addWidget(foot)

    def _go(self, n: int) -> None:
        self.pages.setCurrentIndex(n)
        for i, b in enumerate(self._nav):
            b.setChecked(i == n)
            b.setIcon(svgicons.icon(("home", "gear", "info")[i], "#ffffff" if i == n else theme.RED_HOVER))

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)
        return w, lay

    @staticmethod
    def _card_title(icon_name: str, text: str) -> QHBoxLayout:
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(svgicons.pixmap(icon_name, 24, theme.RED))
        h.addWidget(ic)
        h.addSpacing(6)
        h.addWidget(label(text, "cardTitle"))
        h.addStretch(1)
        return h

    @staticmethod
    def _tool_button(text: str, icon_name: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("tool")
        b.setFixedHeight(58)
        b.setIcon(svgicons.icon(icon_name))
        b.setIconSize(QSize(20, 20))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # ---------------------------------------------------------------- Principal
    def _page_main(self) -> QWidget:
        w, lay = self._page()

        # ===== topo: hero + info + rosto
        top = QHBoxLayout()
        top.setSpacing(14)
        hero = QFrame()
        hero.setObjectName("hero")
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.setSpacing(18)
        av = QLabel()
        ap = asset_pixmap("avatar.png", 150, 150)
        if not ap.isNull():
            av.setPixmap(ap)
        hl.addWidget(av)
        tl = QVBoxLayout()
        tl.setSpacing(4)
        tl.addWidget(label(APP_NAME, "heroTitle"))
        sub = label("Só o Discord passa pela VPN.\nO resto segue pela sua internet.", "muted")
        sub.setStyleSheet("font-size: 11pt;")
        tl.addWidget(sub)
        hs = QFrame()
        hs.setObjectName("hsep")
        tl.addSpacing(6)
        tl.addWidget(hs)
        tl.addSpacing(4)
        srow = QHBoxLayout()
        self.state_dot = QLabel()
        self.state_label = label("VPN Desconectada", "stateLabel")
        srow.addWidget(self.state_dot)
        srow.addWidget(self.state_label)
        srow.addStretch(1)
        tl.addLayout(srow)
        self.detail_label = label("", "muted")
        self.detail_label.setWordWrap(True)
        tl.addWidget(self.detail_label)
        self.uptime_label = label("", "muted")
        tl.addWidget(self.uptime_label)
        tl.addStretch(1)
        hl.addLayout(tl, 1)
        sep = QFrame()
        sep.setObjectName("sep")
        hl.addWidget(sep)

        info = QGridLayout()
        info.setHorizontalSpacing(12)
        info.setVerticalSpacing(10)
        self.v_endpoint = label("-", "infoVal")
        self.v_server_ip = label("-", "infoVal")
        self.v_exit_ip = label("-", "infoVal")
        self.profile_label = label("-", "infoVal")
        rows = (("globe", "Endpoint", self.v_endpoint), ("server", "Servidor", self.v_server_ip),
                ("shield", "IP de saída do Discord", self.v_exit_ip), ("user", "Perfil Ativo", self.profile_label))
        for r, (ic, key, val) in enumerate(rows):
            il = QLabel()
            il.setPixmap(svgicons.pixmap(ic, 28, theme.RED))
            info.addWidget(il, r, 0, 2 if False else 1, 1, Qt.AlignmentFlag.AlignTop)
            box = QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(label(key, "infoKey"))
            box.addWidget(val)
            info.addLayout(box, r, 1)
        hl.addLayout(info)
        top.addWidget(hero, 1)
        face = QLabel()
        fp = asset_pixmap("rosto.png", 260, 200)
        if not fp.isNull():
            face.setPixmap(fp)
            face.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            top.addWidget(face)
        lay.addLayout(top)

        # ===== meio: perfis + status do Discord
        mid = QHBoxLayout()
        mid.setSpacing(14)
        pc = card()
        pl = QVBoxLayout(pc)
        pl.setContentsMargins(16, 14, 16, 14)
        pl.setSpacing(10)
        ph = self._card_title("layers", "Seus Perfis")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar perfil…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(svgicons.icon("search", theme.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet("padding-left: 8px;")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._filter_profiles)
        ph.addWidget(self.search)
        self.btn_refresh = QPushButton()
        self.btn_refresh.setObjectName("iconOnly")
        self.btn_refresh.setIcon(svgicons.icon("refresh"))
        self.btn_refresh.setToolTip("Atualizar lista e medir ping")
        self.btn_refresh.clicked.connect(lambda: (self.refresh_profiles(), self.run_pings()))
        ph.addWidget(self.btn_refresh)
        pl.addLayout(ph)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Perfil", "Endpoint", "Ping", "Modificado", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._profile_menu)
        self.table.itemDoubleClicked.connect(lambda *_: self.connect_selected())
        self.table.itemSelectionChanged.connect(self._on_selection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for c in (1, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(230)
        pl.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_add = self._tool_button("Adicionar\nPerfil", "plus")
        self.btn_folder = self._tool_button("Abrir Pasta", "folder")
        self.btn_best = self._tool_button("Conectar ao\nmelhor ping", "star")
        self.btn_remove = self._tool_button("Remover\nPerfil", "trash")
        self.btn_add.clicked.connect(self.import_profiles)
        self.btn_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.profiles_dir()))))
        self.btn_best.clicked.connect(self.connect_best)
        self.btn_remove.clicked.connect(lambda: self._delete(self._selected_profile()) if self._selected_profile() else None)
        for b in (self.btn_add, self.btn_folder, self.btn_best, self.btn_remove):
            row.addWidget(b, 1)
        pl.addLayout(row)
        self.ping_hint = label("Duplo clique conecta · botão direito para mais opções", "muted")
        pl.addWidget(self.ping_hint)
        mid.addWidget(pc, 3)

        dc = card()
        dc.setMinimumWidth(270)
        dl = QVBoxLayout(dc)
        dl.setContentsMargins(16, 14, 16, 16)
        dl.setSpacing(8)
        dl.addLayout(self._card_title("headset", "Status do Discord"))
        dl.addSpacing(10)
        drow = QHBoxLayout()
        drow.addStretch(1)
        self.discord_dot = QLabel()
        drow.addWidget(self.discord_dot)
        self.discord_state = label("Fechado", "bigState")
        drow.addWidget(self.discord_state)
        drow.addStretch(1)
        dl.addLayout(drow)
        self.v_discord = label("Processo: Nenhum", "muted")
        self.v_discord.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(self.v_discord)
        hs2 = QFrame()
        hs2.setObjectName("hsep")
        dl.addWidget(hs2)
        big = QLabel()
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setPixmap(svgicons.pixmap("headset", 84, "#cfc3c6"))
        dl.addWidget(big, 1)
        self.discord_alert = QFrame()
        self.discord_alert.setObjectName("alert")
        al = QHBoxLayout(self.discord_alert)
        al.setContentsMargins(12, 10, 12, 10)
        ai = QLabel()
        ai.setPixmap(svgicons.pixmap("alert", 30, theme.RED))
        al.addWidget(ai)
        self.discord_alert_text = label("O Discord não está em execução.")
        self.discord_alert_text.setWordWrap(True)
        al.addWidget(self.discord_alert_text, 1)
        dl.addWidget(self.discord_alert)
        mid.addWidget(dc, 1)
        lay.addLayout(mid, 1)

        # ===== botões grandes
        act = QHBoxLayout()
        act.setSpacing(14)
        self.btn_connect = QPushButton("Conectar VPN")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.setIcon(svgicons.icon("bolt"))
        self.btn_disconnect = QPushButton("Desconectar VPN")
        self.btn_disconnect.setObjectName("danger")
        self.btn_disconnect.setIcon(svgicons.icon("unplug", "#ffffff", "#6b5a5f"))
        self.btn_discord = QPushButton("Abrir Discord")
        self.btn_discord.setObjectName("big")
        self.btn_discord.setIcon(svgicons.icon("external"))
        self.btn_discord.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        for b in (self.btn_connect, self.btn_disconnect, self.btn_discord):
            b.setIconSize(QSize(26, 26))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect.clicked.connect(self.connect_selected)
        self.btn_disconnect.clicked.connect(self.disconnect_vpn)
        self.btn_discord.clicked.connect(self.open_discord)
        act.addWidget(self.btn_connect, 3)
        act.addWidget(self.btn_disconnect, 3)
        act.addWidget(self.btn_discord, 2)
        lay.addLayout(act)

        # ===== baixo: configurações rápidas + registro
        bot = QHBoxLayout()
        bot.setSpacing(14)
        qc = card()
        ql = QVBoxLayout(qc)
        ql.setContentsMargins(16, 14, 16, 14)
        ql.setSpacing(6)
        th = self._card_title("info", "Como pegar os .conf no ProtonVPN")
        ql.addLayout(th)
        steps = label(
            "<b style='color:#ff2b40'>1.</b> Entre em <b>account.protonvpn.com</b> (conta grátis serve).<br>"
            "<b style='color:#ff2b40'>2.</b> Vá em <b>Downloads → WireGuard configuration</b>.<br>"
            "<b style='color:#ff2b40'>3.</b> Dê um nome, escolha a plataforma <b>Windows</b> e o servidor.<br>"
            "<b style='color:#ff2b40'>4.</b> Clique em <b>Create</b> e depois em <b>Download</b> (.conf).<br>"
            "<b style='color:#ff2b40'>5.</b> Aqui no app, clique em <b>Adicionar Perfil</b> e escolha o arquivo.<br>"
            "<span style='color:#a3969a'>Cada .conf é uma chave: não use o mesmo perfil em dois PCs ao mesmo tempo.</span>")
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setWordWrap(True)
        ql.addWidget(steps)
        ql.addStretch(1)
        b_site = QPushButton("  Abrir site do ProtonVPN")
        b_site.setObjectName("ghost")
        b_site.setIcon(svgicons.icon("external", theme.RED_HOVER))
        b_site.setCursor(Qt.CursorShape.PointingHandCursor)
        b_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://account.protonvpn.com/downloads")))
        ql.addWidget(b_site, 0, Qt.AlignmentFlag.AlignLeft)
        bot.addWidget(qc, 2)

        lc = card()
        ll = QVBoxLayout(lc)
        ll.setContentsMargins(16, 14, 16, 12)
        lh = self._card_title("log", "Registro de Atividades")
        b_clear = QPushButton("  Limpar Log")
        b_clear.setObjectName("ghost")
        b_clear.setIcon(svgicons.icon("trash", theme.RED_HOVER))
        lh.addWidget(b_clear)
        ll.addLayout(lh)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setMinimumHeight(120)
        self._wheel_lock = WheelLock(self)
        self.log_view.viewport().installEventFilter(self._wheel_lock)
        self.table.viewport().installEventFilter(self._wheel_lock)
        b_clear.clicked.connect(self.log_view.clear)
        ll.addWidget(self.log_view, 1)
        bot.addWidget(lc, 3)
        lay.addLayout(bot)
        return w

    # ---------------------------------------------------------------- Configurações
    def _page_settings(self) -> QWidget:
        w, lay = self._page()
        lay.addWidget(label("Configurações", "pageTitle"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("page")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 8, 0)
        il.setSpacing(14)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        def section(icon_name: str, title: str) -> QVBoxLayout:
            c = card()
            cl = QVBoxLayout(c)
            cl.setContentsMargins(18, 14, 18, 14)
            cl.setSpacing(4)
            cl.addLayout(self._card_title(icon_name, title))
            il.addWidget(c)
            return cl

        def chk(box: QVBoxLayout, text: str, tip: str) -> ToggleSwitch:
            c = ToggleSwitch(text)
            c.setToolTip(tip)
            c.toggled.connect(self._save_settings)
            box.addWidget(c)
            return c

        s1 = section("home", "Inicialização")
        self.c_startup = chk(s1, "Iniciar o ANTI CANJA com o Windows",
                             "Cria uma tarefa agendada com privilégios elevados (sem UAC a cada logon).")
        self.c_autoconnect = chk(s1, "Conectar automaticamente usando o último perfil", "")
        self.c_auto_best = chk(s1, "…escolhendo o servidor de menor ping",
                               "Na conexão automática, mede o ping de todos os perfis e usa o melhor.")
        self.c_launch_discord = chk(s1, "Abrir o Discord automaticamente após conectar", "")
        self.c_tray = chk(s1, "Minimizar para a bandeja ao fechar a janela", "")
        self.c_start_min = chk(s1, "Iniciar minimizado na bandeja", "")

        s0 = section("refresh", "Atualizações")
        self.c_upd = chk(s0, "Verificar atualizações ao abrir o programa", "Consulta o GitHub do ANTI CANJA.")
        self.c_upd_auto = chk(s0, "Instalar atualizações automaticamente (sem perguntar)", "")
        b_upd = self._tool_button("Verificar agora", "refresh")
        b_upd.setFixedHeight(42)
        b_upd.clicked.connect(lambda: self.check_updates(manual=True))
        s0.addWidget(b_upd, 0, Qt.AlignmentFlag.AlignLeft)

        s2 = section("headset", "Discord")
        self.c_restart_off = chk(s2, "Reiniciar o Discord ao desconectar ou sair do app",
                                 "Fecha e reabre o Discord para ele voltar à sua internet normal na hora.")
        rrow = QHBoxLayout()
        rrow.addWidget(label("Se o Discord já estiver aberto ao conectar:"))
        rrow.addStretch(1)
        self.cb_restart = QComboBox()
        self.cb_restart.addItem("Perguntar", "ask")
        self.cb_restart.addItem("Reiniciar o Discord", "always")
        self.cb_restart.addItem("Não fazer nada", "never")
        self.cb_restart.currentIndexChanged.connect(self._save_settings)
        rrow.addWidget(self.cb_restart)
        s2.addLayout(rrow)
        crow = QHBoxLayout()
        crow.addWidget(label("Canal preferido do Discord:"))
        crow.addStretch(1)
        self.cb_channel = QComboBox()
        for ch in discord.CHANNELS[:3]:
            self.cb_channel.addItem(ch.label, ch.key)
        self.cb_channel.currentIndexChanged.connect(self._save_settings)
        crow.addWidget(self.cb_channel)
        s2.addLayout(crow)

        s3 = section("shield", "Proteção")
        self.c_killswitch = chk(s3, "Kill-switch: se o túnel cair, bloquear o Discord (nunca vazar)",
                                "Se o sing-box parar inesperadamente, o Discord é encerrado e o túnel reiniciado.")
        self.c_block_idle = chk(s3, "Bloquear o Discord enquanto a VPN estiver desligada",
                                "O Discord só acessa a internet com a VPN conectada. Os outros apps não são afetados.")
        self.c_dns = chk(s3, "Resolver DNS do Discord pela VPN (avançado — pode afetar outros apps)", "Captura o DNS do Windows. Deixe desligado se algum programa parar de funcionar.")
        self.c_strict = chk(s3, "Rota estrita (avançado — pode afetar outros apps)",
                            "Impede que o Windows envie DNS por outras interfaces.")
        il.addStretch(1)

        self._settings_widgets = [self.c_upd, self.c_upd_auto, self.c_startup, self.c_autoconnect, self.c_auto_best, self.c_launch_discord,
                                  self.c_tray, self.c_start_min, self.c_restart_off, self.c_killswitch,
                                  self.c_block_idle, self.c_dns, self.c_strict, self.cb_restart, self.cb_channel]
        return w

    # ---------------------------------------------------------------- Sobre
    def _page_about(self) -> QWidget:
        w, lay = self._page()
        c = card()
        cl = QHBoxLayout(c)
        cl.setContentsMargins(24, 22, 24, 22)
        cl.setSpacing(24)
        av = QLabel()
        ap = asset_pixmap("avatar.png", 180, 180)
        if not ap.isNull():
            av.setPixmap(ap)
        cl.addWidget(av, 0, Qt.AlignmentFlag.AlignTop)
        tl = QVBoxLayout()
        tl.addWidget(label(APP_NAME, "heroTitle"))
        tl.addWidget(label(f"Versão {__version__}", "muted"))
        b_chk = self._tool_button("Verificar atualizações", "refresh")
        b_chk.setFixedHeight(42)
        b_chk.clicked.connect(lambda: self.check_updates(manual=True))
        tl.addWidget(b_chk, 0, Qt.AlignmentFlag.AlignLeft)
        txt = label(
            "Gerenciador de VPN exclusivo para o Discord.<br><br>"
            "• Só os processos do Discord passam pelo túnel WireGuard (perfis .conf do ProtonVPN).<br>"
            "• Navegadores, jogos e o resto do Windows usam a sua internet normal.<br>"
            "• DNS do Discord protegido, kill-switch e escolha automática do melhor ping.<br>"
            "• Nada é alterado de forma permanente no Windows: ao desconectar, tudo volta ao normal.<br><br>"
            "Motor do túnel: sing-box (GPL-3.0), distribuído sem modificações.")
        txt.setTextFormat(Qt.TextFormat.RichText)
        txt.setWordWrap(True)
        tl.addWidget(txt)
        tl.addStretch(1)
        cl.addLayout(tl, 1)
        lay.addWidget(c)
        lay.addStretch(1)
        return w

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon(theme.MUTED), self)
        self.tray.setToolTip(APP_NAME)
        m = QMenu()
        self.a_show = QAction("Abrir ANTI CANJA", self)
        self.a_show.triggered.connect(self.show_normal)
        self.a_connect = QAction("Conectar (último perfil)", self)
        self.a_connect.triggered.connect(self.connect_last)
        self.a_disconnect = QAction("Desconectar", self)
        self.a_disconnect.triggered.connect(self.disconnect_vpn)
        self.a_best = QAction("Conectar ao melhor ping", self)
        self.a_best.triggered.connect(self.connect_best)
        a_discord = QAction("Abrir Discord", self)
        a_discord.triggered.connect(self.open_discord)
        a_quit = QAction("Sair", self)
        a_quit.triggered.connect(self.quit_app)
        for a in (self.a_show, None, self.a_connect, self.a_best, self.a_disconnect, a_discord, None, a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        self.tray_menu = m  # manter referência (senão o menu é coletado)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(
            lambda reason: self.show_normal() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None
        )
        self.tray.show()

    # ================================================================ render
    def _append_log(self, level: str, msg: str) -> None:
        color = {"ERROR": theme.RED, "CRITICAL": theme.RED, "WARNING": theme.AMBER}.get(level, "#c9c2db")
        self.log_view.appendHtml(f'<span style="color:{color}; white-space:pre-wrap">{html.escape(msg)}</span>')
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        if level in ("ERROR", "CRITICAL") and not self.isVisible() and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, msg.split("] ", 1)[-1][:200], QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _render_tunnel(self, st: TunnelStatus) -> None:
        color = theme.STATE_COLORS.get(st.state.value, theme.MUTED)
        if st.state == State.CONNECTED and st.healthy is False:
            color = theme.AMBER
        self.state_dot.setPixmap(icons.dot_pixmap(color, 16))
        self.footer_dot.setPixmap(icons.dot_pixmap(color, 10))
        text = {State.DISCONNECTED: "VPN Desconectada", State.CONNECTED: "VPN Conectada"}.get(st.state, st.state.value)
        if st.state == State.CONNECTED and st.healthy is False:
            text = "Túnel ativo — servidor sem resposta"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {color};")
        self.detail_label.setText(st.detail or "")
        self.profile_label.setText(st.profile or "-")
        self.v_endpoint.setText(st.endpoint or "-")
        self.v_server_ip.setText(st.server_ip or "-")
        if st.exit_ip:
            self.v_exit_ip.setText(st.exit_ip + (f"  ({st.exit_location})" if st.exit_location else ""))
        elif st.state == State.CONNECTED:
            self.v_exit_ip.setText("verificando…" if st.healthy is None else "sem resposta")
        else:
            self.v_exit_ip.setText("-")
        self.tray.setIcon(icons.app_icon(color))
        self.tray.setToolTip(f"{APP_NAME} — {text}" + (f"\n{st.profile}" if st.profile else ""))
        self._render_active_column()
        self._update_buttons()

    def _render_discord(self, st: discord.DiscordStatus) -> None:
        color = theme.GREEN if st.running else theme.RED
        self.discord_dot.setPixmap(icons.dot_pixmap(color, 22))
        self.discord_state.setText("Aberto" if st.running else "Fechado")
        self.discord_state.setStyleSheet(f"color: {color};")
        if st.running:
            pids = ", ".join(str(p) for p in st.pids[:3]) + ("…" if len(st.pids) > 3 else "")
            self.v_discord.setText(f"{', '.join(st.channels)} · PID {pids}")
            vpn = self.tunnel.status.state == State.CONNECTED
            self.discord_alert_text.setText("Tráfego do Discord passando pela VPN." if vpn
                                            else "Discord aberto SEM VPN. Conecte para protegê-lo.")
        else:
            self.v_discord.setText("Processo: Nenhum")
            self.discord_alert_text.setText("O Discord não está em execução.")

    def _render_active_column(self) -> None:
        active = self.tunnel.status.profile if self.tunnel.status.state in (State.CONNECTED, State.CONNECTING) else None
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            it = self.table.item(r, 4)
            is_active = name == active
            it.setText("● Ativo" if is_active else "-")
            it.setForeground(QBrush(QColor(theme.RED_HOVER if is_active else theme.MUTED)))
            sel = self.table.item(r, 0).isSelected()
            self.table.item(r, 0).setText(("◉  " if sel else "○  ") + name)
        if hasattr(self, "discord_state"):
            self._render_discord(self.monitor.current)

    def _tick(self) -> None:
        st = self.tunnel.status
        if st.state == State.CONNECTED and st.since:
            s = int(time.time() - st.since)
            self.uptime_label.setText(f"Conectado há {s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        else:
            self.uptime_label.setText("")

    def _update_buttons(self) -> None:
        st = self.tunnel.status.state
        has_sel = self._selected_profile() is not None
        self.btn_connect.setEnabled(not self._busy and has_sel)
        self.btn_connect.setText("Trocar de perfil" if st == State.CONNECTED else "Conectar VPN")
        self.btn_disconnect.setEnabled(not self._busy and st in (State.CONNECTED, State.CONNECTING, State.ERROR, State.BLOCKING))
        self.btn_disconnect.setText("Remover bloqueio" if st == State.BLOCKING and not self.settings.kill_switch_when_disconnected else "Desconectar VPN")
        self.a_connect.setEnabled(not self._busy and bool(self.settings.last_profile))
        self.a_disconnect.setEnabled(self.btn_disconnect.isEnabled())
        if hasattr(self, "btn_best"):
            self.btn_best.setEnabled(not self._busy and not self._pinging and bool(self._profiles_cache))
            self.btn_remove.setEnabled(not self._busy and has_sel)
        if hasattr(self, "btn_selftest"):  # botão removido da interface (agora no menu do perfil)
            self.btn_selftest.setEnabled(not self._busy and st in (State.CONNECTED, State.BLOCKING))

    # ================================================================ perfis
    def refresh_profiles(self) -> None:
        sel = self._selected_profile()
        sel_name = sel.name if sel else self.settings.last_profile
        def _ping_key(p):
            r = self._pings.get(p.endpoint_host) if p.valid else None
            return (r is None or r.ms is None, r.ms if r and r.ms is not None else 0, p.name.lower())

        self._profiles_cache = sorted(self.profiles.list(), key=_ping_key)
        self.table.setRowCount(0)
        for p in self._profiles_cache:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name_item = QTableWidgetItem("○  " + p.name)
            name_item.setData(Qt.ItemDataRole.UserRole, p.name)
            name_item.setToolTip(f"Servidor: {p.server_name}" if p.valid else (p.error or "inválido"))
            if not p.valid:
                name_item.setForeground(QBrush(QColor(theme.AMBER)))
            cells = [name_item, QTableWidgetItem(p.endpoint), QTableWidgetItem("…" if self._pinging else "-"),
                     QTableWidgetItem(p.modified.strftime("%d/%m/%Y %H:%M")), QTableWidgetItem("-")]
            for c, it in enumerate(cells):
                self.table.setItem(r, c, it)
            if p.name == sel_name:
                self.table.selectRow(r)
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)
        self._render_active_column()
        self._render_pings()
        self._filter_profiles(self.search.text())
        self._update_buttons()

    def _filter_profiles(self, text: str) -> None:
        t = text.strip().lower()
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) or ""
            ep = self.table.item(r, 1).text()
            self.table.setRowHidden(r, bool(t) and t not in name.lower() and t not in ep)

    def _on_selection(self) -> None:
        self._render_active_column()
        self._update_buttons()
        p = self._selected_profile()
        if not hasattr(self, "detail_box"):
            return
        if not p:
            self.detail_box.setText("Selecione um perfil na tela Principal.")
        elif not p.valid:
            self.detail_box.setText(f"<b>{html.escape(p.name)}</b><br>Perfil inválido: {html.escape(p.error or '')}")
        else:
            info = p.config.redacted_summary()
            ping = self._pings.get(p.endpoint_host)
            self.detail_box.setText(
                f"<b style='font-size:13pt'>{html.escape(p.name)}</b><br>"
                f"Servidor: {html.escape(str(info['servidor']))}<br>Endpoint: {info['endpoint']}<br>"
                f"Endereço no túnel: {info['enderecos']}<br>DNS da VPN: {info['dns']}<br>"
                f"IPv6: {info['ipv6']}<br>Ping: {ping.label if ping else '-'}<br>"
                f"Arquivo: {html.escape(str(p.path))}<br><i>A chave privada nunca é exibida.</i>")

    def _import_bundled_confs(self) -> None:
        """Importa os .conf que acompanham o pacote (pasta "confs" ao lado do .exe).

        Só roda quando ainda não há nenhum perfil, então perfis excluídos pelo
        usuário não voltam sozinhos.
        """
        bundled = paths.app_root() / "confs"
        if self.profiles.list() or not bundled.is_dir():
            return
        files = sorted(bundled.glob("*.conf"))
        if not files:
            return
        ok, failed = self.profiles.import_files(files)
        if ok:
            log.info("%d perfil(is) do pacote importado(s) automaticamente.", len(ok))
        for name, err in failed:
            log.warning("Perfil do pacote ignorado (%s): %s", name, err)

    # ================================================================ ping
    def run_pings(self, connect_best: bool = False) -> None:
        if self._pinging:
            return
        hosts = [p.endpoint_host for p in self._profiles_cache if p.valid]
        if not hosts:
            if connect_best:
                QMessageBox.information(self, APP_NAME, "Importe perfis .conf primeiro.")
            return
        self._pinging = True
        self._connect_best_after_ping = connect_best
        self.btn_refresh.setEnabled(False)
        for r in range(self.table.rowCount()):
            self.table.item(r, 2).setText("…")
        log.info("Medindo ping de %d servidor(es)…", len(hosts))
        threading.Thread(target=lambda: self.bridge.pings_done.emit(latency.ping_many(hosts, count=4)),
                         name="ping", daemon=True).start()

    def _on_pings_done(self, results: dict) -> None:
        self._pinging = False
        self._pings = results
        self.btn_refresh.setEnabled(True)
        self.refresh_profiles()  # reordena: menor ping primeiro
        b = latency.best(results)
        prof = next((p for p in self._profiles_cache if p.valid and b and p.endpoint_host == b.host), None)
        if prof:
            log.info("Melhor servidor: %s — %s.", prof.name, b.label)
            self.ping_hint.setText(f"★ Melhor ping: {prof.name} ({b.label}) · duplo clique conecta · botão direito para mais opções")
        else:
            log.warning("Nenhum servidor respondeu ao ping.")
        if self._connect_best_after_ping:
            self._connect_best_after_ping = False
            if prof:
                cur = self.tunnel.status
                if cur.state == State.CONNECTED and cur.profile == prof.name:
                    log.info("Você já está no servidor de melhor ping.")
                else:
                    self._select_profile(prof.name)
                    self._connect_profile(prof)
            else:
                QMessageBox.warning(self, APP_NAME, "Nenhum servidor respondeu ao ping.")
        self._update_buttons()

    def _render_pings(self) -> None:
        b = latency.best(self._pings) if self._pings else None
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            prof = next((p for p in self._profiles_cache if p.name == name), None)
            it = self.table.item(r, 2)
            res = self._pings.get(prof.endpoint_host) if prof and prof.valid else None
            if res is None:
                it.setText("…" if self._pinging else "-")
                it.setForeground(QBrush(QColor(theme.MUTED)))
                continue
            star = "★ " if b and res.host == b.host else ""
            it.setText(star + res.label)
            it.setToolTip(f"{res.sent - res.loss}/{res.sent} respostas ({res.method})")
            if res.ms is None:
                color = theme.RED
            elif res.ms < 80:
                color = theme.GREEN
            elif res.ms < 160:
                color = theme.AMBER
            else:
                color = theme.RED
            it.setForeground(QBrush(QColor(color)))

    def _select_profile(self, name: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == name:
                self.table.selectRow(r)
                return

    def connect_best(self) -> None:
        self.run_pings(connect_best=True)

    def _selected_profile(self) -> Profile | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        name = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((p for p in self._profiles_cache if p.name == name), None)

    def import_profiles(self) -> None:
        start = str(Path.home() / "Downloads")
        files, _ = QFileDialog.getOpenFileNames(self, "Importar perfis WireGuard", start,
                                                "WireGuard (*.conf);;Todos os arquivos (*.*)")
        if not files:
            return
        ok, failed = self.profiles.import_files(files)
        self.refresh_profiles()
        if failed:
            QMessageBox.warning(self, APP_NAME, "Alguns arquivos não foram importados:\n\n" +
                                "\n".join(f"• {n}: {e}" for n, e in failed))
        if ok:
            log.info("%d perfil(is) importado(s).", len(ok))
            self.run_pings()

    def _profile_menu(self, pos) -> None:
        p = self._selected_profile()
        if not p:
            return
        m = QMenu(self)
        m.addAction("Conectar", self.connect_selected).setEnabled(p.valid and not self._busy)
        m.addAction("Ver detalhes…", lambda: self._show_details(p))
        m.addAction("Testar ping de todos", lambda: self.run_pings())
        a_iso = m.addAction("Testar isolamento", self.run_selftest)
        a_iso.setEnabled(self.tunnel.status.state in (State.CONNECTED, State.BLOCKING))
        m.addSeparator()
        m.addAction("Renomear…", lambda: self._rename(p))
        m.addAction("Atualizar com novo .conf…", lambda: self._replace(p))
        m.addAction("Mostrar na pasta", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.path.parent))))
        m.addSeparator()
        m.addAction("Excluir", lambda: self._delete(p))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _is_active(self, p: Profile) -> bool:
        return self.tunnel.status.profile == p.name and self.tunnel.status.state in (State.CONNECTED, State.CONNECTING)

    def _show_details(self, p: Profile) -> None:
        if not p.valid:
            QMessageBox.warning(self, p.name, f"Perfil inválido:\n{p.error}")
            return
        info = p.config.redacted_summary()
        labels = {"servidor": "Servidor", "endpoint": "Endpoint", "enderecos": "Endereço(s) no túnel",
                  "dns": "DNS da VPN", "mtu": "MTU", "allowed_ips": "AllowedIPs", "ipv6": "IPv6",
                  "chave_publica_servidor": "Chave pública do servidor", "chave_pre_compartilhada": "PresharedKey"}
        body = "\n".join(f"{labels[k]}: {v}" for k, v in info.items())
        body += f"\n\nArquivo: {p.path}\n(A chave privada nunca é exibida.)"
        QMessageBox.information(self, f"Perfil — {p.name}", body)

    def _rename(self, p: Profile) -> None:
        if self._is_active(p):
            QMessageBox.information(self, APP_NAME, "Desconecte antes de renomear o perfil ativo.")
            return
        new, ok = QInputDialog.getText(self, "Renomear perfil", "Novo nome:", text=p.name)
        if not ok or not new.strip() or new.strip() == p.name:
            return
        try:
            np = self.profiles.rename(p.name, new.strip())
            if self.settings.last_profile == p.name:
                self.settings.last_profile = np.name
                self.settings.save()
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, APP_NAME, str(e))
        self.refresh_profiles()

    def _replace(self, p: Profile) -> None:
        f, _ = QFileDialog.getOpenFileName(self, f"Novo .conf para {p.name}", str(Path.home() / "Downloads"),
                                           "WireGuard (*.conf)")
        if not f:
            return
        try:
            self.profiles.replace(p.name, f)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Não foi possível atualizar: {e}")
        self.refresh_profiles()
        if self._is_active(p):
            log.info("Perfil ativo atualizado — reconecte para aplicar.")

    def _delete(self, p: Profile) -> None:
        if self._is_active(p):
            QMessageBox.information(self, APP_NAME, "Desconecte antes de excluir o perfil ativo.")
            return
        if QMessageBox.question(self, APP_NAME, f"Excluir o perfil '{p.name}'?\n\n"
                                "O arquivo original que você importou não será afetado.") != QMessageBox.StandardButton.Yes:
            return
        self.profiles.delete(p.name)
        if self.settings.last_profile == p.name:
            self.settings.last_profile = None
            self.settings.save()
        self.refresh_profiles()

    # ================================================================ ações
    def _run_bg(self, name: str, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self._update_buttons()

        def runner():
            err = None
            try:
                fn()
            except TunnelError as e:
                err = e
            except Exception as e:  # noqa: BLE001
                log.exception("Erro inesperado em %s", name)
                err = e
            self.bridge.task_done.emit(name, err)

        threading.Thread(target=runner, name=f"task-{name}", daemon=True).start()

    def _on_task_done(self, name: str, err) -> None:
        self._busy = False
        self._update_buttons()
        if err:
            log.error("%s", err)
            if self.isVisible():
                QMessageBox.warning(self, APP_NAME, str(err))
            return
        if name == "connect":
            self._after_connect()
        elif name == "selftest" and self._selftest_result is not None:
            r = self._selftest_result
            box = QMessageBox.information if r.ok else QMessageBox.warning
            box(self, "Teste de isolamento",
                f"{r.message}\n\nIP direto (outros apps): {r.direct_ip or '—'}\n"
                f"IP do processo Discord: {r.discord_ip or 'bloqueado / sem resposta'}")

    def connect_selected(self) -> None:
        p = self._selected_profile()
        if not p:
            QMessageBox.information(self, APP_NAME, "Importe e selecione um perfil .conf primeiro.")
            return
        self._connect_profile(p)

    def connect_last(self) -> None:
        name = self.settings.last_profile
        p = self.profiles.get(name) if name else None
        if not p:
            log.warning("Nenhum último perfil salvo.")
            self.show_normal()
            return
        self._connect_profile(p)

    def _connect_profile(self, p: Profile) -> None:
        if not p.valid:
            QMessageBox.warning(self, APP_NAME, f"Perfil inválido: {p.error}")
            return
        if not winproc.is_admin():
            QMessageBox.warning(self, APP_NAME, "Execute o ANTI CANJA como administrador para criar o túnel.")
            return
        self.settings.last_profile = p.name
        self.settings.save()
        self._discord_was_running = discord.status().running
        self._run_bg("connect", lambda: self.tunnel.connect(p))

    def _after_connect(self) -> None:
        if self.tunnel.status.state != State.CONNECTED:
            return
        st = discord.status()
        chan = self.settings.preferred_discord
        if st.running and getattr(self, "_discord_was_running", False):
            mode = self.settings.restart_discord_on_connect
            if mode == "ask":
                box = QMessageBox(self)
                box.setWindowTitle(APP_NAME)
                box.setIcon(QMessageBox.Icon.Question)
                box.setText("O Discord já estava aberto.")
                box.setInformativeText("Conexões abertas antes da VPN continuam fora do túnel. "
                                       "Reiniciar o Discord agora para que tudo passe pela VPN?")
                b_yes = box.addButton("Reiniciar Discord", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Agora não", QMessageBox.ButtonRole.RejectRole)
                remember = QCheckBox("Lembrar minha escolha")
                box.setCheckBox(remember)
                box.exec()
                restart = box.clickedButton() is b_yes
                if remember.isChecked():
                    self.settings.restart_discord_on_connect = "always" if restart else "never"
                    self.settings.save()
                    self._sync_settings_widgets()
            else:
                restart = mode == "always"
            if restart:
                threading.Thread(target=discord.restart, args=(chan,), daemon=True).start()
            else:
                log.warning("Discord já estava aberto: conexões antigas seguem fora da VPN até reiniciá-lo.")
        elif not st.running and self.settings.launch_discord_after_connect:
            discord.launch(chan)

    def run_selftest(self) -> None:
        st = self.tunnel.status.state
        if st not in (State.CONNECTED, State.BLOCKING):
            QMessageBox.information(self, APP_NAME, "Conecte a VPN (ou ative o bloqueio sem VPN) para testar o isolamento.")
            return
        from anticanja.core import selftest

        self._selftest_result = None
        self._pings: dict = {}
        self._pinging = False
        self._connect_best_after_ping = False

        def job():
            self._selftest_result = selftest.run(expect_vpn=(st == State.CONNECTED))

        self._run_bg("selftest", job)

    def disconnect_vpn(self) -> None:
        was_vpn = self.tunnel.status.state in (State.CONNECTED, State.CONNECTING, State.ERROR)

        def job():
            self.tunnel.disconnect()
            self._restart_discord_after_vpn(was_vpn)

        self._run_bg("disconnect", job)

    def _restart_discord_after_vpn(self, was_vpn: bool) -> None:
        """Ao sair da VPN, reabre o Discord para ele reconectar pela internet normal na hora."""
        if not (was_vpn and self.settings.restart_discord_on_disconnect):
            return
        if discord.status().running:
            discord.restart(self.settings.preferred_discord, reason="voltar à internet normal")

    def open_discord(self) -> None:
        if discord.status().running:
            log.info("O Discord já está aberto.")
            return
        if not discord.installed_channels():
            QMessageBox.warning(self, APP_NAME, "Discord não encontrado neste usuário do Windows.")
            return
        discord.launch(self.settings.preferred_discord)

    def _startup_actions(self, autostarted: bool) -> None:
        if self.settings.check_updates and updater.enabled():
            QTimer.singleShot(4000, lambda: self.check_updates(manual=False))
        if not (self.settings.auto_connect and self.settings.auto_connect_best_ping):
            self.run_pings()
        if self.settings.kill_switch_when_disconnected:
            self._run_bg("idle-policy", self.tunnel.apply_idle_policy)
            QTimer.singleShot(1500, lambda: self._maybe_autoconnect())
        else:
            self._maybe_autoconnect()

    def _maybe_autoconnect(self) -> None:
        if self.settings.auto_connect and self.settings.auto_connect_best_ping and self._profiles_cache:
            if self._busy or self._pinging:
                QTimer.singleShot(1000, self._maybe_autoconnect)
                return
            log.info("Conexão automática no servidor de menor ping…")
            self.connect_best()
            return
        if self.settings.auto_connect and self.settings.last_profile:
            if self._busy:
                QTimer.singleShot(1000, self._maybe_autoconnect)
                return
            log.info("Conexão automática com o último perfil (%s).", self.settings.last_profile)
            self.connect_last()

    # ================================================================ atualizações
    def check_updates(self, manual: bool = False) -> None:
        from anticanja import UPDATE_REPO

        if not UPDATE_REPO:
            if manual:
                QMessageBox.information(self, APP_NAME, "Atualizações automáticas ainda não configuradas "
                                        "(repositório do GitHub não definido nesta versão).")
            return
        if not updater.enabled() and manual:
            QMessageBox.information(self, APP_NAME, "A atualização só funciona na versão instalada (.exe).")
            return
        log.info("Verificando atualizações…")

        def job():
            try:
                self.bridge.update_checked.emit(updater.check(), None, manual)
            except updater.UpdateError as e:
                self.bridge.update_checked.emit(None, e, manual)

        threading.Thread(target=job, name="update-check", daemon=True).start()

    def _on_update_checked(self, rel, err, manual: bool) -> None:
        if err:
            log.warning("Verificação de atualização falhou: %s", err)
            if manual:
                QMessageBox.warning(self, APP_NAME, f"Não foi possível verificar atualizações:\n{err}")
            return
        if rel is None:
            log.info("Você está na versão mais recente (%s).", __version__)
            if manual:
                QMessageBox.information(self, APP_NAME, f"Você já está na versão mais recente ({__version__}).")
            return
        log.info("Nova versão disponível: %s.", rel.version)
        if not manual and self.settings.skipped_version == rel.version:
            return
        if self.settings.auto_install_updates and not manual:
            self._start_update_download(rel)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Atualização disponível")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"<b>ANTI CANJA {html.escape(rel.version)}</b> está disponível (você tem {__version__}).")
        notes = rel.notes[:1500] if rel.notes else "Sem notas de versão."
        box.setInformativeText("O que mudou:\n" + notes)
        b_now = box.addButton("Atualizar agora", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Depois", QMessageBox.ButtonRole.RejectRole)
        b_skip = box.addButton("Pular esta versão", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is b_now:
            self._start_update_download(rel)
        elif box.clickedButton() is b_skip:
            self.settings.skipped_version = rel.version
            self.settings.save()

    def _start_update_download(self, rel) -> None:
        from PySide6.QtWidgets import QProgressDialog

        self._upd_cancel = False
        dlg = QProgressDialog(f"Baixando ANTI CANJA {rel.version}…", "Cancelar", 0, 100, self)
        dlg.setWindowTitle("Atualizando")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(lambda: setattr(self, "_upd_cancel", True))
        dlg.setValue(0)
        dlg.show()
        self._upd_dialog = dlg
        log.info("Baixando atualização %s…", rel.version)

        def prog(done, total):
            self.bridge.update_progress.emit(done, total)
            return not self._upd_cancel

        def job():
            try:
                self.bridge.update_downloaded.emit(updater.download(rel, prog), None)
            except updater.UpdateError as e:
                self.bridge.update_downloaded.emit(None, e)

        threading.Thread(target=job, name="update-download", daemon=True).start()

    def _on_update_progress(self, done: int, total: int) -> None:
        d = self._upd_dialog
        if not d:
            return
        mb = 1024 * 1024
        if total:
            d.setMaximum(100)
            d.setValue(int(done * 100 / total))
            d.setLabelText(f"Baixando atualização… {done / mb:.1f} de {total / mb:.1f} MB")
        else:
            d.setMaximum(0)
            d.setLabelText(f"Baixando atualização… {done / mb:.1f} MB")

    def _on_update_downloaded(self, zip_path, err) -> None:
        if self._upd_dialog:
            self._upd_dialog.close()
            self._upd_dialog = None
        if err:
            log.error("Atualização falhou: %s", err)
            if "cancelado" not in str(err):
                QMessageBox.warning(self, APP_NAME, f"A atualização falhou:\n{err}\n\nO programa continua na versão atual.")
            return
        try:
            updater.install_and_restart(zip_path)
        except updater.UpdateError as e:
            log.error("Atualização falhou: %s", e)
            QMessageBox.warning(self, APP_NAME, f"A atualização falhou:\n{e}")
            return
        log.info("Reiniciando para concluir a atualização…")
        self._quit_now()

    # ================================================================ settings
    def _sync_settings_widgets(self) -> None:
        s = self.settings
        for w in self._settings_widgets:
            w.blockSignals(True)
        self.c_startup.setChecked(autostart.is_enabled())
        self.c_restart_off.setChecked(s.restart_discord_on_disconnect)
        self.c_upd.setChecked(s.check_updates)
        self.c_upd_auto.setChecked(s.auto_install_updates)
        self.c_autoconnect.setChecked(s.auto_connect)
        self.c_auto_best.setChecked(s.auto_connect_best_ping)
        self.c_launch_discord.setChecked(s.launch_discord_after_connect)
        self.c_tray.setChecked(s.minimize_to_tray)
        self.c_start_min.setChecked(s.start_minimized)
        self.c_killswitch.setChecked(s.kill_switch)
        self.c_block_idle.setChecked(s.kill_switch_when_disconnected)
        self.c_dns.setChecked(s.protect_dns)
        self.c_strict.setChecked(s.strict_route)
        self.cb_restart.setCurrentIndex(max(0, self.cb_restart.findData(s.restart_discord_on_connect)))
        self.cb_channel.setCurrentIndex(max(0, self.cb_channel.findData(s.preferred_discord)))
        for w in self._settings_widgets:
            w.blockSignals(False)

    def _save_settings(self, *_args) -> None:
        s = self.settings
        old_block, old_dns, old_strict = s.kill_switch_when_disconnected, s.protect_dns, s.strict_route
        s.restart_discord_on_disconnect = self.c_restart_off.isChecked()
        s.check_updates = self.c_upd.isChecked()
        s.auto_install_updates = self.c_upd_auto.isChecked()
        s.auto_connect = self.c_autoconnect.isChecked()
        s.auto_connect_best_ping = self.c_auto_best.isChecked()
        s.launch_discord_after_connect = self.c_launch_discord.isChecked()
        s.minimize_to_tray = self.c_tray.isChecked()
        s.start_minimized = self.c_start_min.isChecked()
        s.kill_switch = self.c_killswitch.isChecked()
        s.kill_switch_when_disconnected = self.c_block_idle.isChecked()
        s.protect_dns = self.c_dns.isChecked()
        s.strict_route = self.c_strict.isChecked()
        s.restart_discord_on_connect = self.cb_restart.currentData()
        s.preferred_discord = self.cb_channel.currentData()
        want_startup = self.c_startup.isChecked()
        s.start_with_windows = want_startup
        s.save()

        if want_startup != autostart.is_enabled():
            ok = autostart.enable() if want_startup else autostart.disable()
            if not ok:
                self._sync_settings_widgets()
        if old_block != s.kill_switch_when_disconnected:
            self._run_bg("idle-policy", self.tunnel.apply_idle_policy)
        if (old_dns, old_strict) != (s.protect_dns, s.strict_route) and self.tunnel.status.state == State.CONNECTED:
            log.info("Alteração de DNS/rota será aplicada na próxima conexão.")

    # ================================================================ janela
    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        import traceback

        log.info("Janela fechada (espontânea=%s).", e.spontaneous())
        log.debug("closeEvent stack:\n%s", "".join(traceback.format_stack(limit=12)))
        if self._quitting:
            e.accept()
            return
        if self.settings.minimize_to_tray and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore()
            self.hide()
            if self.tunnel.status.state == State.CONNECTED:
                self.tray.showMessage(APP_NAME, "A VPN do Discord continua ligada. Para desligar, use "
                                      "Desconectar ou clique com o botão direito no ícone → Sair.",
                                      QSystemTrayIcon.MessageIcon.Information, 5000)
            elif not self.settings.tray_hint_shown:
                self.tray.showMessage(APP_NAME, "Continuo rodando na bandeja. Clique com o botão direito para sair.",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
                self.settings.tray_hint_shown = True
                self.settings.save()
            return
        e.ignore()
        self.quit_app()

    def quit_app(self) -> None:
        import traceback

        log.info("Sair solicitado.")
        log.debug("quit_app stack:\n%s", "".join(traceback.format_stack(limit=12)))
        if self.tunnel.status.state == State.CONNECTED:
            if self.isVisible() and QMessageBox.question(
                self, APP_NAME, "A VPN está conectada. Desconectar e sair?") != QMessageBox.StandardButton.Yes:
                return
        self._quit_now()

    def _quit_now(self) -> None:
        self._quitting = True
        log.info("Encerrando…")
        was_vpn = self.tunnel.status.state in (State.CONNECTED, State.CONNECTING, State.ERROR)
        self.monitor.stop()
        try:
            self.tunnel.shutdown()
            self._restart_discord_after_vpn(was_vpn)
        except Exception:  # noqa: BLE001
            log.exception("Erro ao encerrar")
        finally:
            self.tray.hide()
            QApplication.instance().quit()
