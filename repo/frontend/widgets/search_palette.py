"""Universal search palette (Ctrl+K)."""
from __future__ import annotations
import csv

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QVBoxLayout)


class SearchPalette(QDialog):
    hit_chosen = pyqtSignal(str, int, str)  # entity_type, entity_id, action

    def __init__(self, container, session, parent=None) -> None:
        super().__init__(parent)
        self.container = container
        self.session = session
        self.setWindowTitle("Search")
        self.setMinimumSize(560, 380)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Search students, resources, employers, cases...")
        self.list = QListWidget()
        layout.addWidget(self.input)
        layout.addWidget(self.list)

        # Save / pin / export controls — completes the saved-search UX.
        bar = QHBoxLayout()
        self.save_btn = QPushButton("Save search")
        self.pin_btn = QPushButton("Save && pin to sidebar")
        self.csv_btn = QPushButton("Export results to CSV")
        bar.addWidget(self.save_btn)
        bar.addWidget(self.pin_btn)
        bar.addWidget(self.csv_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self._hits: list = []
        self.save_btn.clicked.connect(lambda: self._save_search(pinned=False))
        self.pin_btn.clicked.connect(lambda: self._save_search(pinned=True))
        self.csv_btn.clicked.connect(self._export_csv)

        self.input.textChanged.connect(self._on_text)
        self.input.returnPressed.connect(self._activate_first)
        self.list.itemActivated.connect(self._on_item_activated)
        self.input.setFocus()

    def _on_text(self, text: str) -> None:
        self.list.clear()
        text = text.strip()
        if len(text) < 2:
            return
        try:
            hits = self.container.search.global_search(self.session, text, limit=25)
        except Exception:
            hits = []
        self._hits = list(hits)
        for h in hits:
            item = QListWidgetItem(f"[{h.entity_type}] {h.title} — {h.subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, (h.entity_type, h.entity_id, h.open_action))
            self.list.addItem(item)

    def _activate_first(self) -> None:
        if self.list.count():
            self._on_item_activated(self.list.item(0))

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        et, eid, action = item.data(Qt.ItemDataRole.UserRole)
        self.hit_chosen.emit(et, eid, action)
        self.accept()

    # ---- saved searches / export -----------------------------------------

    def _save_search(self, *, pinned: bool) -> None:
        from PyQt6.QtWidgets import QInputDialog
        text = self.input.text().strip()
        if not text:
            return
        name, ok = QInputDialog.getText(
            self, "Save search", "Name for this search:", text=text[:40])
        if not ok or not name.strip():
            return
        try:
            sid = self.container.search.save_search(
                self.session, name.strip(), scope="global",
                query={"text": text})
            if pinned:
                self.container.search.pin(self.session, sid, True)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        # Notify the parent so the sidebar refreshes if it's listening.
        parent = self.parent()
        if parent is not None and hasattr(parent, "_refresh_saved_sidebar"):
            try:
                parent._refresh_saved_sidebar()
            except Exception:
                pass

    def _export_csv(self) -> None:
        if not self._hits:
            QMessageBox.information(self, "No results",
                                    "Run a search first, then export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export search results", "search_results.csv",
            "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["entity_type", "entity_id", "title", "subtitle",
                            "score", "action"])
                for h in self._hits:
                    w.writerow([h.entity_type, h.entity_id, h.title,
                                h.subtitle, f"{h.score:.1f}", h.open_action])
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Export done",
                                f"Wrote {len(self._hits)} row(s) to {path}.")
