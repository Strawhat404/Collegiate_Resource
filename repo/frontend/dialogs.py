"""Login, bootstrap, unlock, and small input dialogs."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QLineEdit, QMessageBox, QVBoxLayout)


class LoginDialog(QDialog):
    def __init__(self, container, parent=None) -> None:
        super().__init__(parent)
        self.container = container
        self.session = None
        self.setWindowTitle("Sign in")
        self.setMinimumWidth(320)

        layout = QFormLayout(self)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Username", self.username)
        layout.addRow("Password", self.password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.username.setFocus()

    def _accept(self) -> None:
        try:
            self.session = self.container.auth.login(
                self.username.text().strip(), self.password.text())
        except Exception as e:
            QMessageBox.warning(self, "Sign in failed", str(e))
            return
        self.accept()


class BootstrapDialog(QDialog):
    def __init__(self, container, parent=None) -> None:
        super().__init__(parent)
        self.container = container
        self.user = None
        self.setWindowTitle("Create administrator")
        self.setMinimumWidth(380)

        layout = QFormLayout(self)
        self.full_name = QLineEdit()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Full name", self.full_name)
        layout.addRow("Username", self.username)
        layout.addRow("Password (≥10 chars)", self.password)
        layout.addRow("Confirm password", self.confirm)

        info = QLabel("This account will hold the System Administrator role.")
        info.setWordWrap(True)
        layout.addRow(info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self) -> None:
        if self.password.text() != self.confirm.text():
            QMessageBox.warning(self, "Mismatch", "Passwords do not match.")
            return
        try:
            self.user = self.container.auth.bootstrap_admin(
                self.username.text().strip(), self.password.text(),
                self.full_name.text().strip())
        except Exception as e:
            QMessageBox.warning(self, "Bootstrap failed", str(e))
            return
        self.accept()


class UnlockDialog(QDialog):
    def __init__(self, container, session, parent=None) -> None:
        super().__init__(parent)
        self.container = container
        self.session = session
        self.unlocked = False
        self.setWindowTitle("Re-enter password")
        layout = QFormLayout(self)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow(QLabel("Re-enter your password to reveal masked fields."))
        layout.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept(self) -> None:
        try:
            self.container.auth.unlock_masked_fields(
                self.session, self.password.text())
            self.unlocked = True
        except Exception as e:
            QMessageBox.warning(self, "Unlock failed", str(e))
            return
        self.accept()
