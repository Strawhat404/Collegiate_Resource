"""Tabs added in chunk 2: Catalog, Evidence/Actions, Styles/BOM, Updates."""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                             QMessageBox, QPushButton, QSplitter, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from .widgets import ResultsTable


# ---- Catalog tab ----------------------------------------------------------

class CatalogTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window

        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.add_node_btn = QPushButton("Add folder")
        self.add_type_btn = QPushButton("Define type…")
        self.attach_btn = QPushButton("Attach resource…")
        self.review_btn = QPushButton("Submit for review")
        self.publish_btn = QPushButton("Publish (semver bump)")
        for b in (self.add_node_btn, self.add_type_btn, self.attach_btn,
                  self.review_btn, self.publish_btn):
            bar.addWidget(b)
        bar.addStretch()
        outer.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Catalog tree"])
        self.types = ResultsTable(["Code", "Name", "Fields"])
        split.addWidget(self.tree)
        split.addWidget(self.types)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        self.add_node_btn.clicked.connect(self._add_node)
        self.add_type_btn.clicked.connect(self._add_type)
        self.attach_btn.clicked.connect(self._attach)
        self.review_btn.clicked.connect(self._submit_review)
        self.publish_btn.clicked.connect(self._publish)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for root in self.container.catalog.list_tree():
            self._add_tree_item(self.tree.invisibleRootItem(), root)
        self.tree.expandAll()
        types = self.container.catalog.list_types()
        self.types.set_rows([
            [t.code, t.name,
             ", ".join(f"{f.code}{'*' if f.required else ''}" for f in t.fields)]
            for t in types])

    def _add_tree_item(self, parent, node) -> None:
        item = QTreeWidgetItem([node.name])
        item.setData(0, Qt.ItemDataRole.UserRole, node.id)
        parent.addChild(item)
        for c in node.children:
            self._add_tree_item(item, c)

    def _selected_node_id(self) -> int | None:
        it = self.tree.currentItem()
        if not it:
            return None
        return it.data(0, Qt.ItemDataRole.UserRole)

    def _add_node(self) -> None:
        name, ok = QInputDialog.getText(self, "New folder", "Name:")
        if not ok or not name.strip():
            return
        try:
            self.container.catalog.create_node(self.session, name.strip(),
                                               self._selected_node_id())
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))
            return
        self.refresh()

    def _add_type(self) -> None:
        code, ok = QInputDialog.getText(self, "Type code", "Code:")
        if not ok or not code.strip():
            return
        name, ok = QInputDialog.getText(self, "Type name", "Display name:")
        if not ok:
            return
        try:
            self.container.catalog.upsert_type(
                self.session, code.strip(), name.strip(), "",
                fields=[{"code": "title", "label": "Title",
                         "field_type": "text", "required": True}])
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))
            return
        self.refresh()

    def _attach(self) -> None:
        nid = self._selected_node_id()
        if nid is None:
            QMessageBox.information(self, "Pick folder",
                                    "Select a folder in the tree first.")
            return
        rid, ok = QInputDialog.getInt(self, "Attach", "Resource ID:")
        if not ok:
            return
        types = [t.code for t in self.container.catalog.list_types()]
        if not types:
            return
        tcode, ok = QInputDialog.getItem(self, "Attach", "Type:", types, 0, False)
        if not ok:
            return
        # For simplicity, do no metadata in the GUI demo.
        try:
            self.container.catalog.attach(self.session, rid, node_id=nid,
                                          type_code=tcode)
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))

    def _submit_review(self) -> None:
        rid, ok = QInputDialog.getInt(self, "Submit", "Resource ID:")
        if not ok:
            return
        try:
            self.container.catalog.submit_for_review(self.session, rid)
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))

    def _publish(self) -> None:
        rid, ok = QInputDialog.getInt(self, "Publish", "Resource ID:")
        if not ok:
            return
        level, ok = QInputDialog.getItem(self, "Bump", "Level:",
                                         ["minor", "patch", "major"], 0, False)
        if not ok:
            return
        try:
            new_v = self.container.catalog.publish_with_semver(
                self.session, rid, level=level)
            self.main.statusBar().showMessage(f"Resource at {new_v}", 4000)
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))


# ---- Evidence + violation actions ----------------------------------------

class ComplianceExtTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.upload_btn = QPushButton("Upload evidence…")
        self.scan_btn = QPushButton("Sensitive-word scan…")
        self.takedown_btn = QPushButton("Takedown")
        self.suspend30_btn = QPushButton("Suspend 30d")
        self.suspend60_btn = QPushButton("Suspend 60d")
        self.suspend180_btn = QPushButton("Suspend 180d")
        self.throttle_btn = QPushButton("Throttle")
        for b in (self.upload_btn, self.scan_btn, self.takedown_btn,
                  self.suspend30_btn, self.suspend60_btn, self.suspend180_btn,
                  self.throttle_btn):
            bar.addWidget(b)
        bar.addStretch()
        outer.addLayout(bar)
        outer.addWidget(QLabel("<b>Evidence files</b>"))
        self.files = ResultsTable(["Emp", "File", "SHA-256", "Bytes",
                                   "Uploaded", "Retain until"])
        outer.addWidget(self.files, 1)
        outer.addWidget(QLabel("<b>Active violation actions</b>"))
        self.actions = ResultsTable(["ID", "Emp", "Action", "Days",
                                     "Starts", "Ends", "Reason"])
        outer.addWidget(self.actions, 1)

        self.upload_btn.clicked.connect(self._upload)
        self.scan_btn.clicked.connect(self._scan)
        self.takedown_btn.clicked.connect(lambda: self._action("takedown"))
        self.suspend30_btn.clicked.connect(lambda: self._action("suspend", 30))
        self.suspend60_btn.clicked.connect(lambda: self._action("suspend", 60))
        self.suspend180_btn.clicked.connect(lambda: self._action("suspend", 180))
        self.throttle_btn.clicked.connect(lambda: self._action("throttle"))
        self.refresh()

    def refresh(self) -> None:
        emps = self.container.compliance.list_employers(self.session)
        files: list = []
        actions: list = []
        for e in emps:
            for f in self.container.evidence.list_for_employer(
                    e["id"], session=self.session):
                files.append([e["name"], f.file_name, f.sha256[:16] + "…",
                              f.size_bytes, f.uploaded_at, f.retain_until])
            for a in self.container.violations.list_for_employer(
                    e["id"], True, session=self.session):
                actions.append([a.id, e["name"], a.action, a.duration_days or "",
                                a.starts_at, a.ends_at or "", a.reason or ""])
        self.files.set_rows(files)
        self.actions.set_rows(actions)

    def _pick_employer(self) -> int | None:
        emps = self.container.compliance.list_employers(self.session)
        if not emps:
            QMessageBox.information(self, "No employers",
                                    "Submit an employer first.")
            return None
        labels = [f"{e['id']}: {e['name']}" for e in emps]
        choice, ok = QInputDialog.getItem(self, "Employer", "Pick:", labels, 0, False)
        if not ok:
            return None
        return int(choice.split(":", 1)[0])

    def _upload(self) -> None:
        emp_id = self._pick_employer()
        if emp_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Pick evidence file")
        if not path:
            return
        try:
            self.container.evidence.upload(self.session, emp_id, path)
        except Exception as e:
            QMessageBox.warning(self, "Upload failed", str(e))
            return
        self.refresh()

    def _scan(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "Sensitive-word scan", "Paste text:")
        if not ok or not text.strip():
            return
        hits = self.container.sensitive.scan(text)
        if not hits:
            QMessageBox.information(self, "Scan", "No sensitive terms found.")
            return
        msg = "\n".join(f"  {h['severity']:>6}  {h['word']}  @{h['position']}"
                        for h in hits)
        QMessageBox.warning(self, "Sensitive terms detected",
                            f"{len(hits)} match(es):\n{msg}")

    def _action(self, action: str, days: int | None = None) -> None:
        emp_id = self._pick_employer()
        if emp_id is None:
            return
        reason, ok = QInputDialog.getText(self, "Reason", "Reason:")
        if not ok:
            return
        try:
            if action == "takedown":
                self.container.violations.takedown(self.session, emp_id, reason)
            elif action == "suspend":
                self.container.violations.suspend(self.session, emp_id, days, reason)
            elif action == "throttle":
                self.container.violations.throttle(self.session, emp_id, reason)
        except Exception as e:
            QMessageBox.warning(self, "Action failed", str(e))
            return
        self.refresh()


# ---- Styles / BOM tab -----------------------------------------------------

class BomTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.new_style_btn = QPushButton("New style")
        self.add_bom_btn = QPushButton("Add BOM line")
        self.add_step_btn = QPushButton("Add routing step")
        self.submit_btn = QPushButton("Submit for approval")
        self.first_btn = QPushButton("First approve")
        self.final_btn = QPushButton("Final approve (release)")
        self.cr_btn = QPushButton("Open change request")
        for b in (self.new_style_btn, self.add_bom_btn, self.add_step_btn,
                  self.submit_btn, self.first_btn, self.final_btn, self.cr_btn):
            bar.addWidget(b)
        bar.addStretch()
        outer.addLayout(bar)

        self.styles = ResultsTable(["ID", "Code", "Name", "Status"])
        outer.addWidget(QLabel("<b>Styles</b>"))
        outer.addWidget(self.styles, 1)
        self.versions = ResultsTable(
            ["Ver ID", "No", "State", "Cost USD", "1st approver", "Released"])
        outer.addWidget(QLabel("<b>Versions</b>"))
        outer.addWidget(self.versions, 1)

        self.new_style_btn.clicked.connect(self._new_style)
        self.add_bom_btn.clicked.connect(self._add_bom)
        self.add_step_btn.clicked.connect(self._add_step)
        self.submit_btn.clicked.connect(self._submit)
        self.first_btn.clicked.connect(self._first)
        self.final_btn.clicked.connect(self._final)
        self.cr_btn.clicked.connect(self._open_cr)
        self.styles.itemSelectionChanged.connect(self._refresh_versions)
        self.refresh()

    def refresh(self) -> None:
        sts = self.container.bom.list_styles()
        self.styles.set_rows([[s.id, s.style_code, s.name, s.status] for s in sts])
        self._refresh_versions()

    def _refresh_versions(self) -> None:
        row = self.styles.selected_row_data()
        if not row:
            self.versions.set_rows([])
            return
        try:
            sid = int(row[0])
        except ValueError:
            return
        vs = self.container.bom.list_versions(sid)
        self.versions.set_rows([
            [v.id, v.version_no, v.state, f"${v.cost_usd:.2f}",
             v.first_approver_id or "", v.released_at or ""] for v in vs])

    def _new_style(self) -> None:
        code, ok = QInputDialog.getText(self, "New style", "Style code:")
        if not ok or not code.strip():
            return
        name, ok = QInputDialog.getText(self, "New style", "Name:")
        if not ok:
            return
        try:
            self.container.bom.create_style(self.session, code.strip(), name.strip())
        except Exception as e:
            QMessageBox.warning(self, "Create failed", str(e))
            return
        self.refresh()

    def _selected_version_id(self) -> int | None:
        row = self.versions.selected_row_data()
        if not row:
            return None
        try:
            return int(row[0])
        except ValueError:
            return None

    def _add_bom(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        code, ok = QInputDialog.getText(self, "BOM", "Component code:")
        if not ok:
            return
        qty, ok = QInputDialog.getDouble(self, "BOM", "Quantity:", 1.0, 0)
        if not ok:
            return
        cost, ok = QInputDialog.getDouble(self, "BOM", "Unit cost USD:", 0.0, 0)
        if not ok:
            return
        try:
            self.container.bom.add_bom_item(
                self.session, vid, component_code=code,
                quantity=qty, unit_cost_usd=cost)
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))
            return
        self._refresh_versions()

    def _add_step(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        op, ok = QInputDialog.getText(self, "Step", "Operation:")
        if not ok:
            return
        run, ok = QInputDialog.getDouble(self, "Step", "Run minutes:", 1.0, 0)
        if not ok:
            return
        rate, ok = QInputDialog.getDouble(self, "Step", "Rate per hour USD:", 0.0, 0)
        if not ok:
            return
        try:
            self.container.bom.add_routing_step(
                self.session, vid, operation=op,
                run_minutes=run, rate_per_hour_usd=rate)
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))
            return
        self._refresh_versions()

    def _submit(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        try:
            self.container.bom.submit_for_approval(self.session, vid)
        except Exception as e:
            QMessageBox.warning(self, "Submit failed", str(e))
            return
        self._refresh_versions()

    def _first(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        try:
            self.container.bom.first_approve(self.session, vid)
        except Exception as e:
            QMessageBox.warning(self, "Approve failed", str(e))
            return
        self._refresh_versions()

    def _final(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        try:
            self.container.bom.final_approve(self.session, vid)
        except Exception as e:
            QMessageBox.warning(self, "Approve failed", str(e))
            return
        self._refresh_versions()

    def _open_cr(self) -> None:
        vid = self._selected_version_id()
        if vid is None:
            return
        sid_row = self.styles.selected_row_data()
        if not sid_row:
            return
        sid = int(sid_row[0])
        reason, ok = QInputDialog.getText(self, "Change request", "Reason:")
        if not ok:
            return
        try:
            self.container.bom.open_change_request(self.session, sid, vid, reason)
        except Exception as e:
            QMessageBox.warning(self, "CR failed", str(e))
            return
        self._refresh_versions()


# ---- Updates tab ----------------------------------------------------------

class UpdaterTab(QWidget):
    def __init__(self, container, session, main_window) -> None:
        super().__init__()
        self.container = container
        self.session = session
        self.main = main_window
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.apply_btn = QPushButton("Import update package…")
        self.rollback_btn = QPushButton("Rollback selected")
        self.refresh_btn = QPushButton("Refresh")
        bar.addWidget(self.apply_btn)
        bar.addWidget(self.rollback_btn)
        bar.addWidget(self.refresh_btn)
        bar.addStretch()
        outer.addLayout(bar)
        self.table = ResultsTable(
            ["ID", "Version", "SHA-256", "Signed by", "Sig OK",
             "Applied", "Rolled back", "Notes"])
        outer.addWidget(self.table, 1)

        self.apply_btn.clicked.connect(self._apply)
        self.rollback_btn.clicked.connect(self._rollback)
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        try:
            pkgs = self.container.updater.list_packages()
        except Exception:
            pkgs = []
        self.table.set_rows([
            [p.id, p.version, p.sha256[:16] + "…", p.signed_by or "",
             "yes" if p.signature_ok else "no", p.applied_at,
             p.rolled_back_at or "", (p.notes or "")[:60]] for p in pkgs])

    def _apply(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick update package", "", "Update packages (*.zip *.crpkg)")
        if not path:
            return
        # Default the install dir to the actual application install path so
        # operators can confirm/override before applying. Previously this
        # silently fell back to the per-user data dir.
        from backend.services.updater import _default_install_dir
        suggested = str(_default_install_dir())
        target_dir, ok = QInputDialog.getText(
            self, "Install directory",
            "Files will be written to:", text=suggested)
        if not ok or not target_dir.strip():
            return
        try:
            pkg = self.container.updater.apply_package(
                self.session, path, install_dir=target_dir.strip())
        except Exception as e:
            # Signed-update policy: there is NO operator-driven UI bypass for
            # SIGNATURE_REQUIRED. The only path to apply an unsigned package
            # is the audited backend test hook (``allow_unsigned=True``), and
            # that hook is intentionally not exposed through the GUI so a
            # production operator cannot weaken the trust chain by clicking
            # through a dialog.
            code = getattr(e, "code", "")
            if code == "SIGNATURE_REQUIRED":
                QMessageBox.critical(
                    self, "Unsigned / untrusted package — REJECTED",
                    f"{e}\n\nThe package has no valid signature and was "
                    "rejected. Replace the public key at the documented "
                    "path with the production key, or obtain a properly "
                    "signed package from the release pipeline.")
            else:
                QMessageBox.warning(self, "Apply failed", str(e))
            return
        msg = f"Applied {pkg.version}.\nSignature: {'verified' if pkg.signature_ok else 'NOT verified'}"
        QMessageBox.information(self, "Update applied", msg)
        self.refresh()

    def _rollback(self) -> None:
        row = self.table.selected_row_data()
        if not row:
            return
        try:
            pid = int(row[0])
        except ValueError:
            return
        if QMessageBox.question(
                self, "Rollback?",
                f"Rollback package #{pid}? This restores the database "
                "snapshot taken before that package was applied.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.container.updater.rollback(self.session, pid)
        except Exception as e:
            QMessageBox.warning(self, "Rollback failed", str(e))
            return
        self.refresh()
