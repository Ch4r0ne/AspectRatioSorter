from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

SUPPORTED_IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SUPPORTED_VIDEO_EXT = {".mp4", ".mov"}


def _sanitize_folder_name(name: str) -> str:
    name = (name or "").strip()
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return name


def _compute_output_root(source_dir: Path, output_name: str) -> Path:
    out = _sanitize_folder_name(output_name)
    return source_dir if out == "" else (source_dir / out)


def _ensure_output_dirs(output_root: Path) -> Tuple[Path, Path]:
    portrait = output_root / "portrait"
    landscape = output_root / "landscape"
    portrait.mkdir(parents=True, exist_ok=True)
    landscape.mkdir(parents=True, exist_ok=True)
    return portrait, landscape


def _is_supported(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in SUPPORTED_IMAGE_EXT or ext in SUPPORTED_VIDEO_EXT


def _classify_dimensions(p: Path) -> Tuple[str, int, int]:
    ext = p.suffix.lower()

    if ext in SUPPORTED_VIDEO_EXT:
        cap = cv2.VideoCapture(str(p))
        try:
            if not cap.isOpened():
                raise RuntimeError("Could not open video.")
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w <= 0 or h <= 0:
                raise RuntimeError(f"Invalid video dimensions: {w}x{h}")
            return "video", w, h
        finally:
            cap.release()

    if ext in SUPPORTED_IMAGE_EXT:
        im = cv2.imread(str(p))
        if im is None:
            raise RuntimeError("Could not read image.")
        h, w = im.shape[:2]
        if w <= 0 or h <= 0:
            raise RuntimeError(f"Invalid image dimensions: {w}x{h}")
        return "image", w, h

    raise RuntimeError("Unsupported format.")


def _orientation(w: int, h: int) -> str:
    return "portrait" if (w / h) < 1 else "landscape"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _enumerate_files(source_dir: Path, recursive: bool, exclude_dirs: List[Path]) -> List[Path]:
    def allowed(p: Path) -> bool:
        for ex in exclude_dirs:
            if _is_under(p, ex):
                return False
        return True

    if not recursive:
        return [p for p in source_dir.iterdir() if p.is_file()]

    out: List[Path] = []
    for p in source_dir.rglob("*"):
        if not p.is_file():
            continue
        if allowed(p):
            out.append(p)
    return out


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, ext = dest.stem, dest.suffix
    parent = dest.parent
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){ext}"
        if not cand.exists():
            return cand
        i += 1


def _move_file(src: Path, dest: Path, overwrite: bool) -> None:
    if overwrite and dest.exists() and dest.is_file():
        dest.unlink()
    try:
        src.rename(dest)
    except OSError:
        import shutil
        if overwrite and dest.exists() and dest.is_file():
            dest.unlink()
        shutil.move(str(src), str(dest))


@dataclass(frozen=True)
class AppConfig:
    source_dir: Path
    output_name: str
    recursive: bool
    lowercase: bool
    dry_run: bool
    dup_mode: str  # auto_rename | skip | overwrite
    remember_settings: bool


@dataclass(frozen=True)
class PreviewItem:
    src: Path
    kind: str
    width: int
    height: int
    orientation: str
    dest: Path
    status: str


@dataclass
class RunStats:
    found: int = 0
    supported: int = 0
    portrait: int = 0
    landscape: int = 0
    skipped_unsupported: int = 0
    skipped_duplicates: int = 0
    errors: int = 0
    moved: int = 0


class PreviewTableModel(QAbstractTableModel):
    HEADERS = ["File", "Type", "WxH", "Class", "Destination", "Status"]

    def __init__(self):
        super().__init__()
        self._items: List[PreviewItem] = []

    def set_items(self, items: List[PreviewItem]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        it = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return it.src.name
            if col == 1:
                return it.kind
            if col == 2:
                return f"{it.width}x{it.height}" if it.width and it.height else "-"
            if col == 3:
                return it.orientation
            if col == 4:
                return "-" if str(it.dest) == "-" else f"{it.dest.parent.name}\\{it.dest.name}"
            if col == 5:
                return it.status

        if role == Qt.ItemDataRole.ForegroundRole and col == 5:
            if it.status.startswith("ERROR"):
                return QColor(220, 130, 130)
            if it.status.startswith("SKIP"):
                return QColor(170, 170, 170)
            return QColor(200, 200, 200)

        return None


class AnalyzerWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list, object)
    failed = pyqtSignal(str)

    def __init__(self, cfg: AppConfig, cancel_event: threading.Event):
        super().__init__()
        self.cfg = cfg
        self.cancel_event = cancel_event

    def run(self) -> None:
        src = self.cfg.source_dir
        if not src.exists() or not src.is_dir():
            self.failed.emit("Source does not exist or is not a folder.")
            return

        out_root = _compute_output_root(src, self.cfg.output_name)
        portrait_dir, landscape_dir = _ensure_output_dirs(out_root)

        exclude_dirs: List[Path] = []
        if out_root != src:
            exclude_dirs.append(out_root)
        exclude_dirs.append(out_root / "portrait")
        exclude_dirs.append(out_root / "landscape")

        files = _enumerate_files(src, self.cfg.recursive, exclude_dirs)
        stats = RunStats(found=len(files))
        preview: List[PreviewItem] = []

        self.progress.emit(0, stats.found)
        done = 0

        for p in files:
            if self.cancel_event.is_set():
                break

            if not _is_supported(p):
                stats.skipped_unsupported += 1
                done += 1
                self.progress.emit(done, stats.found)
                continue

            try:
                kind, w, h = _classify_dimensions(p)
                orient = _orientation(w, h)

                stats.supported += 1
                if orient == "portrait":
                    stats.portrait += 1
                    dest_dir = portrait_dir
                else:
                    stats.landscape += 1
                    dest_dir = landscape_dir

                out_name = p.name.lower() if self.cfg.lowercase else p.name
                dest = dest_dir / out_name

                if dest.exists():
                    if self.cfg.dup_mode == "skip":
                        stats.skipped_duplicates += 1
                        preview.append(PreviewItem(p, kind, w, h, orient, dest, "SKIP (duplicate)"))
                        done += 1
                        self.progress.emit(done, stats.found)
                        continue
                    if self.cfg.dup_mode == "auto_rename":
                        dest = _unique_dest(dest)

                status = "OK"
                if self.cfg.dup_mode == "overwrite" and (dest_dir / out_name).exists():
                    status = "OK (overwrite)"

                preview.append(PreviewItem(p, kind, w, h, orient, dest, status))

            except Exception as e:
                stats.errors += 1
                preview.append(PreviewItem(p, "?", 0, 0, "?", Path("-"), f"ERROR: {e}"))

            done += 1
            self.progress.emit(done, stats.found)

        self.finished.emit(preview, stats)


class SortWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, cfg: AppConfig, items: List[PreviewItem], cancel_event: threading.Event):
        super().__init__()
        self.cfg = cfg
        self.items = items
        self.cancel_event = cancel_event

    def run(self) -> None:
        ok_items = [x for x in self.items if x.status.startswith("OK")]
        if not ok_items:
            self.failed.emit("No sortable items. Run Analyze first.")
            return

        src = self.cfg.source_dir
        out_root = _compute_output_root(src, self.cfg.output_name)
        portrait_dir, landscape_dir = _ensure_output_dirs(out_root)

        total = len(ok_items)
        stats = RunStats()
        self.progress.emit(0, total)

        moved = 0
        for it in ok_items:
            if self.cancel_event.is_set():
                break

            dest_dir = portrait_dir if it.orientation == "portrait" else landscape_dir
            out_name = it.src.name.lower() if self.cfg.lowercase else it.src.name
            dest = dest_dir / out_name

            if dest.exists():
                if self.cfg.dup_mode == "skip":
                    stats.skipped_duplicates += 1
                    continue
                if self.cfg.dup_mode == "auto_rename":
                    dest = _unique_dest(dest)

            if not self.cfg.dry_run:
                try:
                    _move_file(it.src, dest, overwrite=(self.cfg.dup_mode == "overwrite"))
                except Exception:
                    stats.errors += 1
                    continue

            moved += 1
            stats.moved = moved
            self.progress.emit(moved, total)

        self.finished.emit(stats)


def _apply_business_dark(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(24, 24, 24))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(235, 235, 235))
    pal.setColor(QPalette.ColorRole.Base, QColor(18, 18, 18))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(28, 28, 28))
    pal.setColor(QPalette.ColorRole.Text, QColor(235, 235, 235))
    pal.setColor(QPalette.ColorRole.Button, QColor(42, 42, 42))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(235, 235, 235))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(90, 90, 90))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    app.setStyleSheet("""
        QWidget { font-size: 10pt; }
        QGroupBox { font-weight: 600; }
        QLineEdit, QTableView, QProgressBar, QTreeWidget, QComboBox {
            border: 1px solid #3a3a3a;
            border-radius: 8px;
        }
        QLineEdit, QComboBox { padding: 6px; }
        QHeaderView::section {
            background: #2a2a2a;
            border: 1px solid #3a3a3a;
            padding: 6px;
            font-weight: 700;
        }
        QPushButton {
            padding: 7px 12px;
            border: 1px solid #3a3a3a;
            border-radius: 8px;
            background: #2b2b2b;
        }
        QPushButton:hover { border: 1px solid #5a5a5a; background: #303030; }
        QPushButton:pressed { background: #262626; }
        QPushButton:disabled { color: #9a9a9a; background: #242424; border: 1px solid #2f2f2f; }
        QProgressBar { text-align: center; }
        QProgressBar::chunk { background-color: #6a6a6a; }
        QToolTip {
            color: #f0f0f0;
            background-color: #202020;
            border: 1px solid #4a4a4a;
            padding: 6px;
        }
    """)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AspectRatioSorter")
        self.resize(1320, 760)

        self.settings = QSettings("TimTools", "AspectRatioSorter_BusinessClean")
        self.cancel_event = threading.Event()

        self.thread: Optional[QThread] = None
        self.worker: Optional[QObject] = None

        self.preview_items: List[PreviewItem] = []
        self.preview_cfg: Optional[AppConfig] = None

        self.model = PreviewTableModel()

        self._build_ui()
        self._load()
        self._update_tree()
        self._set_stats(RunStats())
        self._refresh()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        header = QFrame()
        hl = QHBoxLayout(header)

        name = QLabel("AspectRatioSorter")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        name.setFont(f)

        micro = QLabel("Analyze → Preview → Sort")
        micro.setStyleSheet("color: #cfcfcf;")
        micro.setToolTip("Analyze builds the preview table. Sort moves supported files into the output structure.")

        hl.addWidget(name)
        hl.addStretch(1)
        hl.addWidget(micro)
        main.addWidget(header)

        setup = QGroupBox("Setup")
        gl = QGridLayout(setup)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Source folder")
        self.source_edit.setClearButtonEnabled(True)
        self.source_edit.setToolTip("Folder that contains your images/videos.")
        self.source_edit.textChanged.connect(self._on_any_change)

        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setToolTip("Select the source folder.")
        self.btn_browse.clicked.connect(self._browse)

        self.btn_open_src = QPushButton("Open")
        self.btn_open_src.setToolTip("Open the source folder in Explorer.")
        self.btn_open_src.clicked.connect(self._open_source)

        self.output_edit = QLineEdit("")
        self.output_edit.setPlaceholderText("Output folder (optional)")
        self.output_edit.setToolTip("Leave empty to sort directly inside the source folder (creates portrait/landscape there).")
        self.output_edit.textChanged.connect(self._on_any_change)

        for b in (self.btn_browse, self.btn_open_src):
            b.setMinimumHeight(34)
            b.setFixedWidth(110)

        gl.addWidget(QLabel("Source"), 0, 0)
        gl.addWidget(self.source_edit, 0, 1)
        gl.addWidget(self.btn_browse, 0, 2)
        gl.addWidget(self.btn_open_src, 0, 3)

        gl.addWidget(QLabel("Output"), 1, 0)
        gl.addWidget(self.output_edit, 1, 1)

        main.addWidget(setup)

        body = QHBoxLayout()
        main.addLayout(body, 1)

        left = QVBoxLayout()
        body.addLayout(left, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setToolTip("Folder structure that will be created.")
        left.addWidget(self.tree, 1)

        options = QGroupBox("Options")
        ol = QGridLayout(options)

        self.cb_dry = QCheckBox("Dry-run")
        self.cb_dry.setToolTip("No files are moved. Use this to validate the preview.")
        self.cb_dry.stateChanged.connect(self._on_any_change)

        self.cb_lower = QCheckBox("Lower-case filenames")
        self.cb_lower.setToolTip("Renames moved files to lower-case (matches the original script).")
        self.cb_lower.setChecked(True)
        self.cb_lower.stateChanged.connect(self._on_any_change)

        self.cb_recursive = QCheckBox("Recursive")
        self.cb_recursive.setToolTip("Include subfolders. Destination folders are excluded automatically.")
        self.cb_recursive.stateChanged.connect(self._on_any_change)

        self.cb_remember = QCheckBox("Remember settings")
        self.cb_remember.setToolTip("Stores your selections for the next start.")
        self.cb_remember.setChecked(True)
        self.cb_remember.stateChanged.connect(self._save)

        self.dup_combo = QComboBox()
        self.dup_combo.addItem("Duplicates: Auto-rename", "auto_rename")
        self.dup_combo.addItem("Duplicates: Skip", "skip")
        self.dup_combo.addItem("Duplicates: Overwrite", "overwrite")
        self.dup_combo.setToolTip(
            "Auto-rename: keeps both files (adds a suffix).\n"
            "Skip: leaves existing destination file unchanged.\n"
            "Overwrite: replaces existing destination file."
        )
        self.dup_combo.currentIndexChanged.connect(self._on_any_change)

        ol.addWidget(self.cb_dry, 0, 0)
        ol.addWidget(self.cb_lower, 0, 1)
        ol.addWidget(self.cb_recursive, 1, 0)
        ol.addWidget(self.cb_remember, 1, 1)
        ol.addWidget(self.dup_combo, 2, 0, 1, 2)

        left.addWidget(options)

        actions = QGroupBox("Actions")
        al = QHBoxLayout(actions)

        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.setToolTip("Scan files and build the preview table.")
        self.btn_sort = QPushButton("Sort")
        self.btn_sort.setToolTip("Move files using the last preview configuration.")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setToolTip("Cancel the running task.")
        self.btn_cancel.setEnabled(False)

        for b in (self.btn_analyze, self.btn_sort, self.btn_cancel):
            b.setMinimumHeight(34)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btn_analyze.clicked.connect(self._analyze)
        self.btn_sort.clicked.connect(self._sort)
        self.btn_cancel.clicked.connect(self._cancel)

        al.addWidget(self.btn_analyze)
        al.addWidget(self.btn_sort)
        al.addWidget(self.btn_cancel)

        left.addWidget(actions)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        left.addWidget(self.progress)

        self.stats = QLabel("Found: 0  |  Supported: 0  |  Portrait: 0  |  Landscape: 0  |  Skipped: 0  |  Errors: 0  |  Moved: 0")
        self.stats.setStyleSheet("color: #cfcfcf;")
        left.addWidget(self.stats)

        right = QVBoxLayout()
        body.addLayout(right, 2)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table, 1)

        self.setAcceptDrops(True)

    def _cfg(self) -> AppConfig:
        return AppConfig(
            source_dir=Path(self.source_edit.text().strip()),
            output_name=self.output_edit.text(),
            recursive=self.cb_recursive.isChecked(),
            lowercase=self.cb_lower.isChecked(),
            dry_run=self.cb_dry.isChecked(),
            dup_mode=str(self.dup_combo.currentData()),
            remember_settings=self.cb_remember.isChecked(),
        )

    def _invalidate_preview(self) -> None:
        self.preview_items = []
        self.preview_cfg = None
        self.model.set_items([])
        self.progress.setValue(0)
        self._set_stats(RunStats())

    def _on_any_change(self) -> None:
        if self.thread is None:
            self._invalidate_preview()
        self._save()
        self._update_tree()
        self._refresh()

    def _load(self) -> None:
        self.source_edit.setText(self.settings.value("source", "", type=str))
        self.output_edit.setText(self.settings.value("output", "", type=str))
        self.cb_dry.setChecked(self.settings.value("dry", False, type=bool))
        self.cb_lower.setChecked(self.settings.value("lower", True, type=bool))
        self.cb_recursive.setChecked(self.settings.value("recursive", False, type=bool))
        self.cb_remember.setChecked(self.settings.value("remember", True, type=bool))

        dup = self.settings.value("dup", "auto_rename", type=str)
        idx = self.dup_combo.findData(dup)
        if idx >= 0:
            self.dup_combo.setCurrentIndex(idx)

    def _save(self) -> None:
        if not self.cb_remember.isChecked():
            return
        self.settings.setValue("source", self.source_edit.text().strip())
        self.settings.setValue("output", self.output_edit.text())
        self.settings.setValue("dry", self.cb_dry.isChecked())
        self.settings.setValue("lower", self.cb_lower.isChecked())
        self.settings.setValue("recursive", self.cb_recursive.isChecked())
        self.settings.setValue("remember", self.cb_remember.isChecked())
        self.settings.setValue("dup", self.dup_combo.currentData())

    def _update_tree(self) -> None:
        self.tree.clear()
        src_txt = self.source_edit.text().strip()
        src_label = src_txt if src_txt else "Source"

        out_name = _sanitize_folder_name(self.output_edit.text())
        use_source_as_output = (out_name == "")

        src_item = QTreeWidgetItem([src_label])
        self.tree.addTopLevelItem(src_item)

        if use_source_as_output:
            src_item.addChild(QTreeWidgetItem(["landscape"]))
            src_item.addChild(QTreeWidgetItem(["portrait"]))
        else:
            out_item = QTreeWidgetItem([out_name])
            src_item.addChild(out_item)
            out_item.addChild(QTreeWidgetItem(["landscape"]))
            out_item.addChild(QTreeWidgetItem(["portrait"]))

        self.tree.expandAll()

    def _set_stats(self, s: RunStats) -> None:
        skipped = s.skipped_unsupported + s.skipped_duplicates
        self.stats.setText(
            f"Found: {s.found}  |  Supported: {s.supported}  |  Portrait: {s.portrait}  |  Landscape: {s.landscape}  |  Skipped: {skipped}  |  Errors: {s.errors}  |  Moved: {s.moved}"
        )

    def _refresh(self) -> None:
        running = self.thread is not None
        has_source_text = bool(self.source_edit.text().strip())
        has_preview = len(self.preview_items) > 0 and self.preview_cfg is not None

        self.btn_open_src.setEnabled(has_source_text and not running)
        self.btn_analyze.setEnabled(has_source_text and not running)
        self.btn_sort.setEnabled(has_preview and not running)
        self.btn_cancel.setEnabled(running)

        self.source_edit.setEnabled(not running)
        self.output_edit.setEnabled(not running)
        self.cb_dry.setEnabled(not running)
        self.cb_lower.setEnabled(not running)
        self.cb_recursive.setEnabled(not running)
        self.cb_remember.setEnabled(not running)
        self.dup_combo.setEnabled(not running)
        self.btn_browse.setEnabled(not running)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Source Folder", self.source_edit.text().strip() or str(Path.home()))
        if d:
            self.source_edit.setText(d)

    def _open_source(self) -> None:
        p = Path(self.source_edit.text().strip())
        if p.exists() and p.is_dir():
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _analyze(self) -> None:
        if self.thread is not None:
            return

        cfg = self._cfg()
        if not cfg.source_dir.exists() or not cfg.source_dir.is_dir():
            QMessageBox.warning(self, "Invalid Source", "Select a valid source folder.")
            return

        self._save()
        self.statusBar().showMessage("Analyzing…")
        self.progress.setValue(0)

        self.preview_items = []
        self.preview_cfg = None
        self.model.set_items([])
        self._set_stats(RunStats())

        self.cancel_event.clear()
        self.thread = QThread()
        self.worker = AnalyzerWorker(cfg, self.cancel_event)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_analyze_finished)

        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)

        self.thread.start()
        self._refresh()

    def _sort(self) -> None:
        if self.thread is not None:
            return
        if not self.preview_items or self.preview_cfg is None:
            return

        cfg = self.preview_cfg
        out_root = _compute_output_root(cfg.source_dir, cfg.output_name)

        if not cfg.dry_run:
            res = QMessageBox.question(
                self,
                "Confirm",
                f"Move files into:\n{out_root}\\landscape and {out_root}\\portrait\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return

        self.statusBar().showMessage("Sorting…")
        self.progress.setValue(0)

        self.cancel_event.clear()
        self.thread = QThread()
        self.worker = SortWorker(cfg, self.preview_items, self.cancel_event)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_sort_finished)

        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)

        self.thread.start()
        self._refresh()

    def _cancel(self) -> None:
        if self.thread is None:
            return
        self.cancel_event.set()
        self.statusBar().showMessage("Cancel requested…")

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setValue(0)
            return
        self.progress.setValue(max(0, min(100, int((done / total) * 100))))

    def _on_failed(self, msg: str) -> None:
        self.statusBar().showMessage("Error.")
        QMessageBox.critical(self, "Error", msg)

    def _on_analyze_finished(self, preview: list, stats: object) -> None:
        self.preview_items = list(preview)
        self.preview_cfg = self._cfg()
        self.model.set_items(self.preview_items)
        self.table.resizeColumnsToContents()
        self._set_stats(stats)
        self.statusBar().showMessage("Preview ready.")
        self._refresh()

    def _on_sort_finished(self, stats: object) -> None:
        self._set_stats(stats)
        self.statusBar().showMessage("Done." if not (self.preview_cfg and self.preview_cfg.dry_run) else "Dry-run done.")
        self._refresh()

    def _cleanup(self) -> None:
        self.thread = None
        self.worker = None
        self._refresh()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        p = Path(urls[0].toLocalFile())
        if p.is_dir():
            self.source_edit.setText(str(p))

    def closeEvent(self, event):
        if self.thread is not None:
            self.cancel_event.set()
        self._save()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    _apply_business_dark(app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
