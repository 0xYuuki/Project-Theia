from math import cos, radians, sin

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QWidget


STYLE = """
QMainWindow, QWidget#root { background: #10121d; }
QWidget { color: #e9e9f5; font-size: 13px; }
QFrame#card { background: #171a2a; border: 1px solid #2d3249; border-radius: 15px; }
QFrame#separator { background: #2b3046; }
QLabel#brand { color: #f4f1ff; font-size: 27px; font-weight: 750; }
QLabel#heading { color: #f2f0ff; font-size: 18px; font-weight: 650; }
QLabel#muted, QLabel#metricName { color: #afb6d1; }
QLabel#metricValue { color: #eeecff; font-weight: 650; }
QLabel#chatMode {
    color: #d5cbfc; background: #24233a; border: 1px solid #3f375b;
    border-radius: 10px; padding: 6px 10px; font-size: 11px; font-weight: 650;
}
QLabel#status { color: #c8bcfa; font-size: 12px; font-weight: 700; }
QLabel#status[active="true"] { color: #90eddd; }
QTextBrowser#transcript { border: none; background: transparent; color: #e9e9f5; }
QPlainTextEdit#heard {
    background: #101320; border: 1px solid #3a415f; border-radius: 8px; padding: 5px 7px;
}
QLineEdit {
    background: #101320; border: 1px solid #3a415f; border-radius: 9px;
    min-height: 21px; padding: 9px 12px; selection-background-color: #6955b7;
}
QPushButton {
    background: #21263c; color: #d8daf2; border: 1px solid #3a415e;
    border-radius: 8px; padding: 7px 12px; font-weight: 600;
}
QPushButton:hover { background: #2c3250; border-color: #746ba3; }
QPushButton:pressed { background: #343b5d; }
QPushButton#send { background: #a593f5; color: #17112d; border-color: #a593f5; min-width: 60px; }
QPushButton#send:hover { background: #b9a9ff; }
QPushButton#record {
    background: #b9a7ff; color: #17112d; border-color: #b9a7ff;
    min-height: 22px; padding: 8px 14px; font-weight: 700;
}
QPushButton#record:hover { background: #cbbdff; }
QPushButton#record[recording="true"] { background: #8de9d9; border-color: #8de9d9; }
QPushButton:disabled, QPushButton#record:disabled, QPushButton#send:disabled {
    color: #a4abc7; background: #202438; border-color: #30364f;
}
QPushButton:focus, QLineEdit:focus { border: 1px solid #c3b0ff; }
QCheckBox { color: #d8daf2; spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QProgressBar { background: #272c45; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #a18af0; border-radius: 3px; }
QProgressBar#RAM::chunk { background: #79cdd0; }
QProgressBar#Disk::chunk { background: #79a2eb; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #4b5272; border-radius: 4px; min-height: 22px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class BrandMark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(42, 42)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2c2849"))
        painter.drawRoundedRect(QRectF(0, 0, 42, 42), 12, 12)
        painter.setPen(QPen(QColor("#c3afff"), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(21, 8))
        for point in ((32, 21), (21, 34), (10, 21), (21, 8)):
            path.lineTo(*point)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#96e9e3"))
        painter.drawEllipse(QPointF(21, 21), 3, 3)


class VoiceOrb(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(70)
        self.setMaximumHeight(238)
        self.phase = 0
        self.active = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.timer.start(50)

    def advance(self):
        self.phase = (self.phase + (4 if self.active else 1)) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QRectF(self.rect()).center()
        radius = min(self.width(), self.height()) * 0.44
        painter.translate(center)
        painter.scale(radius / 80, radius / 80)

        halo = QRadialGradient(0, 0, 86)
        halo.setColorAt(0.15, QColor("#314a66" if self.active else "#35325d"))
        halo.setColorAt(0.65, QColor("#2c3556" if self.active else "#272944"))
        halo.setColorAt(1, QColor(23, 26, 42, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(), 86, 86)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for size, color in ((76, "#373b5c"), (65, "#494769"), (57, "#59618c")):
            painter.setPen(QPen(QColor(color), 0.9))
            painter.drawEllipse(QPointF(), size, size)

        painter.setPen(QPen(QColor("#7667b6"), 1.4))
        painter.drawArc(QRectF(-76, -76, 152, 152), self.phase * 16, 82 * 16)
        painter.setPen(QPen(QColor("#72c2d9"), 1.2))
        painter.drawArc(QRectF(-65, -65, 130, 130), (225 - self.phase) * 16, 67 * 16)

        surface = QLinearGradient(-50, -50, 50, 50)
        surface.setColorAt(0, QColor("#7162d6"))
        surface.setColorAt(0.45, QColor("#c6b4ff"))
        surface.setColorAt(1, QColor("#6ee0db"))
        painter.setPen(QPen(surface, 1.35))
        for width in (53, 39, 20):
            painter.drawEllipse(QPointF(), width, 53)
        for height in (33, 12):
            painter.drawEllipse(QPointF(), 53, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#a3f4e9" if self.active else "#e0d5ff"))
        angle = radians(self.phase)
        painter.drawEllipse(QPointF(76 * cos(angle), -76 * sin(angle)), 3, 3)
        painter.drawEllipse(QPointF(), 3.5, 3.5)
