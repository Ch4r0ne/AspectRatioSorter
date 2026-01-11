from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
from PyQt6.QtCore import QObject, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QSpinBox,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


SUPPORTED_IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SUPPORTED_VIDEO_EXT = {".mp4", ".mov"}


@dataclass(frozen=True)
class SortConfig:
    directory: Path
    normalize_lowercase: bool = True  # entspricht deinem Script (filename.lower())
    recursive: bool = False           # dein Script ist nicht rekursiv -> default False
    max_workers: int = 8              # Threadpool-Größe (im Worker genutzt)


@dataclass
class SortStats:
    total_files: int = 0
    processed: int = 0
    moved_portrait: int = 0
    moved_landscape: int = 0
    skipped_unsupported: int = 0
    failed: int = 0


def classify_orientation(path: Path) -> Tuple[str, int, int]:
    """
    Returns: ("portrait"|"landscape", width, height)
    Raises: RuntimeError on read failures.
    """
    ext = path.suffix.lower()

    if ext in SUPPORTED_VIDEO_EXT:
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                raise RuntimeError("cv2.VideoCapture konnte Datei nicht öffnen")

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            if width <= 0 or height <= 0:
                raise RuntimeError(f"Ungültige Video-Dimensionen: {width}x{height}")

        finally:
            cap.release()

    elif ext in SUPPORTED_IMAGE_EXT:
        im = cv2.imread(str(path))
        if im is None:
            raise RuntimeError("cv2.imread konnte Bild nicht lesen")

        height, width = im.shape[:2]
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Ungültige Bild-Dimensionen: {width}x{height}")

    else:
        raise RuntimeError("UNSUPPORTED")

    aspect_ratio = width / height
    folder = "portrait" if aspect_ratio < 1 else "landscape"
    return folder, width, height


def ensure_folder(base_dir: Path, subfolder: str) -> Path:
    target_dir = base_dir / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def unique_destination_path(dest_dir: Path, filename: str) -> Path:
    """
    If dest exists, add suffix: 'name (1).ext', 'name (2).ext', ...
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    ext = Path(filename).suffix
    i = 1
    while True:
        candidate = dest_dir / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate
        i += 1


def move_file(src: Path, dest: Path) -> None:
    """
    Move via rename; fallback for edge cases.
    """
    try:
        src.rename(dest)
    except OSError:
        # Fallback (rare in same folder, but safe)
        import shutil
        shutil.move(str(src), str(dest))


class SortWorker(QObject):
    progress = pyqtSignal(int, int)  # processed, total
    log = pyqtSignal(str)
    finished = pyqtSignal(object)    # SortStats
    failed = pyqtSignal(str)

    def __init__(self, config: SortConfig, cancel_event: threading.Event):
        super().__init__()
        self.config = config
        self.cancel_event = cancel_event

    def run(self) -> None:
        stats = SortStats()
        base = self.config.directory

        if not base.exists() or not base.is_dir():
            self.failed.emit("Der gewählte Pfad existiert nicht oder ist kein Ordner.")
            return

        # Datei-Liste (wie dein Script: nur Dateien direkt im Ordner, keine Unterordner)
        if self.config.recursive:
            all_files = [p for p in base.rglob("*") if p.is_file()]
        else:
            all_files = [p for p in base.iterdir() if p.is_file()]

        stats.total_files = len(all_files)
        self.log.emit(f"Ordner: {base}")
        self.log.emit(f"Dateien gefunden: {stats.total_files}")
        self.log.emit("Erstelle Zielordner (landscape/portrait) falls nötig ...")
        ensure_folder(base, "landscape")
        ensure_folder(base, "portrait")

        self.progress.emit(0, stats.total_files)

        # Parallelisierung: OpenCV I/O ist oft GIL-frei, Threads helfen.
        # Cancel ist "best effort": laufende Reads stoppen nicht sofort.
        import concurrent.futures

        def _process_one(path: Path) -> Tuple[Path, Optional[Path], str]:
            """
            Returns: (src, dest or None, status)
            status: "MOVED_L", "MOVED_P", "SKIP", "FAIL:<msg>"
            """
            if self.cancel_event.is_set():
                return path, None, "SKIP"

            ext = path.suffix.lower()
            if ext not in SUPPORTED_IMAGE_EXT and ext not in SUPPORTED_VIDEO_EXT:
                return path, None, "SKIP"

            try:
                folder, w, h = classify_orientation(path)
                dest_dir = ensure_folder(base, folder)

                # deinem Script folgend: filename wird lowercased
                filename = path.name.lower() if self.config.normalize_lowercase else path.name

                # Kollisionen abfangen (dein Script würde hier ggf. crashen -> UX besser)
                dest_path = unique_destination_path(dest_dir, filename)

                move_file(path, dest_path)

                if folder == "portrait":
                    return path, dest_path, f"MOVED_P:{w}x{h}"
                else:
                    return path, dest_path, f"MOVED_L:{w}x{h}"

            except RuntimeError as e:
                if str(e) == "UNSUPPORTED":
                    return path, None, "SKIP"
                return path, None, f"FAIL:{e}"

            except Exception as e:
                return path, None, f"FAIL:{e}"

        max_workers = max(1, int(self.config.max_workers))
        self.log.emit(f"Starte Verarbeitung (Threads: {max_workers}) ...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_process_one, p) for p in all_files]

            for fut in concurrent.futures.as_completed(futures):
                if self.cancel_event.is_set():
                    # versuche pending futures zu canceln (nur wenn noch nicht gestartet)
                    for f in futures:
                        f.cancel()
                    self.log.emit("Abbruch angefordert. Beende so schnell wie möglich ...")
                    break

                src, dest, status = fut.result()

                stats.processed += 1
                if status == "SKIP":
                    stats.skipped_unsupported += 1
                elif status.startswith("MOVED_P"):
                    stats.moved_portrait += 1
                    self.log.emit(f"PORTRAIT  -> {src.name}  ({status.split(':',1)[1]})")
                elif status.startswith("MOVED_L"):
                    stats.moved_landscape += 1
                    self.log.emit(f"LANDSCAPE -> {src.name}  ({status.split(':',1)[1]})")
                elif status.startswith("FAIL:"):
                    stats.failed += 1
                    self.log.emit(f"FEHLER    -> {src.name}: {status.split(':',1)[1]}")
                else:
                    # defensive
                    stats.failed += 1
                    self.log.emit(f"FEHLER    -> {src.name}: Unbekannter Status")

                self.progress.emit(stats.processed, stats.total_files)

        self.finished.emit(stats)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Sorter (Portrait/Landscape) – PyQt6")
        self.setMinimumSize(920, 600)

        self.settings = QSettings("TimTools", "MediaSorterPyQt6")

        self.cancel_event = threading.Event()
        self.thread: Optional[QThread] = None
        self.worker: Optional[SortWorker] = None

        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        # Folder selection
        folder_box = QGroupBox("Ordner auswählen")
        folder_layout = QGridLayout(folder_box)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"z.B. C:\Users\Username\Documents\MediaFolder")
        self.path_edit.setClearButtonEnabled(True)

        browse_btn = QPushButton("Durchsuchen…")
        browse_btn.clicked.connect(self._browse)

        self.start_btn = QPushButton("Start")
        self.start_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_btn.clicked.connect(self._start)

        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)

        folder_layout.addWidget(QLabel("Ordner:"), 0, 0)
        folder_layout.addWidget(self.path_edit, 0, 1)
        folder_layout.addWidget(browse_btn, 0, 2)
        folder_layout.addWidget(self.start_btn, 1, 1)
        folder_layout.addWidget(self.cancel_btn, 1, 2)

        layout.addWidget(folder_box)

        # Options
        opt_box = QGroupBox("Optionen")
        opt_layout = QHBoxLayout(opt_box)

        self.lowercase_cb = QCheckBox("Dateinamen in Zielordnern in lower-case umbenennen (wie Script)")
        self.lowercase_cb.setChecked(True)

        self.recursive_cb = QCheckBox("Rekursiv (Unterordner mit einschließen)")
        self.recursive_cb.setChecked(False)

        self.threads_spin = QSpinBox()
        self.threads_spin.setMinimum(1)
        self.threads_spin.setMaximum(64)
        self.threads_spin.setValue(min(8, (os.cpu_count() or 8)))
        self.threads_spin.setToolTip("Threadpool-Größe für parallele Verarbeitung")

        opt_layout.addWidget(self.lowercase_cb)
        opt_layout.addWidget(self.recursive_cb)
        opt_layout.addWidget(QLabel("Threads:"))
        opt_layout.addWidget(self.threads_spin)
        opt_layout.addStretch(1)

        layout.addWidget(opt_box)

        # Progress + status
        prog_box = QGroupBox("Fortschritt")
        prog_layout = QVBoxLayout(prog_box)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)

        self.status_label = QLabel("Bereit.")
        prog_layout.addWidget(self.progress)
        prog_layout.addWidget(self.status_label)

        layout.addWidget(prog_box)

        # Log
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)

        clear_btn = QToolButton()
        clear_btn.setText("Log leeren")
        clear_btn.clicked.connect(self.log_edit.clear)

        log_layout.addWidget(self.log_edit)
        log_layout.addWidget(clear_btn)

        layout.addWidget(log_box)

        # Menu
        exit_action = QAction("Beenden", self)
        exit_action.triggered.connect(self.close)
        menu = self.menuBar().addMenu("Datei")
        menu.addAction(exit_action)

        # Drag & drop for folder paths
        self.setAcceptDrops(True)

    def _load_settings(self) -> None:
        last_dir = self.settings.value("last_dir", "", type=str)
        if last_dir:
            self.path_edit.setText(last_dir)

        self.lowercase_cb.setChecked(self.settings.value("lowercase", True, type=bool))
        self.recursive_cb.setChecked(self.settings.value("recursive", False, type=bool))
        self.threads_spin.setValue(self.settings.value("threads", self.threads_spin.value(), type=int))

    def _save_settings(self) -> None:
        self.settings.setValue("last_dir", self.path_edit.text().strip())
        self.settings.setValue("lowercase", self.lowercase_cb.isChecked())
        self.settings.setValue("recursive", self.recursive_cb.isChecked())
        self.settings.setValue("threads", self.threads_spin.value())

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Ordner auswählen", self.path_edit.text().strip() or str(Path.home()))
        if d:
            self.path_edit.setText(d)

    def _start(self) -> None:
        if self.thread is not None:
            return

        directory_str = self.path_edit.text().strip()
        if not directory_str:
            QMessageBox.warning(self, "Fehlt", "Bitte einen Ordner auswählen.")
            return

        directory = Path(directory_str)

        config = SortConfig(
            directory=directory,
            normalize_lowercase=self.lowercase_cb.isChecked(),
            recursive=self.recursive_cb.isChecked(),
            max_workers=self.threads_spin.value(),
        )

        self._save_settings()
        self.cancel_event.clear()

        self.log_edit.appendPlainText("====================================================")
        self.log_edit.appendPlainText("Start…")
        self.status_label.setText("Läuft…")
        self.progress.setValue(0)

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self.thread = QThread()
        self.worker = SortWorker(config, self.cancel_event)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._on_log)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)

        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_thread)

        self.thread.start()

    def _cancel(self) -> None:
        if self.thread is None:
            return
        self.cancel_event.set()
        self.status_label.setText("Abbruch angefordert…")
        self.cancel_btn.setEnabled(False)

    def _cleanup_thread(self) -> None:
        self.thread = None
        self.worker = None
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, processed: int, total: int) -> None:
        if total <= 0:
            self.progress.setValue(0)
            return
        pct = int((processed / total) * 100)
        self.progress.setValue(pct)
        self.status_label.setText(f"Verarbeitet: {processed}/{total} ({pct}%)")

    def _on_log(self, msg: str) -> None:
        self.log_edit.appendPlainText(msg)

    def _on_failed(self, msg: str) -> None:
        self.log_edit.appendPlainText(f"ABBRUCH: {msg}")
        self.status_label.setText("Fehler.")
        QMessageBox.critical(self, "Fehler", msg)

    def _on_finished(self, stats: SortStats) -> None:
        self.log_edit.appendPlainText("")
        self.log_edit.appendPlainText("FERTIG.")
        self.log_edit.appendPlainText(
            f"Gesamt: {stats.total_files} | "
            f"Processed: {stats.processed} | "
            f"Portrait: {stats.moved_portrait} | "
            f"Landscape: {stats.moved_landscape} | "
            f"Skipped: {stats.skipped_unsupported} | "
            f"Failed: {stats.failed}"
        )
        self.status_label.setText("Fertig.")
        QMessageBox.information(
            self,
            "Fertig",
            "Sortierung abgeschlossen.\n\n"
            f"Portrait: {stats.moved_portrait}\n"
            f"Landscape: {stats.moved_landscape}\n"
            f"Skipped: {stats.skipped_unsupported}\n"
            f"Failed: {stats.failed}"
        )

    # Drag & Drop (Ordnerpfad)
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        p = Path(urls[0].toLocalFile())
        if p.is_dir():
            self.path_edit.setText(str(p))

    def closeEvent(self, event):
        # Bei laufendem Job: sauber abbrechen
        if self.thread is not None:
            self.cancel_event.set()
            # Thread wird beendet sobald Worker zurückkommt
        self._save_settings()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
