"""Student profile detachable window."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFormLayout, QLabel, QMainWindow, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


class StudentProfileWindow(QMainWindow):
    def __init__(self, container, session, student_id: int, parent=None) -> None:
        super().__init__(parent)
        self.container = container
        self.session = session
        self.student_id = student_id

        self.setWindowTitle(f"Student Profile #{student_id}")
        self.resize(620, 480)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # Header
        form = QFormLayout()
        self.name_lbl = QLabel("…")
        self.id_lbl = QLabel("…")
        self.college_lbl = QLabel("…")
        self.email_lbl = QLabel("…")
        self.phone_lbl = QLabel("…")
        self.status_lbl = QLabel("…")
        form.addRow("Name", self.name_lbl)
        form.addRow("Student ID", self.id_lbl)
        form.addRow("College", self.college_lbl)
        form.addRow("Email", self.email_lbl)
        form.addRow("Phone", self.phone_lbl)
        form.addRow("Housing", self.status_lbl)
        outer.addLayout(form)

        outer.addWidget(QLabel("<b>Bed assignment history</b>"))
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(
            ["When", "Bed", "Effective", "End", "Reason"])
        outer.addWidget(self.history, 1)

        outer.addWidget(QLabel(
            "<b>Change log</b> <i>(immutable, audit-chained)</i>"))
        self.change_log = QTableWidget(0, 4)
        self.change_log.setHorizontalHeaderLabels(
            ["Timestamp (UTC)", "Operator", "Action", "Details"])
        outer.addWidget(self.change_log, 1)

        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh)
        outer.addWidget(btn)

        self.refresh()

    def refresh(self) -> None:
        s = self.container.students.get(self.session, self.student_id)
        self.name_lbl.setText(s.full_name)
        self.id_lbl.setText(s.student_id)
        self.college_lbl.setText(s.college or "—")
        self.email_lbl.setText(s.email or "—")
        self.phone_lbl.setText(s.phone or "—")
        self.status_lbl.setText(s.housing_status)
        history = self.container.housing.assignment_history(
            self.session, student_id=self.student_id)
        self.history.setRowCount(0)
        for h in history:
            i = self.history.rowCount()
            self.history.insertRow(i)
            # Bed-assignment history is itself audit-chained; the audit row's
            # ts becomes the "When" column so reviewers see who acted, when.
            self.history.setItem(i, 0, QTableWidgetItem(
                getattr(h, "created_at", "") or ""))
            self.history.setItem(i, 1, QTableWidgetItem(h.bed_label))
            self.history.setItem(i, 2, QTableWidgetItem(h.effective_date))
            self.history.setItem(i, 3, QTableWidgetItem(h.end_date or ""))
            self.history.setItem(i, 4, QTableWidgetItem(h.reason or ""))

        try:
            entries = self.container.students.history(
                self.session, self.student_id)
        except Exception:
            entries = []
        self.change_log.setRowCount(0)
        # Resolve operator usernames once for the rows we display.
        from backend import db as _db
        actor_ids = {e.actor_id for e in entries if e.actor_id}
        op_names: dict[int, str] = {}
        if actor_ids:
            qmarks = ",".join("?" * len(actor_ids))
            for r in _db.get_connection().execute(
                    f"SELECT id, username FROM users WHERE id IN ({qmarks})",
                    tuple(actor_ids)):
                op_names[r["id"]] = r["username"]
        import json as _json
        for e in entries:
            i = self.change_log.rowCount()
            self.change_log.insertRow(i)
            self.change_log.setItem(i, 0, QTableWidgetItem(e.ts or ""))
            self.change_log.setItem(i, 1, QTableWidgetItem(
                op_names.get(e.actor_id or 0, str(e.actor_id or "—"))))
            self.change_log.setItem(i, 2, QTableWidgetItem(e.action or ""))
            try:
                payload_text = _json.dumps(e.payload, sort_keys=True)
            except Exception:
                payload_text = str(e.payload)
            self.change_log.setItem(i, 3, QTableWidgetItem(payload_text))
