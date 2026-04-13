"""Main window: tabbed workspace, tray icon, global shortcuts."""
from __future__ import annotations
import sys
from datetime import date

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QListWidget, QListWidgetItem,
                             QMainWindow, QMenu, QMessageBox, QPushButton,
                             QStatusBar, QSystemTrayIcon, QTabWidget, QVBoxLayout,
                             QWidget)

from .dialogs import BootstrapDialog, LoginDialog, UnlockDialog
from .widgets import ResultsTable, SearchPalette
from .windows.student_profile import StudentProfileWindow
from .tabs_extra import (BomTab, CatalogTab, ComplianceExtTab, UpdaterTab)


# ---- Tab widgets -----------------------------------------------------------

class StudentsTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.new_btn = QPushButton("New (Ctrl+Shift+N)")
        self.import_btn = QPushButton("Import CSV…")
        self.export_btn = QPushButton("Export (Ctrl+E)")
        self.unlock_btn = QPushButton("Unlock PII")
        bar.addWidget(self.new_btn)
        bar.addWidget(self.import_btn)
        bar.addWidget(self.export_btn)
        bar.addWidget(self.unlock_btn)
        bar.addStretch()
        outer.addLayout(bar)

        self.table = ResultsTable(["ID", "Name", "College", "Year", "Housing"])
        self.table.add_action("Open profile", self._open_profile)
        self.table.add_action("Assign bed…", self._assign_bed)
        self.table.add_action("View history", self._view_history)
        outer.addWidget(self.table, 1)

        self.new_btn.clicked.connect(self._create_student)
        self.import_btn.clicked.connect(self._import_csv)
        self.export_btn.clicked.connect(self._export_csv)
        self.unlock_btn.clicked.connect(self._unlock_pii)
        self.refresh()

    def refresh(self) -> None:
        rows = self.container.students.search(self.session, limit=500)
        self.table.set_rows([
            [r.student_id, r.full_name, r.college or "", r.class_year or "",
             r.housing_status] for r in rows])

    def _create_student(self) -> None:
        # Pre-fill from any unsaved draft autosaved during a prior crash.
        draft = None
        try:
            draft = self.container.checkpoints.load_draft(
                self.session, "student:new")
        except Exception:
            pass
        sid_default = (draft or {}).get("student_id", "")
        name_default = (draft or {}).get("full_name", "")

        sid, ok = QInputDialog.getText(self, "New student", "Student ID:",
                                       text=sid_default)
        if not ok or not sid.strip():
            return
        # Autosave intermediate draft so a crash between the two prompts is
        # recoverable on next launch.
        try:
            self.container.checkpoints.save_draft(
                self.session, "student:new",
                {"student_id": sid.strip(), "full_name": name_default})
        except Exception:
            pass
        name, ok = QInputDialog.getText(self, "New student", "Full name:",
                                        text=name_default)
        if not ok or not name.strip():
            return
        from backend.models import StudentDTO
        try:
            self.container.students.create(self.session, StudentDTO(
                student_id=sid.strip(), full_name=name.strip()))
        except Exception as e:
            QMessageBox.warning(self, "Create failed", str(e))
            return
        # Successful create — discard the draft.
        try:
            self.container.checkpoints.discard_draft(self.session, "student:new")
        except Exception:
            pass
        self.refresh()

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import students", "",
            "Spreadsheets (*.csv *.xlsx);;CSV files (*.csv);;Excel files (*.xlsx)")
        if not path:
            return
        try:
            preview = self.container.students.import_file(self.session, path)
        except Exception as e:
            QMessageBox.warning(self, "Import error", str(e))
            return
        msg = (f"Preview: {len(preview.accepted)} accepted, "
               f"{len(preview.rejected)} rejected.\nCommit?")
        if QMessageBox.question(self, "Dry-run preview", msg) != QMessageBox.StandardButton.Yes:
            return
        result = self.container.students.commit_import(self.session, preview.preview_id)
        QMessageBox.information(self, "Import done",
                                f"Created: {result['created']}, "
                                f"Updated: {result['updated']}, "
                                f"Skipped: {result['skipped']}, "
                                f"Rejected: {result['rejected']}")
        self.refresh()

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export students", "students.xlsx",
            "Excel files (*.xlsx);;CSV files (*.csv)")
        if not path:
            return
        try:
            n = self.container.students.export_file(self.session, path)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        self.main.statusBar().showMessage(f"Exported {n} rows to {path}", 4000)

    def _unlock_pii(self) -> None:
        dlg = UnlockDialog(self.container, self.session, self)
        if dlg.exec() and dlg.unlocked:
            self.refresh()
            self.main.statusBar().showMessage("PII unlocked for 5 minutes.", 4000)

    def _open_profile(self, row) -> None:
        if not row:
            return
        sid_ext = row[0]
        results = self.container.students.search(self.session, text=sid_ext)
        if not results:
            return
        w = StudentProfileWindow(self.container, self.session, results[0].id, self)
        w.show()
        self.main.detached.append(w)

    def _assign_bed(self, row) -> None:
        if not row:
            return
        beds = [b for b in self.container.housing.list_beds(self.session) if not b.occupied]
        if not beds:
            QMessageBox.information(self, "No beds", "No vacant beds available.")
            return
        labels = [f"{b.id}: {b.building} {b.room}-{b.code}" for b in beds]
        choice, ok = QInputDialog.getItem(self, "Assign bed", "Bed:", labels, 0, False)
        if not ok:
            return
        bed_id = int(choice.split(":", 1)[0])
        sid_ext = row[0]
        students = self.container.students.search(self.session, text=sid_ext)
        if not students:
            return
        try:
            self.container.housing.assign_bed(
                self.session, students[0].id, bed_id, date.today(),
                reason="manual assignment")
        except Exception as e:
            QMessageBox.warning(self, "Assignment failed", str(e))
            return
        self.refresh()
        self.main.statusBar().showMessage("Bed assigned.", 3000)

    def _view_history(self, row) -> None:
        if not row:
            return
        students = self.container.students.search(self.session, text=row[0])
        if not students:
            return
        history = self.container.students.history(self.session, students[0].id)
        text = "\n".join(f"[{h.ts}] {h.action}: {h.payload}" for h in history) or "(no history)"
        QMessageBox.information(self, "Change history", text)


class HousingTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("<b>Beds</b>"))
        self.table = ResultsTable(["Bed ID", "Building", "Room", "Code", "Occupied"])
        outer.addWidget(self.table, 1)
        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh)
        outer.addWidget(btn)
        self.refresh()

    def refresh(self) -> None:
        beds = self.container.housing.list_beds(self.session)
        self.table.set_rows([
            [b.id, b.building, b.room, b.code, "yes" if b.occupied else "no"]
            for b in beds])


class ResourcesTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.new_btn = QPushButton("New resource")
        self.add_ver_btn = QPushButton("Add version")
        self.publish_btn = QPushButton("Publish latest")
        self.hold_btn = QPushButton("Place on hold")
        bar.addWidget(self.new_btn)
        bar.addWidget(self.add_ver_btn)
        bar.addWidget(self.publish_btn)
        bar.addWidget(self.hold_btn)
        bar.addStretch()
        outer.addLayout(bar)
        self.table = ResultsTable(["ID", "Title", "Category", "Status",
                                   "Latest", "Published"])
        outer.addWidget(self.table, 1)
        self.new_btn.clicked.connect(self._new)
        self.add_ver_btn.clicked.connect(self._add_version)
        self.publish_btn.clicked.connect(self._publish_latest)
        self.hold_btn.clicked.connect(self._hold)
        self.refresh()

    def refresh(self) -> None:
        rs = self.container.resources.search(self.session)
        self.table.set_rows([
            [r.id, r.title, r.category or "", r.status,
             r.latest_version or "", r.published_version or ""] for r in rs])

    def _selected_id(self) -> int | None:
        row = self.table.selected_row_data()
        if not row:
            return None
        try:
            return int(row[0])
        except ValueError:
            return None

    def _new(self) -> None:
        title, ok = QInputDialog.getText(self, "New resource", "Title:")
        if not ok or not title.strip():
            return
        try:
            self.container.resources.create_resource(self.session, title.strip())
        except Exception as e:
            QMessageBox.warning(self, "Create failed", str(e))
            return
        self.refresh()

    def _add_version(self) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        summary, ok = QInputDialog.getText(self, "Add version", "Summary:")
        if not ok:
            return
        body, ok = QInputDialog.getMultiLineText(self, "Add version", "Body:")
        if not ok:
            return
        try:
            self.container.resources.add_version(self.session, rid, summary, body)
        except Exception as e:
            QMessageBox.warning(self, "Add version failed", str(e))
            return
        self.refresh()

    def _publish_latest(self) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        versions = self.container.resources.list_versions(self.session, rid)
        if not versions:
            QMessageBox.information(self, "No versions", "Add a version first.")
            return
        try:
            self.container.resources.publish_version(self.session, versions[0].id)
        except Exception as e:
            QMessageBox.warning(self, "Publish failed", str(e))
            return
        self.refresh()
        self.main.statusBar().showMessage("Version published.", 3000)

    def _hold(self) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        reason, ok = QInputDialog.getText(self, "Place on hold", "Reason:")
        if not ok:
            return
        try:
            self.container.resources.place_on_hold(self.session, rid, reason)
        except Exception as e:
            QMessageBox.warning(self, "Hold failed", str(e))
            return
        self.refresh()


class ComplianceTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.submit_btn = QPushButton("Submit employer")
        self.approve_btn = QPushButton("Approve")
        self.reject_btn = QPushButton("Reject")
        bar.addWidget(self.submit_btn)
        bar.addWidget(self.approve_btn)
        bar.addWidget(self.reject_btn)
        bar.addStretch()
        outer.addLayout(bar)
        self.table = ResultsTable(["Case", "Employer", "Kind", "State",
                                   "Decision", "Decided"])
        outer.addWidget(self.table, 1)
        self.submit_btn.clicked.connect(self._submit)
        self.approve_btn.clicked.connect(lambda: self._decide("approve"))
        self.reject_btn.clicked.connect(lambda: self._decide("reject"))
        self.refresh()

    def refresh(self) -> None:
        cases = self.container.compliance.list_cases(self.session)
        self.table.set_rows([
            [c.id, c.employer_name, c.kind, c.state, c.decision or "",
             c.decided_at or ""] for c in cases])

    def _submit(self) -> None:
        name, ok = QInputDialog.getText(self, "Submit employer", "Name:")
        if not ok or not name.strip():
            return
        try:
            self.container.compliance.submit_employer(
                self.session, name.strip(), None, None)
        except Exception as e:
            QMessageBox.warning(self, "Submit failed", str(e))
            return
        self.refresh()

    def _decide(self, decision: str) -> None:
        row = self.table.selected_row_data()
        if not row:
            return
        try:
            cid = int(row[0])
        except ValueError:
            return
        notes, ok = QInputDialog.getText(self, "Decision", "Notes:")
        if not ok:
            return
        try:
            self.container.compliance.decide(self.session, cid, decision, notes)
        except Exception as e:
            QMessageBox.warning(self, "Decision failed", str(e))
            return
        self.refresh()


class NotificationsTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.read_btn = QPushButton("Mark read")
        self.retry_btn = QPushButton("Retry failed")
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.read_btn)
        bar.addWidget(self.retry_btn)
        bar.addStretch()
        outer.addLayout(bar)
        self.table = ResultsTable(["ID", "Template", "Subject", "Status", "When", "Read"])
        outer.addWidget(self.table, 1)
        self.refresh_btn.clicked.connect(self.refresh)
        self.read_btn.clicked.connect(self._mark_read)
        self.retry_btn.clicked.connect(self._retry)
        self.refresh()

    def refresh(self) -> None:
        msgs = self.container.notifications.inbox(self.session, limit=200)
        self.table.set_rows([
            [m.id, m.template_name, m.subject, m.status, m.created_at,
             m.read_at or ""] for m in msgs])

    def _mark_read(self) -> None:
        row = self.table.selected_row_data()
        if not row:
            return
        try:
            self.container.notifications.mark_read(self.session, int(row[0]))
        except Exception:
            pass
        self.refresh()

    def _retry(self) -> None:
        try:
            n = self.container.notifications.retry_failed(self.session)
            self.main.statusBar().showMessage(f"Re-queued {n} message(s).", 3000)
        except Exception as e:
            QMessageBox.warning(self, "Retry failed", str(e))


class ReportsTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.occ_btn = QPushButton("Occupancy")
        self.move_btn = QPushButton("Move trends")
        self.vel_btn = QPushButton("Resource velocity")
        self.sla_btn = QPushButton("Compliance SLA")
        self.notif_btn = QPushButton("Notification delivery")
        self.export_btn = QPushButton("Export CSV")
        for b in (self.occ_btn, self.move_btn, self.vel_btn, self.sla_btn,
                  self.notif_btn, self.export_btn):
            bar.addWidget(b)
        bar.addStretch()
        outer.addLayout(bar)
        self.table = ResultsTable(["—"])
        outer.addWidget(self.table, 1)
        self._current = None
        self.occ_btn.clicked.connect(lambda: self._show("occupancy"))
        self.move_btn.clicked.connect(lambda: self._show("move_trends"))
        self.vel_btn.clicked.connect(lambda: self._show("resource_velocity"))
        self.sla_btn.clicked.connect(lambda: self._show("compliance_sla"))
        self.notif_btn.clicked.connect(lambda: self._show("notification_delivery"))
        self.export_btn.clicked.connect(self._export)

    def _show(self, name: str) -> None:
        try:
            report = getattr(self.container.reporting, name)(self.session)
        except Exception as e:
            QMessageBox.warning(self, "Report failed", str(e))
            return
        self._current = report
        self.table.setColumnCount(len(report.columns))
        self.table.setHorizontalHeaderLabels(report.columns)
        self.table.set_rows(report.rows)
        self.main.statusBar().showMessage(report.title, 5000)

    def _export(self) -> None:
        if self._current is None:
            QMessageBox.information(self, "No report", "Run a report first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report",
                                              "report.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            self.container.reporting.export(self.session, self._current, "csv", path)
            self.main.statusBar().showMessage(f"Exported to {path}", 4000)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))


# ---- Main window ----------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, container, session) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.detached: list = []
        self.setWindowTitle(
            f"CRHGC — {session.full_name} ({', '.join(session.roles) or 'no roles'})")
        self.resize(1180, 760)

        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)

        # Each tab is gated on the perms its underlying service requires.
        # A user without the perms simply does not see the tab; this avoids
        # exposing read APIs on sensitive domains by mere UI presence.
        def _can(*perms: str) -> bool:
            return any(session.has(p) for p in perms)

        student_perms = ("student.write", "student.import", "student.pii.read",
                         "housing.write", "system.admin")
        housing_perms = ("housing.write", "system.admin")
        resource_perms = ("resource.write", "resource.publish", "system.admin")
        catalog_perms = ("catalog.write", "catalog.review", "catalog.publish",
                         "system.admin")
        compliance_perms = ("compliance.review", "compliance.violation",
                            "compliance.evidence", "compliance.action",
                            "system.admin")
        compliance_ext_perms = ("compliance.evidence", "compliance.action",
                                "system.admin")
        bom_perms = ("bom.write", "bom.approve.first", "bom.approve.final",
                     "system.admin")
        notif_perms = ("notification.admin", "system.admin")
        report_perms = ("report.read", "report.export", "system.admin")
        update_perms = ("update.apply", "system.admin")

        if _can(*student_perms):
            self.tabs.addTab(StudentsTab(container, session, self), "Students")
        if _can(*housing_perms):
            self.tabs.addTab(HousingTab(container, session, self), "Housing")
        if _can(*resource_perms):
            self.tabs.addTab(ResourcesTab(container, session, self), "Resources")
        if _can(*catalog_perms):
            self.tabs.addTab(CatalogTab(container, session, self), "Catalog")
        if _can(*compliance_perms):
            self.tabs.addTab(ComplianceTab(container, session, self), "Compliance")
        if _can(*compliance_ext_perms):
            self.tabs.addTab(ComplianceExtTab(container, session, self),
                             "Evidence/Actions")
        if _can(*bom_perms):
            self.tabs.addTab(BomTab(container, session, self), "Styles/BOM")
        # Notifications inbox is per-user, so always visible.
        self.tabs.addTab(NotificationsTab(container, session, self),
                         "Notifications")
        if _can(*report_perms):
            self.tabs.addTab(ReportsTab(container, session, self), "Reports")
        if _can(*update_perms):
            self.tabs.addTab(UpdaterTab(container, session, self), "Updates")

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        self._build_menu()
        self._wire_shortcuts()
        self._build_tray()
        self._build_saved_sidebar()

        # Notification dispatcher tick (every 30s).
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setInterval(30_000)
        self._dispatch_timer.timeout.connect(self._tick_dispatcher)
        self._dispatch_timer.start()
        self._tick_dispatcher()

        # Crash-recovery: restore last workspace + offer to recover drafts.
        self._restore_workspace()
        self._offer_draft_recovery()

        # Autosave checkpoint every 60 s.
        self._checkpoint_timer = QTimer(self)
        self._checkpoint_timer.setInterval(60_000)
        self._checkpoint_timer.timeout.connect(self._tick_checkpoint)
        self._checkpoint_timer.start()

    # ---- menu / shortcuts ----

    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_m = bar.addMenu("&File")
        file_m.addAction(self._make_action("New record", "Ctrl+Shift+N", self._new_record))
        file_m.addAction(self._make_action("Export…", "Ctrl+E", self._export_current))
        file_m.addSeparator()
        file_m.addAction(self._make_action("Lock", "Ctrl+L", self._lock))
        file_m.addAction(self._make_action("Quit", "Ctrl+Q", self.close))

        edit_m = bar.addMenu("&Edit")
        edit_m.addAction(self._make_action("Search…", "Ctrl+K", self._open_palette))
        edit_m.addAction(self._make_action("Settings", "Ctrl+,", self._open_settings))

        help_m = bar.addMenu("&Help")
        help_m.addAction(self._make_action("About", "F1", self._about))

    def _make_action(self, label: str, shortcut: str, fn) -> QAction:
        a = QAction(label, self)
        a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(fn)
        return a

    def _wire_shortcuts(self) -> None:
        # Belt & suspenders: also create global QShortcuts so they fire even
        # when focus is on a non-menu widget.
        for keys, fn in (("Ctrl+K", self._open_palette),
                         ("Ctrl+Shift+N", self._new_record),
                         ("Ctrl+E", self._export_current),
                         ("Ctrl+L", self._lock),
                         ("Esc", self._close_topmost_detached)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(fn)

    def _build_saved_sidebar(self) -> None:
        """Dockable sidebar listing the user's pinned saved searches.

        Single-click runs the saved query in the global palette so the
        operator stays inside the unified search UI. Right-click unpins.
        """
        self.saved_dock = QDockWidget("Pinned searches", self)
        self.saved_dock.setObjectName("pinned_searches_dock")
        self.saved_list = QListWidget()
        self.saved_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.saved_list.itemActivated.connect(self._open_saved_search)
        self.saved_list.customContextMenuRequested.connect(
            self._saved_sidebar_menu)
        self.saved_dock.setWidget(self.saved_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.saved_dock)
        self._refresh_saved_sidebar()

    def _refresh_saved_sidebar(self) -> None:
        self.saved_list.clear()
        try:
            saved = self.container.search.list_saved(self.session)
        except Exception:
            saved = []
        for s in saved:
            if not s.get("pinned"):
                continue
            item = QListWidgetItem(f"📌  {s['name']}")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.saved_list.addItem(item)
        if self.saved_list.count() == 0:
            self.saved_list.addItem(
                "(No pinned searches — pin from Ctrl+K)")

    def _open_saved_search(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        text = (data.get("query") or {}).get("text", "")
        dlg = SearchPalette(self.container, self.session, self)
        dlg.input.setText(text)
        dlg.hit_chosen.connect(self._handle_palette_hit)
        dlg.exec()

    def _saved_sidebar_menu(self, pos) -> None:
        item = self.saved_list.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self.saved_list)
        unpin = menu.addAction("Unpin")
        delete = menu.addAction("Delete saved search")
        chosen = menu.exec(self.saved_list.mapToGlobal(pos))
        if chosen == unpin:
            self.container.search.pin(self.session, data["id"], False)
        elif chosen == delete:
            self.container.search.delete_saved(self.session, data["id"])
        self._refresh_saved_sidebar()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(), self)
        self.tray.setToolTip("CRHGC")
        menu = QMenu()
        menu.addAction("Open", self._show_from_tray)
        menu.addAction("Search…", self._open_palette)
        menu.addAction("Lock", self._lock)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._show_from_tray() if r ==
                                    QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    # ---- actions ----

    def _open_palette(self) -> None:
        dlg = SearchPalette(self.container, self.session, self)
        dlg.hit_chosen.connect(self._handle_palette_hit)
        dlg.exec()

    def _handle_palette_hit(self, entity_type: str, entity_id: int, action: str) -> None:
        if entity_type == "student":
            w = StudentProfileWindow(self.container, self.session, entity_id, self)
            w.show()
            self.detached.append(w)
        else:
            self.statusBar().showMessage(
                f"Open requested for {entity_type} #{entity_id}", 3000)

    def _new_record(self) -> None:
        idx = self.tabs.currentIndex()
        widget = self.tabs.widget(idx)
        if hasattr(widget, "_create_student"):
            widget._create_student()
        elif hasattr(widget, "_new"):
            widget._new()
        elif hasattr(widget, "_submit"):
            widget._submit()
        else:
            self.statusBar().showMessage("No 'new' action for this tab.", 3000)

    def _export_current(self) -> None:
        widget = self.tabs.currentWidget()
        if hasattr(widget, "_export_csv"):
            widget._export_csv()
        elif hasattr(widget, "_export"):
            widget._export()
        else:
            self.statusBar().showMessage("Nothing to export here.", 3000)

    def _open_settings(self) -> None:
        QMessageBox.information(self, "Settings",
                                "Settings panel: synonyms, templates, rules.\n"
                                "(Edit via the Notifications and Settings APIs.)")

    def _lock(self) -> None:
        self.container.auth.logout(self.session)
        QMessageBox.information(self, "Locked",
                                "Session locked. Sign in again to continue.")
        # Bypass the tray-minimize behavior in closeEvent so locking truly
        # tears down the window and forces the launch loop to re-prompt.
        self._force_quit = True
        self._relogin = True
        try:
            if self.tray.isVisible():
                self.tray.hide()
        except Exception:
            pass
        self.close()
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _close_topmost_detached(self) -> None:
        if self.detached:
            w = self.detached.pop()
            try:
                w.close()
            except Exception:
                pass

    def _about(self) -> None:
        QMessageBox.about(self, "CRHGC",
                          "Collegiate Resource & Housing Governance Console\n"
                          "Version 1.0.0\n"
                          "Offline desktop edition.")

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _tick_dispatcher(self) -> None:
        try:
            self.container.notifications.fire_scheduled_rules()
            n = self.container.notifications.drain_queue()
        except Exception:
            return
        if n:
            self.statusBar().showMessage(f"Delivered {n} notification(s).", 3000)
            try:
                unread = self.container.notifications.unread_count(self.session)
                self.tray.setToolTip(f"CRHGC — {unread} unread")
            except Exception:
                pass

    def _restore_workspace(self) -> None:
        try:
            ws = self.container.checkpoints.load_workspace(self.session)
        except Exception:
            ws = None
        if not ws:
            return
        idx = ws.get("active_tab")
        if isinstance(idx, int) and 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    def _offer_draft_recovery(self) -> None:
        try:
            drafts = self.container.checkpoints.list_drafts(self.session)
        except Exception:
            drafts = []
        if not drafts:
            return
        names = ", ".join(d["draft_key"] for d in drafts[:5])
        more = "" if len(drafts) <= 5 else f" (+{len(drafts) - 5} more)"
        ans = QMessageBox.question(
            self, "Recover unsaved drafts?",
            f"Found {len(drafts)} unsaved draft(s): {names}{more}.\n"
            "Restore now? (Choose No to discard them.)")
        if ans != QMessageBox.StandardButton.Yes:
            try:
                self.container.checkpoints.discard_all(self.session)
            except Exception:
                pass

    def _tick_checkpoint(self) -> None:
        try:
            self.container.checkpoints.save_workspace(
                self.session,
                {"active_tab": self.tabs.currentIndex(),
                 "open_tabs": [self.tabs.tabText(i)
                               for i in range(self.tabs.count())]})
        except Exception:
            pass
        # Refresh the encrypted at-rest blob on the checkpoint cadence so a
        # crash / SIGKILL leaves at most one checkpoint window of plaintext
        # exposure on disk, instead of the entire session.
        try:
            from backend import db as _db
            _db.periodic_reseal()
        except Exception:
            pass

    def _drain_detached(self) -> None:
        # Explicit cleanup so 8-hour sessions don't leak QObject hierarchies.
        for w in list(self.detached):
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
        self.detached.clear()

    def closeEvent(self, ev) -> None:
        # Minimize to tray instead of closing if tray is available.
        if self.tray.isVisible() and not getattr(self, "_force_quit", False):
            self.hide()
            self.tray.showMessage("CRHGC", "Still running in the tray.",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)
            ev.ignore()
            return
        # Final cleanup before quit.
        self._tick_checkpoint()
        self._drain_detached()
        try:
            self._dispatch_timer.stop()
            self._checkpoint_timer.stop()
        except Exception:
            pass
        # At-rest encryption: re-seal the SQLite database before exiting so
        # the plaintext file does not linger on disk while the app is off.
        try:
            from backend import db as _db
            _db.close_and_seal()
        except Exception:
            pass
        ev.accept()


# ---- Entry point ----------------------------------------------------------

def launch(container) -> int:
    # High-DPI scaling is on by default in PyQt6, but explicitly request the
    # PassThrough rounding policy so 1920x1080 + scaling stays crisp.
    if QApplication.instance() is None:
        try:
            from PyQt6.QtGui import QGuiApplication
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("CRHGC")
    app.setOrganizationName("CRHGC")
    qss = (__import__("pathlib").Path(__file__).parent / "style.qss")
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))

    if not container.auth.has_any_users():
        bdlg = BootstrapDialog(container)
        if not bdlg.exec():
            return 0

    while True:
        ldlg = LoginDialog(container)
        if not ldlg.exec():
            return 0
        session = ldlg.session
        win = MainWindow(container, session)
        win.show()
        rc = app.exec()
        relogin = getattr(win, "_relogin", False)
        try:
            win.deleteLater()
        except Exception:
            pass
        if rc != 0:
            return rc
        if not relogin:
            return 0
