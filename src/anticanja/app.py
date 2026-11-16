"""Ponto de entrada: instância única, elevação, tema e janela principal."""
from __future__ import annotations

import argparse
import sys

from anticanja import APP_ID, APP_NAME
from anticanja.core import logbus, winproc
from anticanja.core.settings import Settings

SERVER_NAME = f"{APP_ID}-singleton"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="anticanja", add_help=True)
    ap.add_argument("--autostart", action="store_true", help="iniciado pelo Windows (logon)")
    ap.add_argument("--minimized", action="store_true", help="iniciar na bandeja")
    ap.add_argument("--no-elevate", action="store_true", help="não pedir elevação (modo diagnóstico)")
    ap.add_argument("--updated", action="store_true", help="aberto pelo atualizador")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if winproc.IS_WINDOWS and not winproc.is_admin() and not args.no_elevate:
        if winproc.relaunch_as_admin():
            return 0
        # Usuário recusou o UAC: segue sem poder conectar (a interface avisa).

    if winproc.IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.App")
        except Exception:  # noqa: BLE001
            pass

    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # ---- instância única: a segunda apenas pede para a primeira aparecer
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        logbus.setup()
        logbus.get("app").info("Outra instância já está aberta; trazendo a janela dela para frente.")
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return 0
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    logbus.setup()
    _install_crash_hooks()

    from anticanja.ui import theme
    from anticanja.ui.main_window import MainWindow

    app.setStyleSheet(theme.QSS)
    settings = Settings.load()
    win = MainWindow(settings, autostarted=args.autostart)
    if args.updated:
        from anticanja import __version__

        logbus.get("update").info("ANTI CANJA atualizado para a versão %s.", __version__)

    def on_new_connection():
        c = server.nextPendingConnection()
        if c:
            c.readyRead.connect(lambda: (c.readAll(), win.show_normal()))

    server.newConnection.connect(on_new_connection)

    start_hidden = args.minimized or args.autostart or settings.start_minimized
    if not start_hidden:
        win.show()

    app.aboutToQuit.connect(lambda: logbus.get("app").info("Aplicativo encerrando (aboutToQuit)."))
    code = app.exec()
    logbus.get("app").info("Loop principal terminou (código %s).", code)
    server.close()
    return code


def _install_crash_hooks() -> None:
    """Registra QUALQUER encerramento inesperado no arquivo de log."""
    import atexit
    import faulthandler
    import threading
    import traceback

    from anticanja.core import paths

    log = logbus.get("crash")
    try:
        fh = open(paths.logs_dir() / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(fh, all_threads=True)
        _install_crash_hooks._fh = fh  # manter aberto
    except OSError:
        pass

    def _exc(t, v, tb):
        log.error("Exceção não tratada: %s", "".join(traceback.format_exception(t, v, tb)))

    def _thread_exc(args):
        log.error("Exceção em thread %s: %s", args.thread.name if args.thread else "?",
                  "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = _exc
    threading.excepthook = _thread_exc
    atexit.register(lambda: log.info("Processo finalizando (atexit)."))
    if sys.stderr is None:  # app empacotado sem console: evita falhas ao escrever em stderr
        import io
        sys.stderr = io.StringIO()
        sys.stdout = io.StringIO()


if __name__ == "__main__":
    sys.exit(main())
