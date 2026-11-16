from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from theia.errors import AssistantError
from theia.preferences import DEFAULTS
from theia.reminders import format_due


class SettingsDialog(QDialog):
    def __init__(self, preferences, parent):
        super().__init__(parent)
        self.setWindowTitle("Theia settings")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.preferences = preferences
        self.fields = {}

        for name, label, choices in (
            ("time_format", "Time format", {12: "12-hour", 24: "24-hour"}),
            ("answer_length", "Answer length", {"default": "Default", "short": "Short"}),
            ("speaking_speed", "Speaking speed", {"normal": "Normal", "slower": "Slower", "faster": "Faster"}),
        ):
            row = QHBoxLayout()
            combo = QComboBox()
            self.fields[name] = combo
            combo.setProperty("preference", name)
            combo.setAccessibleName(label)
            for value, caption in choices.items():
                combo.addItem(caption, value)
            combo.setCurrentIndex(combo.findData(preferences.get(name)))
            combo.activated.connect(self.change_value)
            reset = QPushButton("Reset")
            reset.setProperty("preference", name)
            reset.setAccessibleName(f"Reset {label.casefold()}")
            reset.clicked.connect(self.reset_value)
            row.addWidget(combo, 1)
            row.addWidget(reset)
            form.addRow(label, row)
        layout.addWidget(self.error)
        close_button = QPushButton("Done")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def change_value(self, index):
        field = self.sender()
        self.save(field.property("preference"), field.itemData(index))

    def reset_value(self):
        name = self.sender().property("preference")
        self.save(name, DEFAULTS[name])

    def save(self, name, value):
        try:
            self.preferences.set(name, value)
        except AssistantError as error:
            self.error.setText(str(error))
        else:
            self.error.clear()
        field = self.fields[name]
        field.setCurrentIndex(field.findData(self.preferences.get(name)))


class RemindersDialog(QDialog):
    def __init__(self, assistant, parent):
        super().__init__(parent)
        self.setWindowTitle("Theia reminders")
        self.setMinimumSize(410, 290)
        self.assistant = assistant
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        self.items.setAccessibleName("Pending reminders")
        layout.addWidget(self.items)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_item = QPushButton("Cancel selected")
        self.cancel_item.setEnabled(False)
        self.cancel_item.clicked.connect(self.cancel_selected)
        self.items.currentItemChanged.connect(self.selection_changed)
        actions.addWidget(self.cancel_item)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        actions.addWidget(done)
        layout.addLayout(actions)
        self.refresh()

    def selection_changed(self, current, previous):
        self.cancel_item.setEnabled(current is not None)

    def refresh(self):
        try:
            items = self.assistant.reminders.pending()
        except AssistantError as error:
            self.status.setText(str(error))
            return
        time_format = self.assistant.preferences.time_format
        if self.snapshot != (items, time_format):
            self.snapshot = (items, time_format)
            selected = self.items.currentItem()
            selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
            self.items.clear()
            for item in items:
                row = QListWidgetItem(f"{item.label}   ·   {format_due(item.due, time_format)}")
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                self.items.addItem(row)
                if item.id == selected_id:
                    self.items.setCurrentItem(row)
        self.status.setText("" if items else "No pending timers or reminders.")

    def cancel_selected(self):
        if item := self.items.currentItem():
            try:
                self.assistant.reminders.cancel(item.data(Qt.ItemDataRole.UserRole))
            except AssistantError as error:
                self.status.setText(str(error))
            else:
                self.refresh()
