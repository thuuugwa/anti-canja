"""Identidade visual do ANTI CANJA — vermelho e preto."""

BG = "#0a0607"
SIDEBAR = "#0d0809"
SURFACE = "#140c0e"
SURFACE_2 = "#1c1214"
BORDER = "#3a1a1f"
BORDER_SOFT = "#26161a"
RED = "#e3172b"
RED_HOVER = "#ff2b40"
RED_DIM = "#5a0f18"
RED_GLOW = "#ff3347"
GREEN = "#33d17a"
AMBER = "#f5a524"
TEXT = "#f4eeef"
MUTED = "#a3969a"

# Mantidos por compatibilidade com o restante do código
PURPLE = RED
PURPLE_SOFT = RED_HOVER

STATE_COLORS = {
    "Desconectado": RED,
    "Conectando…": AMBER,
    "Túnel ativo": GREEN,
    "Discord bloqueado (kill-switch)": RED,
    "Erro": RED,
}

QSS = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QWidget#root, QStackedWidget, QWidget#page {{ background: {BG}; }}
QWidget#sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a0a0d, stop:0.55 {SIDEBAR}, stop:1 #200a0e);
    border-right: 1px solid {BORDER_SOFT};
}}
QWidget#footer {{ background: #080506; border-top: 1px solid {BORDER_SOFT}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}

QFrame#card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #170d10, stop:1 #100a0b);
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#hero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1b0b0f, stop:0.7 #120a0c, stop:1 #1a0a0d);
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#alert {{ background: #2a0b10; border: 1px solid {RED}; border-radius: 10px; }}
QFrame#sep {{ background: {BORDER}; max-width: 1px; min-width: 1px; }}
QFrame#hsep {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

QLabel#heroTitle {{ font-size: 24pt; font-weight: 800; }}
QLabel#cardTitle {{ font-size: 12.5pt; font-weight: 700; }}
QLabel#bigState {{ font-size: 20pt; font-weight: 800; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#infoKey {{ font-size: 10.5pt; }}
QLabel#infoVal {{ color: {MUTED}; }}
QLabel#stateLabel {{ font-size: 13pt; font-weight: 700; }}
QLabel#brand {{ color: {RED}; font-weight: 800; letter-spacing: 0.5px; }}
QLabel#pageTitle {{ font-size: 18pt; font-weight: 800; }}

QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 9px 14px;
    font-weight: 600;
    text-align: center;
}}
QPushButton:hover {{ background: #26171a; border-color: {RED_DIM}; }}
QPushButton:pressed {{ background: #160d0f; }}
QPushButton:disabled {{ color: #6b5a5f; background: #120b0d; border-color: #221417; }}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f0233a, stop:1 #b30f20);
    border: 2px solid {RED_GLOW}; font-size: 13pt; font-weight: 700; padding: 14px 18px; border-radius: 12px;
}}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff3a50, stop:1 #c81427); }}
QPushButton#primary:disabled {{ background: #2a1014; border-color: #3a1a1f; color: #8a6a70; }}
QPushButton#danger {{
    background: #140a0c; border: 2px solid #3a1a1f; font-size: 13pt; font-weight: 700; padding: 14px 18px; border-radius: 12px;
}}
QPushButton#danger:enabled {{ border-color: {RED}; }}
QPushButton#danger:hover {{ background: #2a0e13; }}
QPushButton#big {{
    background: #120a0c; border: 2px solid {RED_DIM}; font-size: 13pt; font-weight: 700; padding: 14px 18px; border-radius: 12px;
}}
QPushButton#big:hover {{ border-color: {RED}; }}
QPushButton#tool {{ padding: 10px 12px; font-size: 10pt; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {RED}; color: {RED_HOVER}; padding: 5px 10px; font-weight: 600; }}
QPushButton#ghost:hover {{ background: #2a0b10; }}
QPushButton#iconOnly {{ padding: 8px; min-width: 22px; }}

QPushButton#nav {{
    background: transparent; border: none; border-radius: 12px; text-align: left;
    padding: 12px 16px; font-size: 11.5pt; font-weight: 600;
}}
QPushButton#nav:hover {{ background: #1f0d11; }}
QPushButton#nav:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c41226, stop:1 #7a0b17);
    border: 1px solid {RED_GLOW};
}}

QLineEdit {{
    background: #0e0809; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px 8px 34px;
}}
QLineEdit:focus {{ border-color: {RED}; }}

QTableWidget {{
    background: transparent; border: none; gridline-color: transparent;
    selection-background-color: transparent; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 8px; border: none; border-bottom: 1px solid {BORDER_SOFT}; }}
QTableWidget::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7a0b17, stop:1 #3a0a12);
}}
QHeaderView::section {{
    background: #120a0c; color: {MUTED}; border: none; padding: 8px; font-weight: 600; font-size: 10pt;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

QPlainTextEdit {{
    background: transparent; border: none;
    font-family: "Cascadia Mono", "Consolas", monospace; font-size: 9.5pt;
}}
QCheckBox {{ spacing: 10px; padding: 4px 0; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; background: #1e1215; border: 1px solid #4a2228; }}
QCheckBox::indicator:checked {{ background: {RED}; border-color: {RED}; }}
QComboBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; min-width: 160px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {RED_DIM}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #4a2a30; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {RED_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QScrollArea {{ background: transparent; border: none; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RED_DIM}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMessageBox, QDialog, QInputDialog {{ background: {SURFACE}; }}
"""
