from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from theia.errors import AssistantError
from theia.preferences import DEFAULTS
from theia.reminders import format_due


class SettingsDialog(QDialog):
    def __init__(self, preferences, parent, *, interruption=False):
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
        self.audio_action = None

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
        audio = QLabel("Spoken interruption is on for this session." if interruption else "Spoken interruption is off.")
        layout.addWidget(audio)
        check = QPushButton("Run interruption sound check")
        check.clicked.connect(self.sound_check)
        layout.addWidget(check)
        if interruption:
            disable = QPushButton("Turn off spoken interruption")
            disable.clicked.connect(self.disable_interruption)
            layout.addWidget(disable)
        close_button = QPushButton("Done")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def sound_check(self):
        self.audio_action = "check"
        self.accept()

    def disable_interruption(self):
        self.audio_action = "disable"
        self.accept()

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


class RecapEditor(QDialog):
    def __init__(self, store, parent, text, *, title=""):
        super().__init__(parent)
        self.setWindowTitle("Review conversation recap")
        self.setMinimumSize(460, 350)
        self.store = store
        self.previous_title = title or None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(title)
        self.title.setMaxLength(100)
        form.addRow("Title", self.title)
        layout.addLayout(form)
        self.text = QPlainTextEdit(text)
        self.text.setAccessibleName("Conversation recap")
        layout.addWidget(self.text, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = QPushButton("Save recap")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        layout.addLayout(actions)

    def save(self):
        try:
            self.store.save(self.title.text(), self.text.toPlainText(), previous_title=self.previous_title)
        except AssistantError as error:
            self.error.setText(str(error))
        else:
            self.accept()


class RecapsDialog(QDialog):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.setWindowTitle("Saved conversations")
        self.setMinimumSize(450, 330)
        self.store = store
        self.action = None
        layout = QVBoxLayout(self)
        self.items = QListWidget()
        self.items.setAccessibleName("Saved conversations")
        self.items.itemDoubleClicked.connect(self.edit)
        self.items.currentItemChanged.connect(self.selection_changed)
        layout.addWidget(self.items, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.selection_buttons = []
        for label, handler in (
            ("Save recap", self.new_recap), ("Open or rename", self.edit),
            ("Resume", self.resume), ("Delete", self.delete),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
            if handler != self.new_recap:
                self.selection_buttons.append(button)
        layout.addLayout(actions)
        self.refresh()

    def selection_changed(self, current, previous=None):
        for button in self.selection_buttons:
            button.setEnabled(current is not None)

    def refresh(self):
        try:
            records = self.store.list()
        except AssistantError as error:
            self.status.setText(str(error))
            return
        self.items.clear()
        for recap in records:
            item = QListWidgetItem(recap.title)
            item.setData(Qt.ItemDataRole.UserRole, recap.text)
            self.items.addItem(item)
        self.selection_changed(None)
        self.status.setText("" if records else "No saved conversations yet.")

    def new_recap(self):
        self.action = ("draft", "")
        self.accept()

    def edit(self):
        if item := self.items.currentItem():
            dialog = RecapEditor(self.store, self, item.data(Qt.ItemDataRole.UserRole), title=item.text())
            dialog.exec()
            dialog.deleteLater()
            self.refresh()

    def resume(self):
        if item := self.items.currentItem():
            self.action = ("resume", item.text())
            self.accept()

    def delete(self):
        item = self.items.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "Delete saved conversation?", "Delete the selected recap?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(item.text())
        except AssistantError as error:
            self.status.setText(str(error))
        else:
            self.refresh()
