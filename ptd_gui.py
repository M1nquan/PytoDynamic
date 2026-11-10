import os
import shutil
import sys
from pathlib import Path
from typing import Optional, List, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QDragEnterEvent, QDropEvent, QFont, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
    QHeaderView, QStatusBar
)

import ptd

STUDIO_MODERN_STYLE = """
QMainWindow, QDialog {
    background-color: #0f1115;
}
QWidget {
    color: #abb2bf;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Inter', Roboto, sans-serif;
    font-size: 13px;
}

/* Header & Containers */
QFrame#TopHeader {
    background-color: #161920;
    border-bottom: 1px solid #232731;
}
QFrame#ConfigContainer {
    background-color: #12151b;
    border-right: 1px solid #21252d;
}
QFrame#GroupCard {
    background-color: #181c24;
    border: 1px solid #252a35;
    border-radius: 6px;
}

QLabel#BrandTitle {
    font-size: 14px;
    font-weight: 700;
    color: #e6edf3;
    letter-spacing: 0.5px;
}
QLabel#SectionHeading {
    font-size: 11px;
    font-weight: 700;
    color: #4f9cf9;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
QLabel#FieldLabel {
    font-size: 12px;
    font-weight: 500;
    color: #7d8799;
}

/* Inputs */
QLineEdit {
    background-color: #0d0f14;
    border: 1px solid #282e3b;
    border-radius: 4px;
    color: #e6edf3;
    padding: 7px 10px;
    font-size: 12.5px;
    selection-background-color: #264f78;
}
QLineEdit:hover {
    border: 1px solid #3b4457;
}
QLineEdit:focus {
    border: 1px solid #4f9cf9;
    background-color: #0a0c10;
}

QComboBox, QSpinBox {
    background-color: #0d0f14;
    border: 1px solid #282e3b;
    border-radius: 4px;
    color: #e6edf3;
    padding: 6px 10px;
    font-size: 12px;
}
QComboBox:hover, QSpinBox:hover {
    border: 1px solid #3b4457;
}
QComboBox:focus, QSpinBox:focus {
    border: 1px solid #4f9cf9;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #20242e;
}
QComboBox QAbstractItemView {
    background-color: #181c24;
    border: 1px solid #2a313f;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    padding: 4px;
}

/* Buttons */
QPushButton {
    background-color: #212631;
    border: 1px solid #2e3545;
    border-radius: 4px;
    color: #e6edf3;
    font-weight: 500;
    font-size: 12.5px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #2a3140;
    border-color: #414b60;
}
QPushButton:pressed {
    background-color: #1a1e27;
}
QPushButton:disabled {
    background-color: #14171d;
    border-color: #1e222b;
    color: #4b5263;
}

QPushButton#PrimaryBuildBtn {
    background-color: #238636;
    border: 1px solid #2ea043;
    color: #ffffff;
    font-weight: 600;
    padding: 7px 18px;
}
QPushButton#PrimaryBuildBtn:hover {
    background-color: #2ea043;
    border-color: #3fb950;
}
QPushButton#PrimaryBuildBtn:pressed {
    background-color: #1f722e;
}
QPushButton#PrimaryBuildBtn:disabled {
    background-color: #17241b;
    border-color: #1d3323;
    color: #3b5a41;
}

/* Checkboxes */
QCheckBox {
    color: #c9d1d9;
    font-size: 12px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #2d3444;
    border-radius: 3px;
    background-color: #0d0f14;
}
QCheckBox::indicator:hover {
    border-color: #4f9cf9;
}
QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #388bfd;
}

/* Terminal & Console */
QTextEdit#Terminal {
    background-color: #08090c;
    border: 1px solid #1c202a;
    border-radius: 4px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
    line-height: 1.5;
    padding: 12px;
}

QProgressBar {
    background-color: #0d0f14;
    border: none;
    height: 2px;
}
QProgressBar::chunk {
    background-color: #4f9cf9;
}

/* ScrollBar */
QScrollBar:vertical {
    background-color: #08090c;
    width: 8px;
}
QScrollBar::handle:vertical {
    background-color: #202531;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background-color: #32394a;
}
"""

def detect_python_home() -> str:
    try:
        sdk = ptd.locate_verified_sdk(None)
        return str(sdk.home)
    except Exception:
        pass
    base = Path(sys.base_prefix).resolve()
    if (base / "include" / "Python.h").is_file():
        return str(base)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        p_dir = Path(local_app) / "Programs" / "Python"
        if p_dir.is_dir():
            for p in sorted(p_dir.glob("Python3*"), reverse=True):
                if (p / "include" / "Python.h").is_file():
                    return str(p.resolve())
    return str(base)

def detect_toolchain_bin() -> str:
    gcc = shutil.which("g++")
    if gcc:
        return str(Path(gcc).resolve().parent)
    cl = shutil.which("cl")
    if cl:
        return str(Path(cl).resolve().parent)
    candidates = [
        r"C:\winlibs-x86_64-posix-seh-gcc-16.2.0-mingw-w64ucrt-14.0.0-r3\mingw64\bin",
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        r"C:\MinGW\bin"
    ]
    for c in candidates:
        if (Path(c) / "g++.exe").is_file():
            return c
    return ""

class BuildWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params

    def run(self):
        try:
            ok, res = ptd.compile_engine(
                src=self.params["src"],
                out=self.params["out"],
                name=self.params["name"],
                embed=self.params["embed"],
                compiler=self.params["compiler"],
                py_home=self.params["py_home"],
                optimize=self.params["optimize"],
                debug=self.params["debug"],
                keep_cpp=self.params["keep_cpp"],
                add_path=self.params["add_path"],
                max_payload_mb=self.params["max_payload_mb"],
                drain_timeout_ms=self.params["drain_timeout_ms"],
                log_cb=self.log_signal.emit
            )
            self.finished_signal.emit(ok, res)
        except Exception as exc:
            self.finished_signal.emit(False, f"Exception: {exc}")

class SelftestWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, list)

    def __init__(self, dll_path: Path):
        super().__init__()
        self.dll_path = dll_path

    def run(self):
        records = []
        def intercept_log(msg: str):
            self.log_signal.emit(msg)
            if msg.startswith("[PASS]") or msg.startswith("[FAIL]") or msg.startswith("[SKIP]"):
                parts = msg.split(":", 1)
                st = parts[0][1:5]
                desc = parts[1].strip() if len(parts) > 1 else msg
                records.append((st, desc))

        try:
            ok, res = ptd.selftest_full(self.dll_path, log_cb=intercept_log)
            self.finished_signal.emit(ok, res, records)
        except Exception as exc:
            self.finished_signal.emit(False, f"Crash: {exc}", records)

class PeInspectorDialog(QDialog):
    def __init__(self, dll_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"PE/ABI Directory — {dll_path.name}")
        self.resize(680, 500)
        self.setStyleSheet(STUDIO_MODERN_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        meta_frame = QFrame()
        meta_frame.setObjectName("GroupCard")
        m_layout = QGridLayout(meta_frame)
        m_layout.setContentsMargins(14, 12, 14, 12)
        m_layout.setHorizontalSpacing(16)
        m_layout.setVerticalSpacing(6)

        arch = ptd.parse_pe_architecture(dll_path) or "Unknown"
        m_layout.addWidget(QLabel("Binary Architecture:"), 0, 0)
        lbl_a = QLabel(arch)
        lbl_a.setStyleSheet("color: #4f9cf9; font-weight: 600;")
        m_layout.addWidget(lbl_a, 0, 1)

        sz_kb = dll_path.stat().st_size / 1024.0
        m_layout.addWidget(QLabel("Artifact Size:"), 1, 0)
        m_layout.addWidget(QLabel(f"{sz_kb:.2f} KB ({dll_path.stat().st_size:,} bytes)"), 1, 1)

        m_layout.addWidget(QLabel("Location:"), 2, 0)
        lbl_p = QLabel(str(dll_path))
        lbl_p.setWordWrap(True)
        lbl_p.setStyleSheet("color: #7d8799; font-size: 11px;")
        m_layout.addWidget(lbl_p, 2, 1)
        layout.addWidget(meta_frame)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Exported Symbol", "Domain", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(
            "QTableWidget { background-color: #0d0f14; border: 1px solid #252a35; border-radius: 4px; }"
            "QHeaderView::section { background-color: #161920; color: #7d8799; padding: 6px; font-weight: 600; border: none; border-bottom: 1px solid #252a35; }"
        )

        _, exported_symbols = ptd.verify_pe_exports(dll_path, ptd.REQUIRED_ABI_SYMBOLS)
        self.table.setRowCount(len(ptd.REQUIRED_ABI_SYMBOLS))

        for row, req in enumerate(ptd.REQUIRED_ABI_SYMBOLS):
            item_name = QTableWidgetItem(req)
            item_name.setFont(QFont("Cascadia Code", 9.5))

            cat = "Lifecycle" if "init" in req or "shutdown" in req else ("Memory" if "free" in req else ("Protocol" if "abi" in req or "author" in req else "Invocation"))
            item_cat = QTableWidgetItem(cat)

            is_present = req in exported_symbols
            item_st = QTableWidgetItem("VERIFIED" if is_present else "MISSING")
            item_st.setForeground(QColor("#2ea043") if is_present else QColor("#f85149"))
            item_st.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_cat)
            self.table.setItem(row, 2, item_st)

        layout.addWidget(self.table)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

class TestMatrixDialog(QDialog):
    def __init__(self, records: List[Tuple[str, str]], summary: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verification Suite Results")
        self.resize(720, 520)
        self.setStyleSheet(STUDIO_MODERN_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Status", "Verification Item"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet(
            "QTableWidget { background-color: #0d0f14; border: 1px solid #252a35; border-radius: 4px; }"
            "QHeaderView::section { background-color: #161920; color: #7d8799; padding: 6px; font-weight: 600; border: none; border-bottom: 1px solid #252a35; }"
        )

        self.table.setRowCount(len(records))
        for row, (st, desc) in enumerate(records):
            item_st = QTableWidgetItem(f" {st} ")
            item_st.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            if st == "PASS":
                item_st.setForeground(QColor("#2ea043"))
            elif st == "FAIL":
                item_st.setForeground(QColor("#f85149"))
            else:
                item_st.setForeground(QColor("#7d8799"))

            item_desc = QTableWidgetItem(desc)
            self.table.setItem(row, 0, item_st)
            self.table.setItem(row, 1, item_desc)

        layout.addWidget(self.table)
        lbl_sum = QLabel(summary)
        lbl_sum.setStyleSheet("color: #abb2bf; font-size: 12px;")
        layout.addWidget(lbl_sum)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

class PtdWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PTD Engine Studio v1.0")
        self.resize(1200, 780)
        self.setAcceptDrops(True)
        self.last_dll: Optional[Path] = None
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet(STUDIO_MODERN_STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_header = QFrame()
        top_header.setObjectName("TopHeader")
        h_layout = QHBoxLayout(top_header)
        h_layout.setContentsMargins(18, 12, 18, 12)
        h_layout.setSpacing(12)

        lbl_brand = QLabel("PTD ENGINE STUDIO")
        lbl_brand.setObjectName("BrandTitle")
        h_layout.addWidget(lbl_brand)

        is64 = sys.maxsize > 2**32
        lbl_tag = QLabel(f"Python {sys.version.split()[0]} ({'x64' if is64 else 'x86'})")
        lbl_tag.setStyleSheet("color: #7d8799; font-size: 11.5px; margin-left: 6px;")
        h_layout.addWidget(lbl_tag)
        h_layout.addStretch()

        self.btn_inspect = QPushButton("Inspect Binary")
        self.btn_inspect.setEnabled(False)
        self.btn_inspect.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_inspect.clicked.connect(self.open_inspector)

        self.btn_test = QPushButton("Verify Artifact")
        self.btn_test.setEnabled(False)
        self.btn_test.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_test.clicked.connect(self.run_selftest)

        self.btn_build = QPushButton("Compile DLL")
        self.btn_build.setObjectName("PrimaryBuildBtn")
        self.btn_build.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_build.clicked.connect(self.start_build)

        h_layout.addWidget(self.btn_inspect)
        h_layout.addWidget(self.btn_test)
        h_layout.addWidget(self.btn_build)
        root.addWidget(top_header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #21252d; }")

        left_widget = QWidget()
        left_widget.setStyleSheet("background-color: #12151b;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(18, 16, 18, 16)
        left_layout.setSpacing(14)

        c_io = QFrame()
        c_io.setObjectName("GroupCard")
        l_io = QVBoxLayout(c_io)
        l_io.setContentsMargins(14, 12, 14, 14)
        l_io.setSpacing(8)

        lbl_s1 = QLabel("Target Specification")
        lbl_s1.setObjectName("SectionHeading")
        l_io.addWidget(lbl_s1)

        l_io.addWidget(QLabel("Python Source (.py):"))
        h_src = QHBoxLayout()
        self.txt_src = QLineEdit()
        self.txt_src.setPlaceholderText("Select script or drag & drop here...")
        self.txt_src.textChanged.connect(self.on_src_changed)
        btn_src = QPushButton("Browse")
        btn_src.clicked.connect(self.browse_src)
        h_src.addWidget(self.txt_src, stretch=4)
        h_src.addWidget(btn_src, stretch=1)
        l_io.addLayout(h_src)

        l_io.addWidget(QLabel("Output Binary (.dll):"))
        h_dst = QHBoxLayout()
        self.txt_dest = QLineEdit()
        self.txt_dest.setPlaceholderText("Destination file path...")
        btn_dst = QPushButton("Browse")
        btn_dst.clicked.connect(self.browse_dest)
        h_dst.addWidget(self.txt_dest, stretch=4)
        h_dst.addWidget(btn_dst, stretch=1)
        l_io.addLayout(h_dst)
        left_layout.addWidget(c_io)

        c_env = QFrame()
        c_env.setObjectName("GroupCard")
        l_env = QVBoxLayout(c_env)
        l_env.setContentsMargins(14, 12, 14, 14)
        l_env.setSpacing(8)

        lbl_s2 = QLabel("Toolchain & SDK")
        lbl_s2.setObjectName("SectionHeading")
        l_env.addWidget(lbl_s2)

        l_env.addWidget(QLabel("Python Development SDK Root:"))
        h_py = QHBoxLayout()
        self.txt_py_home = QLineEdit()
        self.txt_py_home.setText(detect_python_home())
        btn_py = QPushButton("Browse")
        btn_py.clicked.connect(self.browse_py_home)
        h_py.addWidget(self.txt_py_home, stretch=4)
        h_py.addWidget(btn_py, stretch=1)
        l_env.addLayout(h_py)

        l_env.addWidget(QLabel("Compiler Binary Path (bin):"))
        h_tc = QHBoxLayout()
        self.txt_path = QLineEdit()
        self.txt_path.setText(detect_toolchain_bin())
        btn_tc = QPushButton("Browse")
        btn_tc.clicked.connect(self.browse_compiler_path)
        h_tc.addWidget(self.txt_path, stretch=4)
        h_tc.addWidget(btn_tc, stretch=1)
        l_env.addLayout(h_tc)
        left_layout.addWidget(c_env)

        c_flags = QFrame()
        c_flags.setObjectName("GroupCard")
        l_flags = QVBoxLayout(c_flags)
        l_flags.setContentsMargins(14, 12, 14, 14)
        l_flags.setSpacing(10)

        lbl_s3 = QLabel("Build Options")
        lbl_s3.setObjectName("SectionHeading")
        l_flags.addWidget(lbl_s3)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Compiler:"), 0, 0)
        self.cb_compiler = QComboBox()
        self.cb_compiler.addItems(["auto", "gcc", "msvc"])
        grid.addWidget(self.cb_compiler, 0, 1)

        grid.addWidget(QLabel("Opt Level:"), 0, 2)
        self.sp_opt = QSpinBox()
        self.sp_opt.setRange(0, 2)
        self.sp_opt.setValue(0)
        grid.addWidget(self.sp_opt, 0, 3)

        grid.addWidget(QLabel("Max Payload:"), 1, 0)
        self.sp_payload = QSpinBox()
        self.sp_payload.setRange(1, 100)
        self.sp_payload.setValue(64)
        self.sp_payload.setSuffix(" MB")
        grid.addWidget(self.sp_payload, 1, 1)

        grid.addWidget(QLabel("Drain Timeout:"), 1, 2)
        self.sp_drain = QSpinBox()
        self.sp_drain.setRange(100, 30000)
        self.sp_drain.setValue(3000)
        self.sp_drain.setSingleStep(500)
        self.sp_drain.setSuffix(" ms")
        grid.addWidget(self.sp_drain, 1, 3)
        l_flags.addLayout(grid)

        v_chk = QVBoxLayout()
        v_chk.setSpacing(6)
        self.chk_embed = QCheckBox("In-Memory PTDPAY01 Envelope (Standalone)")
        self.chk_embed.setChecked(True)
        self.chk_debug = QCheckBox("Compile With Debug Symbols (/Zi, -g)")
        self.chk_keep_cpp = QCheckBox("Retain C++ Bridge Source (.cpp)")

        v_chk.addWidget(self.chk_embed)
        v_chk.addWidget(self.chk_debug)
        v_chk.addWidget(self.chk_keep_cpp)
        l_flags.addLayout(v_chk)
        left_layout.addWidget(c_flags)
        left_layout.addStretch()

        right_widget = QWidget()
        right_widget.setStyleSheet("background-color: #0f1115;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.setSpacing(10)

        h_out_top = QHBoxLayout()
        lbl_out = QLabel("Build Telemetry & Diagnostics")
        lbl_out.setObjectName("SectionHeading")
        h_out_top.addWidget(lbl_out)
        h_out_top.addStretch()

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(lambda: self.console.clear())
        h_out_top.addWidget(btn_clear)
        right_layout.addLayout(h_out_top)

        self.pbar = QProgressBar()
        self.pbar.setTextVisible(False)
        self.pbar.setRange(0, 0)
        self.pbar.hide()
        right_layout.addWidget(self.pbar)

        self.console = QTextEdit()
        self.console.setObjectName("Terminal")
        self.console.setReadOnly(True)
        right_layout.addWidget(self.console)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        root.addWidget(splitter)

        self.statusBar = QStatusBar()
        self.statusBar.setStyleSheet("background-color: #0a0c0f; color: #7d8799; border-top: 1px solid #1c202a; padding: 2px 12px;")
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.toLocalFile().lower().endswith((".py", ".dll")) for u in urls):
                event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for u in event.mimeData().urls():
            f_path = u.toLocalFile()
            if f_path.lower().endswith(".py"):
                self.txt_src.setText(f_path)
                break
            elif f_path.lower().endswith(".dll"):
                self.last_dll = Path(f_path)
                self.txt_dest.setText(f_path)
                self.btn_inspect.setEnabled(True)
                self.btn_test.setEnabled(True)
                self.append_log(f"[*] Loaded binary artifact: {f_path}")
                break

    def on_src_changed(self, text: str):
        src_path = text.strip()
        if src_path and src_path.lower().endswith(".py"):
            p = Path(src_path)
            if not self.txt_dest.text() or self.txt_dest.text().endswith(".dll"):
                self.txt_dest.setText(str(p.with_suffix(".dll")))

    def append_log(self, text: str):
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setFontFamily("Cascadia Code")
        fmt.setFontPointSize(9.5)

        lines = text.split("\n")
        for line in lines:
            if line.startswith("[+]"):
                fmt.setForeground(QColor("#2ea043"))
            elif line.startswith("[*]"):
                fmt.setForeground(QColor("#4f9cf9"))
            elif line.startswith("[!]"):
                fmt.setForeground(QColor("#d29922"))
            elif line.startswith("[FAIL]"):
                fmt.setForeground(QColor("#f85149"))
            elif line.startswith("[PASS]"):
                fmt.setForeground(QColor("#2ea043"))
            elif line.startswith("[SKIP]"):
                fmt.setForeground(QColor("#7d8799"))
            else:
                fmt.setForeground(QColor("#c9d1d9"))

            cursor.insertText(line + "\n", fmt)

        self.console.setTextCursor(cursor)
        self.console.ensureCursorVisible()

    def browse_src(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Script", "", "Python (*.py)")
        if f:
            self.txt_src.setText(f)

    def browse_dest(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save Library", self.txt_dest.text(), "Dynamic Link Libraries (*.dll)")
        if f:
            self.txt_dest.setText(f)

    def browse_py_home(self):
        d = QFileDialog.getExistingDirectory(self, "Python SDK Root")
        if d:
            self.txt_py_home.setText(d)

    def browse_compiler_path(self):
        d = QFileDialog.getExistingDirectory(self, "Toolchain Binary Path")
        if d:
            self.txt_path.setText(d)

    def start_build(self):
        src = self.txt_src.text().strip()
        out = self.txt_dest.text().strip()
        py_home = self.txt_py_home.text().strip() or None

        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "Validation Error", "Target script path is invalid.")
            return
        if not out:
            QMessageBox.warning(self, "Validation Error", "Output path is undefined.")
            return

        self.console.clear()
        self.btn_build.setEnabled(False)
        self.btn_inspect.setEnabled(False)
        self.btn_test.setEnabled(False)
        self.pbar.show()
        self.statusBar.showMessage("Building native artifact...")

        params = {
            "src": src,
            "out": out,
            "name": None,
            "embed": self.chk_embed.isChecked(),
            "compiler": ptd.Compiler(self.cb_compiler.currentText()),
            "py_home": Path(py_home) if py_home else None,
            "optimize": self.sp_opt.value(),
            "debug": self.chk_debug.isChecked(),
            "keep_cpp": self.chk_keep_cpp.isChecked(),
            "add_path": self.txt_path.text().strip() or None,
            "max_payload_mb": self.sp_payload.value(),
            "drain_timeout_ms": self.sp_drain.value(),
        }

        self.worker = BuildWorker(params)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.build_finished)
        self.worker.start()

    def build_finished(self, ok: bool, res: str):
        self.btn_build.setEnabled(True)
        self.pbar.hide()
        if ok:
            self.last_dll = Path(res)
            self.btn_inspect.setEnabled(True)
            self.btn_test.setEnabled(True)
            self.append_log(f"\n[+] Build successful: {res}")
            self.statusBar.showMessage("Compilation complete.")
            QMessageBox.information(self, "Success", f"Build succeeded:\n{res}")
        else:
            self.append_log(f"\n[!] Compilation failed:\n{res}")
            self.statusBar.showMessage("Compilation failed.")
            QMessageBox.critical(self, "Build Error", "Compilation aborted. Check telemetry log.")

    def open_inspector(self):
        if self.last_dll and self.last_dll.is_file():
            dlg = PeInspectorDialog(self.last_dll, self)
            dlg.exec()

    def run_selftest(self):
        if self.last_dll and self.last_dll.is_file():
            self.append_log("\n[*] Running 20-dimension verification suite...")
            self.btn_build.setEnabled(False)
            self.btn_inspect.setEnabled(False)
            self.btn_test.setEnabled(False)
            self.pbar.show()
            self.statusBar.showMessage("Running verification...")

            self.test_worker = SelftestWorker(self.last_dll)
            self.test_worker.log_signal.connect(self.append_log)
            self.test_worker.finished_signal.connect(self.selftest_finished)
            self.test_worker.start()

    def selftest_finished(self, ok: bool, summary: str, records: list):
        self.btn_build.setEnabled(True)
        self.btn_inspect.setEnabled(True)
        self.btn_test.setEnabled(True)
        self.pbar.hide()
        self.statusBar.showMessage("Verification complete.")

        if records:
            dlg = TestMatrixDialog(records, summary, self)
            dlg.exec()
        else:
            if ok:
                QMessageBox.information(self, "Verification", summary)
            else:
                QMessageBox.warning(self, "Verification", summary)

def main():
    app = QApplication(sys.argv)
    win = PtdWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()