"""Generic table widget with right-click context menu support."""
from __future__ import annotations
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QHeaderView, QMenu, QTableWidget,
                             QTableWidgetItem)


class ResultsTable(QTableWidget):
    def __init__(self, columns: list[str], parent=None) -> None:
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._menu_actions: list[tuple[str, Callable]] = []
        self.customContextMenuRequested.connect(self._on_menu)

    def add_action(self, label: str, handler: Callable) -> None:
        self._menu_actions.append((label, handler))

    def set_rows(self, rows: list[list]) -> None:
        self.setRowCount(0)
        for r in rows:
            i = self.rowCount()
            self.insertRow(i)
            for j, val in enumerate(r):
                item = QTableWidgetItem("" if val is None else str(val))
                self.setItem(i, j, item)

    def selected_row_data(self) -> list | None:
        row = self.currentRow()
        if row < 0:
            return None
        return [self.item(row, c).text() if self.item(row, c) else ""
                for c in range(self.columnCount())]

    def _on_menu(self, pos) -> None:
        if not self._menu_actions:
            return
        menu = QMenu(self)
        for label, handler in self._menu_actions:
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked, h=handler: h(self.selected_row_data()))
        menu.exec(self.mapToGlobal(pos))
