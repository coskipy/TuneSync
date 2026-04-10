from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re
import os
import json
import time

from PySide6.QtCore import Qt, QUrl, QSize, QRect, QRectF, QObject, Signal, QThread, QTimer, QPoint, QEvent, QSettings
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPixmap, QFontMetrics, QIcon, QAction, QActionGroup, QPolygon
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QFileDialog,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QInputDialog


_DEBUG_IMAGES = os.getenv("TUNESYNC_DEBUG_IMAGES", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


class DownloadRow(QFrame):
    def __init__(self, *, title: str, subtitle: str, image_url: str | None, net: QNetworkAccessManager):
        super().__init__()
        self.setStyleSheet(
            "QFrame {"
            "background: #0f1216;"
            "border: none;"
            "border-radius: 12px;"
            "}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self.img = QLabel()
        self.img.setFixedSize(38, 38)
        self.img.setStyleSheet("background: #1a1e24; border-radius: 8px;")
        self.img.setScaledContents(True)
        lay.addWidget(self.img)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.t = QLabel(title)
        self.t.setStyleSheet("color: #e7eaf0; font-weight: 800;")
        self.t.setFixedHeight(18)
        self.t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.s = QLabel(subtitle)
        self.s.setStyleSheet("color: #a9b0bb;")
        self.s.setFixedHeight(18)
        self.s.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.addWidget(self.t)
        text.addWidget(self.s)
        lay.addLayout(text, 1)

        if image_url:
            req = QNetworkRequest(QUrl(image_url))
            req.setRawHeader(b"User-Agent", b"TuneSync")
            req.setRawHeader(b"Accept", b"image/*")
            try:
                req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
            except Exception:
                pass
            rep = net.get(req)

            def finished():
                rep.deleteLater()
                status = rep.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                if rep.error() != QNetworkReply.NoError:
                    if _DEBUG_IMAGES:
                        print(f"[img] FAIL row status={status} err={rep.error()} url={image_url} msg={rep.errorString()}")
                    return
                pm = QPixmap()
                data = bytes(rep.readAll())
                if not pm.loadFromData(data):
                    if _DEBUG_IMAGES:
                        ct = bytes(rep.rawHeader(b"Content-Type")).decode("utf-8", errors="ignore") if rep.hasRawHeader(b"Content-Type") else ""
                        print(f"[img] DECODE_FAIL row status={status} ct={ct} bytes={len(data)} url={image_url}")
                    return
                scaled = pm.scaled(self.img.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.img.setPixmap(scaled)

            rep.finished.connect(finished)


# -----------------
# Styling
# -----------------

_DARK_BG = "#0b0d10"
_PANEL = "#111418"
_CARD = "#f4f5f7"
_CARD_DISABLED = "#3a3e43"
_TEXT = "#e7eaf0"
_MUTED = "#a9b0bb"
_BLUE = "#2f7cf6"
_GREEN = "#2ecc71"
_ORANGE = "#f39c12"

_TOPBAR_ICON_PX = 27  # 50% larger than the previous 18px
_CONTROL_ICON_PX = 22


class _ImageCarouselPopover(QFrame):
    """Display a sequence of images in a carousel with Next/Close buttons."""
    closed = Signal()

    def __init__(self, *, image_paths: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("ImageCarouselPopover")
        self.setVisible(False)
        self._image_paths = image_paths
        self._current_index = 0
        self._current_pixmap: QPixmap | None = None
        self._preferred_size = QSize(720, 540)

        # Card chrome sizing (used to compute a popover size that never crops the image).
        self._chrome_w = 16 + 16
        self._chrome_h = (14 + 12) + 42 + 8 + 18 + 8 + 36

        self.setStyleSheet(
            "QFrame#ImageCarouselPopover { background: transparent; }"
            "QLabel { color: white; }"
        )

        self._card = QFrame(self)
        self._card.setObjectName("CarouselCard")
        self._card.setStyleSheet(
            "QFrame#CarouselCard {"
            "background: #14181d;"
            "border-radius: 14px;"
            "}"
        )

        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self._title = QLabel("Rekordbox Setup")
        self._title.setStyleSheet("font-weight: 900; font-size: 18px; color: white;")
        top.addWidget(self._title, 1)

        self._close = QToolButton()
        self._close.setText("✕")
        self._close.setFixedSize(42, 42)
        self._close.setStyleSheet(
            "QToolButton { background: transparent; color: white; border: none; font-weight: 900; font-size: 30px; }"
            "QToolButton:hover { color: rgba(255,255,255,0.88); }"
        )
        self._close.clicked.connect(self._on_close)
        top.addWidget(self._close, 0, Qt.AlignTop)
        lay.addLayout(top)

        self._image_label = QLabel()
        # Keep original aspect ratio; we scale the pixmap ourselves.
        self._image_label.setScaledContents(False)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: #0f1216; border-radius: 8px;")
        lay.addWidget(self._image_label, 1, Qt.AlignCenter)

        self._step_label = QLabel()
        self._step_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 13px;")
        self._step_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._step_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(10)

        self._back = QPushButton("Back")
        self._back.setFixedSize(104, 36)
        self._back.setStyleSheet(
            "QPushButton { background: transparent; color: #e7eaf0; border: 1px solid #2b2f36; border-radius: 8px; font-weight: 900; }"
            "QPushButton:hover { border-color: #3b414b; }"
            "QPushButton:disabled { background: transparent; color: #7f8793; border-color: #2b2f36; }"
        )
        self._back.clicked.connect(self._on_back)
        bottom.addWidget(self._back, 0, Qt.AlignLeft)

        bottom.addStretch(1)

        self._next = QPushButton("Next")
        self._next.setFixedSize(104, 36)
        self._next.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.95); color: #1b3b78; border: none; border-radius: 8px; font-weight: 900; }"
            "QPushButton:hover { background: white; }"
            "QPushButton:disabled { background: rgba(255,255,255,0.55); color: rgba(27,59,120,0.75); }"
        )
        self._next.clicked.connect(self._on_next)
        bottom.addWidget(self._next, 0, Qt.AlignRight)
        lay.addLayout(bottom)

        try:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(26)
            eff.setOffset(0, 10)
            eff.setColor(QColor(0, 0, 0, 140))
            self._card.setGraphicsEffect(eff)
        except Exception:
            pass

        self._refresh_image()

    def _on_close(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def _on_back(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._refresh_image()

    def _on_next(self) -> None:
        if self._current_index < len(self._image_paths) - 1:
            self._current_index += 1
            self._refresh_image()
        else:
            self._on_close()

    def _render_pixmap(self) -> None:
        pm = self._current_pixmap
        if pm is None or pm.isNull():
            try:
                self._image_label.clear()
            except Exception:
                pass
            return

        parent = self.parentWidget()
        if parent is not None:
            max_pop = QSize(int(parent.width() * 0.92), int(parent.height() * 0.92))
        else:
            max_pop = QSize(1400, 1000)

        max_img = QSize(max(10, max_pop.width() - self._chrome_w), max(10, max_pop.height() - self._chrome_h))
        desired_img = QSize(int(pm.width() * 1.25), int(pm.height() * 1.25))

        # Prefer 125% scale; if it won't fit, scale down to fit the max area.
        img_target = desired_img
        if desired_img.width() > max_img.width() or desired_img.height() > max_img.height():
            img_target = max_img

        try:
            scaled = pm.scaled(img_target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            scaled = pm

        try:
            self._image_label.setFixedSize(scaled.size())
        except Exception:
            pass

        self._preferred_size = QSize(
            min(max_pop.width(), scaled.width() + self._chrome_w),
            min(max_pop.height(), scaled.height() + self._chrome_h),
        )

        try:
            self._image_label.setPixmap(scaled)
        except Exception:
            pass

    def _refresh_image(self) -> None:
        if 0 <= self._current_index < len(self._image_paths):
            try:
                self._current_pixmap = QPixmap(self._image_paths[self._current_index])
            except Exception:
                self._current_pixmap = None

            self._render_pixmap()
            self._step_label.setText(f"Step {self._current_index + 1} of {len(self._image_paths)}")
            self._next.setText("Close" if self._current_index == len(self._image_paths) - 1 else "Next")
            try:
                self._back.setEnabled(self._current_index > 0)
            except Exception:
                pass

            if self.isVisible():
                try:
                    self.resize(self._preferred_size)
                    self._center_in_parent()
                except Exception:
                    pass

    def sizeHint(self) -> QSize:
        return QSize(int(self._preferred_size.width()), int(self._preferred_size.height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._card.setGeometry(0, 0, self.width(), self.height())
        # Keep the image scaled correctly when the popover resizes.
        try:
            self._render_pixmap()
        except Exception:
            pass

    def paintEvent(self, event):
        super().paintEvent(event)

    def show_centered(self) -> None:
        try:
            self._current_index = 0
            self._refresh_image()
            
            parent = self.parentWidget()
            if parent is not None:
                self.resize(self.sizeHint())
                self._center_in_parent()
                self.raise_()
                self.setVisible(True)
        except Exception:
            self.setVisible(True)

    def _center_in_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        pw = parent.width()
        ph = parent.height()
        w = self.width()
        h = self.height()
        x = int((pw - w) / 2)
        y = int((ph - h) / 2)
        self.move(x, y)


class _TipPopover(QFrame):
    closed = Signal()
    next_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("TipPopover")
        self.setVisible(False)

        self._arrow_dir: str = "up"  # "up" or "down"
        self._arrow_x = 120
        self._radius = 14
        self._arrow_w = 18
        self._arrow_h = 10

        self.setStyleSheet(
            "QFrame#TipPopover { background: transparent; }"
            "QLabel { color: white; }"
        )

        self._card = QFrame(self)
        self._card.setObjectName("TipCard")
        self._card.setStyleSheet(
            "QFrame#TipCard {"
            f"background: {_BLUE};"
            "border-radius: 14px;"
            "}"
        )

        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self._title = QLabel("")
        self._title.setStyleSheet("font-weight: 900; font-size: 18px;")
        try:
            self._title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        except Exception:
            pass
        top.addWidget(self._title, 1)

        self._close = QToolButton()
        self._close.setText("✕")
        self._close.setFixedSize(42, 42)
        self._close.setStyleSheet(
            "QToolButton { background: transparent; color: white; border: none; font-weight: 900; font-size: 30px; }"
            "QToolButton:hover { color: rgba(255,255,255,0.88); }"
        )
        self._close.clicked.connect(self._on_close)
        top.addWidget(self._close, 0)
        lay.addLayout(top)

        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setStyleSheet("font-size: 14px; line-height: 1.2;")
        lay.addWidget(self._body)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        bottom.addStretch(1)

        self._next = QPushButton("Next")
        self._next.setMinimumHeight(36)
        self._next.setMinimumWidth(104)
        try:
            self._next.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        except Exception:
            pass
        self._next.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.95); color: #1b3b78; border: none; border-radius: 8px; font-weight: 900; padding: 0 14px; }"
            "QPushButton:hover { background: white; }"
            "QPushButton:disabled { background: rgba(255,255,255,0.55); color: rgba(27,59,120,0.75); }"
        )
        self._next.clicked.connect(lambda: self.next_clicked.emit())
        bottom.addWidget(self._next, 0, Qt.AlignRight)
        lay.addLayout(bottom)

        # Soft shadow for the whole popover.
        try:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(26)
            eff.setOffset(0, 10)
            eff.setColor(QColor(0, 0, 0, 140))
            self._card.setGraphicsEffect(eff)
        except Exception:
            pass

    def _on_close(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def set_content(
        self,
        *,
        title: str,
        body: str,
        next_text: str = "Next",
        next_enabled: bool = True,
        show_next: bool = True,
    ) -> None:
        self._title.setText(title)
        self._body.setText(body)
        self._next.setVisible(bool(show_next))
        self._next.setText(next_text)
        self._next.setEnabled(bool(next_enabled))
        try:
            fm = QFontMetrics(self._next.font())
            w = int(fm.horizontalAdvance(next_text)) + 28
            self._next.setMinimumWidth(max(104, w))
        except Exception:
            pass

    def set_anchor(
        self,
        *,
        anchor_global: QPoint,
        prefer_below: bool = True,
        window_rect_global: QRectF | None = None,
        arrow_gap: int = 10,
    ) -> None:
        # Compute popover placement and arrow direction.
        if window_rect_global is None:
            try:
                top_left = self.parentWidget().mapToGlobal(QPoint(0, 0))  # type: ignore[union-attr]
                window_rect_global = QRectF(top_left.x(), top_left.y(), float(self.parentWidget().width()), float(self.parentWidget().height()))  # type: ignore[union-attr]
            except Exception:
                window_rect_global = QRectF(float(anchor_global.x() - 300), float(anchor_global.y() - 200), 600.0, 400.0)

        w = self.sizeHint().width()
        h = self.sizeHint().height()

        margin = 10

        # Start with below.
        x = int(anchor_global.x() - (w / 2))
        y_below = int(anchor_global.y() + arrow_gap)
        y_above = int(anchor_global.y() - arrow_gap - h)

        fits_below = (y_below + h) <= int(window_rect_global.y() + window_rect_global.height() - margin)
        fits_above = y_above >= int(window_rect_global.y() + margin)

        place_below = prefer_below
        if place_below and not fits_below and fits_above:
            place_below = False
        if (not place_below) and not fits_above and fits_below:
            place_below = True

        y = y_below if place_below else y_above
        self._arrow_dir = "up" if place_below else "down"

        min_x = int(window_rect_global.x() + margin)
        max_x = int(window_rect_global.x() + window_rect_global.width() - w - margin)
        x = max(min_x, min(max_x, x))

        min_y = int(window_rect_global.y() + margin)
        max_y = int(window_rect_global.y() + window_rect_global.height() - h - margin)
        y = max(min_y, min(max_y, y))

        # Arrow X relative to the popover.
        self._arrow_x = int(anchor_global.x() - x)
        self._arrow_x = max(self._radius + 18, min(w - self._radius - 18, self._arrow_x))

        # Convert global placement to parent coords.
        try:
            parent = self.parentWidget()
            local = parent.mapFromGlobal(QPoint(x, y)) if parent is not None else QPoint(x, y)
        except Exception:
            local = QPoint(x, y)

        self.move(local)

    def sizeHint(self) -> QSize:
        try:
            sh = self._card.sizeHint()
            w = max(360, int(sh.width()))
            h = int(sh.height()) + int(self._arrow_h)
            return QSize(w, max(140, h))
        except Exception:
            return QSize(440, 200)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Card sits below/above arrow.
        if self._arrow_dir == "up":
            self._card.setGeometry(0, self._arrow_h, self.width(), self.height() - self._arrow_h)
        else:
            self._card.setGeometry(0, 0, self.width(), self.height() - self._arrow_h)

    def paintEvent(self, event):
        # Draw the arrow (the card itself is a child).
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_BLUE))

        ax = float(self._arrow_x)
        aw = float(self._arrow_w)
        ah = float(self._arrow_h)

        if self._arrow_dir == "up":
            y0 = 0.0
            pts = [
                QPoint(int(ax), int(y0)),
                QPoint(int(ax - aw / 2), int(y0 + ah)),
                QPoint(int(ax + aw / 2), int(y0 + ah)),
            ]
        else:
            y0 = float(self.height())
            pts = [
                QPoint(int(ax), int(y0)),
                QPoint(int(ax - aw / 2), int(y0 - ah)),
                QPoint(int(ax + aw / 2), int(y0 - ah)),
            ]

        try:
            p.drawPolygon(QPolygon(pts))
        except Exception:
            try:
                p.drawPolygon(pts)
            except Exception:
                pass


class _TutorialSpotlight(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setVisible(False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._target_rect: QRect | None = None
        self._target_widget: QWidget | None = None
        self._target_widget_rect: QRect | None = None

    def set_target_widget(self, w: QWidget | None) -> None:
        self._target_widget = None
        self._target_widget_rect = None

        if w is None or not w.isVisible():
            self._target_rect = None
            self.update()
            return
        try:
            tl = w.mapToGlobal(QPoint(0, 0))
            br = w.mapToGlobal(QPoint(w.width(), w.height()))
            parent = self.parentWidget()
            if parent is not None:
                tl = parent.mapFromGlobal(tl)
                br = parent.mapFromGlobal(br)
            r = QRect(tl, br)
            self._target_widget = w
            self._target_widget_rect = QRect(tl, br)
            pad = 8
            self._target_rect = r.adjusted(-pad, -pad, pad, pad)
        except Exception:
            self._target_rect = None
            self._target_widget = None
            self._target_widget_rect = None
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Dim the whole window.
        overlay = QColor(0, 0, 0, 150)
        p.fillRect(self.rect(), overlay)

        # Re-draw the target widget at full brightness on top of the dim.
        tw = getattr(self, "_target_widget", None)
        wr = getattr(self, "_target_widget_rect", None)
        if tw is not None and wr is not None:
            try:
                pm = tw.grab()
                # Clip the re-drawn widget to rounded corners so it doesn't "square off".
                rr = 12.0
                clip_r = QRectF(wr)
                try:
                    clip = QPainterPath()
                    clip.addRoundedRect(clip_r, rr, rr)
                    p.save()
                    p.setClipPath(clip)
                    p.drawPixmap(wr.topLeft(), pm)
                    p.restore()
                except Exception:
                    p.drawPixmap(wr.topLeft(), pm)
            except Exception:
                pass

        # Optional outline around the spotlight.
        if self._target_rect is not None:
            try:
                cut = QRectF(self._target_rect)
                rr = 12.0
                pen = p.pen()
                pen.setWidth(2)
                pen.setColor(QColor(_BLUE))
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(cut, rr, rr)
            except Exception:
                pass
_TILE_PX = 170
_GRID_SPACING_PX = 18


def _ui_asset_path(*parts: str):
    """Resolve a path under tunesync_app/ui for dev + PyInstaller builds."""
    try:
        import sys
        from pathlib import Path

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return (Path(str(meipass)).resolve() / "tunesync_app" / "ui" / Path(*parts)).resolve()
        return (Path(__file__).resolve().parent / Path(*parts)).resolve()
    except Exception:
        return None


def _svg_icon(name: str, size: int = 18) -> QIcon:
    """Load an SVG from tunesync_app/ui/icons and render to a QIcon."""
    try:
        p = _ui_asset_path("icons", name)
        if not p or (not p.exists()):
            return QIcon()

        # Render at device pixel ratio so SVGs stay crisp on HiDPI.
        dpr = 1.0
        try:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            if screen is not None:
                dpr = float(screen.devicePixelRatio())
        except Exception:
            dpr = 1.0

        renderer = QSvgRenderer(str(p))
        px = max(1, int(round(size * dpr)))
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        try:
            pm.setDevicePixelRatio(dpr)
        except Exception:
            pass
        painter = QPainter(pm)
        # Important: once a devicePixelRatio is set on the pixmap, paint in
        # logical (device-independent) coordinates to avoid double-scaling.
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return QIcon(pm)
    except Exception:
        return QIcon()


def _apply_pointer_cursor(root: QWidget) -> None:
    """Make clickable controls feel consistent (pointer cursor)."""
    try:
        for btn in root.findChildren(QAbstractButton):
            try:
                btn.setCursor(Qt.PointingHandCursor)
            except Exception:
                pass
    except Exception:
        pass


class _NotificationRow(QFrame):
    def __init__(self, text: str, *, kind: str = "info", on_close=None):
        super().__init__()
        self._raw_text = text
        self._on_close = on_close
        self.setStyleSheet(
            "QFrame { background: #0f1216; border: none; border-radius: 12px; }"
        )

        # Keep rows compact and avoid vertical stretching.
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet("font-size: 12px;")
        if kind == "success":
            dot.setStyleSheet(f"color: {_GREEN}; font-size: 12px;")
        elif kind == "error":
            dot.setStyleSheet("color: #ff5a5f; font-size: 12px;")
        else:
            dot.setStyleSheet("color: #7f8793; font-size: 12px;")
        lay.addWidget(dot, 0)

        self._lbl = QLabel("")
        self._lbl.setWordWrap(False)
        self._lbl.setStyleSheet("color: #e7eaf0; font-size: 13px;")
        self._lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._lbl.setFixedHeight(18)
        lay.addWidget(self._lbl, 1)

        close_btn = QToolButton()
        close_btn.setText("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QToolButton { background: transparent; color: #a9b0bb; border: none; font-weight: 900; }"
            "QToolButton:hover { color: #e7eaf0; }"
        )
        close_btn.clicked.connect(self._handle_close)
        lay.addWidget(close_btn, 0, Qt.AlignRight)

        self._set_elided()

    def _set_elided(self) -> None:
        try:
            metrics = QFontMetrics(self._lbl.font())
            # Leave some breathing room for layout + dot.
            max_w = max(10, int(self.width()) - 60)
            self._lbl.setText(metrics.elidedText(self._raw_text or "", Qt.ElideRight, max_w))
        except Exception:
            try:
                self._lbl.setText(self._raw_text or "")
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_elided()

    def _handle_close(self) -> None:
        try:
            if callable(self._on_close):
                self._on_close()
        except Exception:
            pass


class _StorageBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(18)
        self.setStyleSheet("background: #0f1216; border-radius: 9px;")

        self._green = QFrame(self)
        self._green.setStyleSheet(f"background: {_GREEN}; border-radius: 9px;")
        self._orange = QFrame(self)
        self._orange.setStyleSheet(f"background: {_ORANGE}; border-radius: 9px;")
        self._g = 0
        self._o = 0

    def set_values(self, green: int, orange: int) -> None:
        self._g = max(0, int(green))
        self._o = max(0, int(orange))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        total = self._g + self._o
        w = self.width()
        if total <= 0:
            self._green.setGeometry(0, 0, 0, self.height())
            self._orange.setGeometry(0, 0, 0, self.height())
            return

        gw = int(w * (self._g / total))
        ow = w - gw
        self._green.setGeometry(0, 0, gw, self.height())
        self._orange.setGeometry(gw, 0, ow, self.height())


def _pill(color: str, text: str) -> str:
    return (
        "QLabel {"
        f"background: {color};"
        "color: white;"
        "padding: 3px 10px;"
        "border-radius: 10px;"
        "font-weight: 600;"
        "}"
    )


@dataclass
class CardModel:
    title: str
    status: str  # Synced | Syncing…
    progress: Optional[int] = None
    image_url: Optional[str] = None
    creator: Optional[str] = None
    playlist_id: Optional[str] = None


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self.clicked.emit()
                event.accept()
                return
        except Exception:
            pass
        super().mousePressEvent(event)


class RoundedPixmapLabel(QLabel):
    def __init__(self, *, radius: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._radius = radius
        self._src: Optional[QPixmap] = None

    def setSourcePixmap(self, pm: QPixmap) -> None:
        self._src = pm
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), float(self._radius), float(self._radius))
        painter.setClipPath(path)

        painter.fillRect(rect, QColor("#1a1e24"))
        if self._src is None or self._src.isNull():
            return

        target = rect.size()
        scaled = self._src.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, int((scaled.width() - rect.width()) / 2))
        y = max(0, int((scaled.height() - rect.height()) / 2))
        cropped = scaled.copy(x, y, rect.width(), rect.height())
        painter.drawPixmap(0, 0, cropped)


class _StatusOverlay(QFrame):
    def __init__(self, parent: QWidget, *, radius: int, bar_width: int):
        super().__init__(parent)
        self._radius = int(radius)
        self._bar_width = int(bar_width)
        self._bar_color: Optional[str] = None
        self._frozen = False

        # Tuned to match the design: slightly smaller and slightly higher.
        self._snowflake_size = 60
        self._snowflake_y_offset = -22

        self.setObjectName("StatusOverlay")
        self.setStyleSheet("background: transparent;")

    def set_bar_color(self, color: Optional[str]) -> None:
        self._bar_color = color
        self.update()

    def set_mode_frozen(self, frozen: bool) -> None:
        self._frozen = bool(frozen)
        if self._frozen:
            self._bar_color = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), float(self._radius), float(self._radius))
        painter.setClipPath(path)

        if self._bar_color:
            painter.fillRect(0, 0, self._bar_width, rect.height(), QColor(self._bar_color))

        if self._frozen:
            px = int(self._snowflake_size)
            icon = _svg_icon("snowflake.svg", px)
            pm = icon.pixmap(px, px)
            x = int((rect.width() - px) / 2)
            y = int((rect.height() - px) / 2) + int(self._snowflake_y_offset)
            painter.drawPixmap(max(0, x), max(0, y), pm)


class PlaylistCard(QFrame):
    def __init__(self, model: CardModel, *, is_add: bool = False, on_click=None, on_toggle_selected=None):
        super().__init__()
        self.setObjectName("PlaylistCard")
        self.setFixedSize(_TILE_PX, _TILE_PX)

        self._is_add = bool(is_add)

        # Keep status accessible via hover tooltip.
        self.setToolTip(model.status)
        self.setToolTipDuration(5000)

        self._on_click = on_click
        if self._on_click is not None:
            self.setCursor(Qt.PointingHandCursor)

        self._selection_enabled = False
        self._selected = False
        self._on_toggle_selected = on_toggle_selected
        self._sel_mask: Optional[QFrame] = None
        self._check: Optional[QLabel] = None

        self._img_label: Optional[RoundedPixmapLabel] = None
        self._title_label: Optional[QLabel] = None
        self._creator_label: Optional[QLabel] = None
        self._status_overlay: Optional[_StatusOverlay] = None
        self._raw_title: str = model.title
        self._raw_creator: str = model.creator or ""
        self._playlist_id: str | None = model.playlist_id

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        if is_add:
            lay.addStretch(1)
            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            icon_lbl.setStyleSheet("background: transparent;")
            icon_px = int(36 * 1.5)  # 150%
            icon_lbl.setPixmap(_svg_icon("add.svg", icon_px).pixmap(icon_px, icon_px))
            lay.addWidget(icon_lbl)

            add = QLabel("Add")
            add.setAlignment(Qt.AlignHCenter)
            f2 = QFont()
            f2.setPointSize(14)
            f2.setBold(True)
            add.setFont(f2)
            add.setStyleSheet(f"color: {_TEXT}; background: transparent;")
            lay.addWidget(add)
            lay.addStretch(2)
            self.setStyleSheet(
                "QFrame#PlaylistCard {"
                f"background: {_PANEL};"
                "border: 2px solid #2b2f36;"
                "border-radius: 18px;"
                "}"
            )
            return

        # Full-tile cover art background (loaded async)
        self._img_label = RoundedPixmapLabel(radius=18, parent=self)
        self._img_label.setGeometry(0, 0, self.width(), self.height())
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("border-radius: 18px; background: #1a1e24;")

        # Dimmer overlay to keep text readable
        self._dimmer = QFrame(self)
        self._dimmer.setObjectName("Dimmer")
        self._dimmer.setGeometry(0, 0, self.width(), self.height())
        self._dimmer.setStyleSheet(
            "QFrame#Dimmer {"
            "border-radius: 18px;"
            "background: rgba(0, 0, 0, 0.35);"
            "}"
        )

        # Status overlay paints the left bar clipped by the rounded tile,
        # and (for Frozen) a centered snowflake overlay.
        self._status_overlay = _StatusOverlay(self, radius=18, bar_width=10)
        self._status_overlay.setGeometry(0, 0, self.width(), self.height())
        self._status_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Overlay container
        overlay = QFrame(self)
        overlay.setObjectName("Overlay")
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setStyleSheet(
            "QFrame#Overlay {"
            "border-radius: 18px;"
            "background: transparent;"
            "}"
        )

        v = QVBoxLayout(overlay)
        # No outer margins so the bottom-left title panel can sit flush to the tile edges.
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self._apply_status(model.status)

        # Bottom-left info panel: creator above title
        v.addStretch(1)

        # Bottom-left title/creator panel (flush to tile edge) with asymmetric radii.
        panel = QFrame()
        panel.setObjectName("TextPanel")
        panel.setStyleSheet(
            "QFrame#TextPanel {"
            f"background: {_DARK_BG};"
            "border-top-left-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "border-top-right-radius: 18px;"
            "border-bottom-left-radius: 18px;"
            "}"
        )
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(10, 8, 10, 8)
        panel_lay.setSpacing(4)

        self._creator_label = QLabel(model.creator or "")
        self._creator_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._creator_label.setStyleSheet("color: #a9b0bb; background: transparent;")
        self._creator_label.setFixedHeight(18)
        self._creator_label.setToolTip(self._raw_creator)
        self._creator_label.setToolTipDuration(8000)
        self._creator_label.setWordWrap(False)
        if not (self._raw_creator or "").strip():
            self._creator_label.setVisible(False)
            self._creator_label.setFixedHeight(0)
        panel_lay.addWidget(self._creator_label)

        self._title_label = _ClickableLabel()
        ft = QFont()
        ft.setPointSize(13)
        ft.setBold(False)
        self._title_label.setFont(ft)
        self._title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title_label.setStyleSheet("color: white; background: transparent;")
        self._title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self._title_label.setFixedHeight(22)
        self._title_label.setToolTip(self._raw_title)
        self._title_label.setToolTipDuration(8000)
        try:
            self._title_label.setCursor(Qt.PointingHandCursor)
            self._title_label.clicked.connect(self._open_source)
        except Exception:
            pass
        panel_lay.addWidget(self._title_label)

        v.addWidget(panel, 0, Qt.AlignLeft | Qt.AlignBottom)

        # Selection overlay + check badge (hidden unless selection mode)
        self._sel_mask = QFrame(self)
        self._sel_mask.setGeometry(0, 0, self.width(), self.height())
        self._sel_mask.setStyleSheet("background: rgba(255,255,255,0.16); border-radius: 18px;")
        self._sel_mask.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._sel_mask.setVisible(False)

        self._check = QLabel("✓", self)
        self._check.setAlignment(Qt.AlignCenter)
        self._check.setGeometry(self.width() - 42, 10, 32, 32)
        self._check.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._check.setStyleSheet(
            "background: rgba(255,255,255,0.92);"
            "color: #111;"
            "border-radius: 16px;"
            "font-weight: 900;"
            "font-size: 16px;"
        )
        self._check.setVisible(False)

        self.setStyleSheet(
            "QFrame#PlaylistCard {"
            "border-radius: 18px;"
            "}"
        )

        self._set_title_elided()

    def _apply_status(self, status: str) -> None:
        status_l = (status or "").strip().lower()
        if status_l == "frozen":
            if self._status_overlay is not None:
                self._status_overlay.set_mode_frozen(True)
            try:
                self._dimmer.setStyleSheet(
                    "QFrame#Dimmer {"
                    "border-radius: 18px;"
                    "background: rgba(0, 0, 0, 0.72);"
                    "}"
                )
            except Exception:
                pass
            return

        try:
            self._dimmer.setStyleSheet(
                "QFrame#Dimmer {"
                "border-radius: 18px;"
                "background: rgba(0, 0, 0, 0.00);"
                "}"
            )
        except Exception:
            pass

        if self._status_overlay is None:
            return

        self._status_overlay.set_mode_frozen(False)
        if status_l.startswith("synced"):
            bar = _GREEN
        elif status_l.startswith("sync") or status_l == "needs sync":
            bar = _ORANGE
        elif status_l.startswith("error"):
            bar = "#ff5a5f"
        else:
            bar = "#5b6270"
        self._status_overlay.set_bar_color(bar)

    def update_model(self, model: CardModel) -> None:
        if getattr(self, "_is_add", False):
            return

        self._raw_title = model.title
        self._raw_creator = model.creator or ""
        self._playlist_id = model.playlist_id

        # Keep status accessible via hover tooltip.
        try:
            self.setToolTip(model.status)
            self.setToolTipDuration(5000)
        except Exception:
            pass

        if self._title_label is not None:
            self._title_label.setToolTip(self._raw_title)
            self._title_label.setToolTipDuration(8000)

        if self._creator_label is not None:
            self._creator_label.setToolTip(self._raw_creator)
            self._creator_label.setToolTipDuration(8000)
            self._creator_label.setText(self._raw_creator)
            if not (self._raw_creator or "").strip():
                self._creator_label.setVisible(False)
                self._creator_label.setFixedHeight(0)
            else:
                self._creator_label.setVisible(True)
                self._creator_label.setFixedHeight(18)

        self._apply_status(model.status)
        self._set_title_elided()
        self._set_creator_elided()

    def _open_source(self) -> None:
        pid = (getattr(self, "_playlist_id", None) or "").strip()
        if not pid:
            return
        try:
            QDesktopServices.openUrl(QUrl(f"https://open.spotify.com/playlist/{pid}"))
        except Exception:
            pass

    def mousePressEvent(self, event):
        if self._selection_enabled and event.button() == Qt.LeftButton:
            shift = False
            try:
                shift = bool(event.modifiers() & Qt.ShiftModifier)
            except Exception:
                shift = False

            if shift:
                # Let the controller do range selection.
                if self._on_toggle_selected is not None:
                    try:
                        # Pass current selected state so the controller can decide
                        # whether the range should be selected or deselected.
                        self._on_toggle_selected(self._selected, True)
                    except Exception:
                        pass
                event.accept()
                return

            self.set_selected(not self._selected)
            if self._on_toggle_selected is not None:
                try:
                    self._on_toggle_selected(self._selected, False)
                except Exception:
                    pass
            event.accept()
            return

        if self._on_click is not None and event.button() == Qt.LeftButton:
            try:
                self._on_click()
            finally:
                event.accept()
                return
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._img_label is not None:
            self._img_label.setGeometry(0, 0, self.width(), self.height())
        if getattr(self, "_dimmer", None) is not None:
            self._dimmer.setGeometry(0, 0, self.width(), self.height())
        if self._status_overlay is not None:
            self._status_overlay.setGeometry(0, 0, self.width(), self.height())
        # overlay is the direct child QFrame with objectName Overlay
        for child in self.findChildren(QFrame):
            if child.objectName() == "Overlay":
                child.setGeometry(0, 0, self.width(), self.height())
        if self._sel_mask is not None:
            self._sel_mask.setGeometry(0, 0, self.width(), self.height())
        if self._check is not None:
            self._check.setGeometry(self.width() - 42, 10, 32, 32)
        self._set_title_elided()
        self._set_creator_elided()

    def _set_title_elided(self) -> None:
        if self._title_label is None:
            return
        metrics = QFontMetrics(self._title_label.font())
        # Leave a little breathing room
        max_w = max(10, self.width() - 40)
        self._title_label.setText(metrics.elidedText(self._raw_title, Qt.ElideRight, max_w))

    def _set_creator_elided(self) -> None:
        if self._creator_label is None:
            return
        metrics = QFontMetrics(self._creator_label.font())
        max_w = max(10, self.width() - 40)
        self._creator_label.setText(metrics.elidedText(self._raw_creator, Qt.ElideRight, max_w))

    def set_cover_pixmap(self, pm: QPixmap) -> None:
        if self._img_label is None:
            return
        # Let the label scale + crop + round-clip consistently.
        self._img_label.setSourcePixmap(pm)

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        if enabled:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
            self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        if self._sel_mask is not None:
            self._sel_mask.setVisible(self._selected)
        if self._check is not None:
            self._check.setVisible(self._selected)


class _SpotifyPlaylistsWorker(QObject):
    done = Signal(list)
    failed = Signal(str)

    def run(self):
        try:
            from spotify_client import SpotifyClient

            self.done.emit(SpotifyClient.get_my_playlists())
        except Exception as e:
            self.failed.emit(str(e))


class _SpotifyAuthWorker(QObject):
    done = Signal(str)  # display name/id
    failed = Signal(str)

    def __init__(self, *, scope: str):
        super().__init__()
        self._scope = scope

    def run(self):
        try:
            from spotify_client import SpotifyClient

            # Fully automatic PKCE flow:
            # - starts a loopback HTTP server on the redirect URI's port
            # - opens the browser to Spotify auth
            # - captures the redirect automatically
            auth = SpotifyClient.create_pkce_auth(scope=self._scope, open_browser=True)
            auth.get_access_token(check_cache=False)

            import spotipy
            sp = spotipy.Spotify(auth_manager=auth, requests_timeout=10)
            # Validate the token with a playlist-scoped endpoint.
            sp.current_user_playlists(limit=1)
            who = "Spotify"
            try:
                me = sp.current_user() or {}
                who = (me.get("display_name") or me.get("id") or "Spotify")
            except Exception:
                # Some tokens may omit profile scopes; sign-in is still valid for playlist sync.
                pass
            self.done.emit(str(who))
        except Exception as e:
            self.failed.emit(str(e))


class _LocateTrackWorker(QObject):
    done = Signal(str)  # track_id
    failed = Signal(str, str)  # track_id, msg

    def __init__(self, *, track_id: str, url: str):
        super().__init__()
        self._track_id = track_id
        self._url = url

    def run(self):
        try:
            from dotenv import load_dotenv
            from pathlib import Path

            load_dotenv()
            root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
            if not root:
                self.failed.emit(self._track_id, "DOWNLOAD_ROOT is not set")
                return
            download_root = Path(root)

            from db import (
                get_conn,
                attach_file,
                clear_track_unavailable,
                set_track_manual_error,
            )
            from downloader import download_track_from_url
            from tagger import tag_tracks_in_db

            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT id, name, artist FROM tracks WHERE id = ?",
                    (self._track_id,),
                ).fetchone()
                if not row:
                    self.failed.emit(self._track_id, "Track not found in DB")
                    return

                res = download_track_from_url(
                    url=self._url,
                    track_id=self._track_id,
                    artist=row["artist"],
                    title=row["name"],
                    out_root=download_root,
                    aac_kbps=192,
                    progress=False,
                )
                if not res.ok or not res.final_path:
                    msg = res.error or "Download failed"
                    try:
                        set_track_manual_error(conn, self._track_id, msg)
                        conn.commit()
                    except Exception:
                        pass
                    self.failed.emit(self._track_id, msg)
                    return

                rel = res.final_path.relative_to(download_root)
                attach_file(conn, self._track_id, rel, download_root)
                clear_track_unavailable(conn, self._track_id, manual_url=self._url)
                set_track_manual_error(conn, self._track_id, None)
                conn.commit()

            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            # Tag after DB commit (uses DB metadata + file path).
            try:
                tag_tracks_in_db(download_root, track_ids=[self._track_id])
            except Exception:
                pass

            self.done.emit(self._track_id)
        except Exception as e:
            self.failed.emit(self._track_id, str(e))


class _MissingTrackRow(QFrame):
    def __init__(
        self,
        *,
        track_id: str,
        title: str,
        artist: str,
        cover_url: str | None,
        archived: bool,
        net: QNetworkAccessManager,
        on_locate,
        on_archive_toggle,
    ):
        super().__init__()
        self._track_id = track_id
        self.setStyleSheet("QFrame { background: #0f1216; border: none; border-radius: 12px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        self.img = QLabel()
        self.img.setFixedSize(54, 54)
        self.img.setStyleSheet("background: #1a1e24; border-radius: 14px;")
        self.img.setScaledContents(True)
        lay.addWidget(self.img, 0)

        text = QVBoxLayout()
        text.setSpacing(2)
        t = QLabel(title or "")
        t.setStyleSheet("color: #e7eaf0; font-weight: 700;")
        t.setFixedHeight(22)
        s = QLabel(artist or "")
        s.setStyleSheet("color: #a9b0bb;")
        s.setFixedHeight(20)
        text.addWidget(t)
        text.addWidget(s)
        lay.addLayout(text, 1)

        status = QLabel("NOT FOUND")
        status.setStyleSheet(f"color: {_ORANGE}; font-weight: 900; letter-spacing: 0.5px;")
        status.setFixedWidth(140)
        status.setAlignment(Qt.AlignCenter)
        lay.addWidget(status, 0)

        locate = QPushButton("LOCATE")
        locate.setFixedSize(104, 36)
        locate.setStyleSheet(
            "QPushButton {"
            "background: rgba(255,255,255,0.92);"
            "color: #111;"
            "border: none;"
            "border-radius: 18px;"
            "font-weight: 900;"
            "}"
            "QPushButton:hover { background: white; }"
        )
        locate.clicked.connect(lambda: on_locate(self._track_id))
        lay.addWidget(locate, 0)

        archive = QToolButton()
        archive.setText("Unarchive" if archived else "Archive")
        archive.setStyleSheet(
            "QToolButton {"
            "background: transparent;"
            "color: #a9b0bb;"
            "border: none;"
            "padding: 6px 6px;"
            "font-weight: 700;"
            "}"
            "QToolButton:hover { color: #e7eaf0; }"
        )
        archive.clicked.connect(lambda: on_archive_toggle(self._track_id, not archived))
        lay.addWidget(archive, 0)

        if cover_url:
            req = QNetworkRequest(QUrl(cover_url))
            req.setRawHeader(b"User-Agent", b"TuneSync")
            req.setRawHeader(b"Accept", b"image/*")
            try:
                req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
            except Exception:
                pass
            rep = net.get(req)

            def finished():
                rep.deleteLater()
                if rep.error() != QNetworkReply.NoError:
                    return
                pm = QPixmap()
                data = bytes(rep.readAll())
                if not pm.loadFromData(data):
                    return
                scaled = pm.scaled(self.img.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.img.setPixmap(scaled)

            rep.finished.connect(finished)


class SelectablePlaylistCard(QFrame):
    toggled = Signal(str, bool, bool)  # playlist_id, selected, shift

    def __init__(
        self,
        *,
        playlist_id: str,
        name: str,
        image_url: str | None,
        net: QNetworkAccessManager,
    ):
        super().__init__()
        self._pid = playlist_id
        self._selected = False
        self._raw_title = name or ""
        self.setFixedSize(_TILE_PX, _TILE_PX)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("SelectablePlaylistCard")

        # Cover
        self._img = RoundedPixmapLabel(radius=18, parent=self)
        self._img.setGeometry(0, 0, self.width(), self.height())
        self._img.setStyleSheet("border-radius: 18px; background: #1a1e24;")

        # Dimmer
        self._dimmer = QFrame(self)
        self._dimmer.setGeometry(0, 0, self.width(), self.height())
        self._dimmer.setStyleSheet("background: rgba(0,0,0,0.00); border-radius: 18px;")

        # Selection overlay + check badge
        self._sel_mask = QFrame(self)
        self._sel_mask.setGeometry(0, 0, self.width(), self.height())
        self._sel_mask.setStyleSheet("background: rgba(255,255,255,0.18); border-radius: 18px;")
        self._sel_mask.setVisible(False)

        self._check = QLabel("✓", self)
        self._check.setAlignment(Qt.AlignCenter)
        self._check.setGeometry(self.width() - 42, 10, 32, 32)
        self._check.setStyleSheet(
            "background: rgba(255,255,255,0.92);"
            "color: #111;"
            "border-radius: 16px;"
            "font-weight: 900;"
            "font-size: 16px;"
        )
        self._check.setVisible(False)

        # Bottom-left title panel (match homepage title formatting; no status bar, no creator).
        overlay = QFrame(self)
        overlay.setObjectName("Overlay")
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setStyleSheet(
            "QFrame#Overlay {"
            "background: transparent;"
            "border-radius: 18px;"
            "}"
        )
        v = QVBoxLayout(overlay)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addStretch(1)

        panel = QFrame()
        panel.setObjectName("TextPanel")
        panel.setStyleSheet(
            "QFrame#TextPanel {"
            f"background: {_DARK_BG};"
            "border-top-left-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "border-top-right-radius: 18px;"
            "border-bottom-left-radius: 18px;"
            "}"
        )
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(10, 8, 10, 8)
        panel_lay.setSpacing(0)

        self._title = QLabel()
        ft = QFont()
        ft.setPointSize(13)
        ft.setBold(False)
        self._title.setFont(ft)
        self._title.setStyleSheet("color: white; background: transparent;")
        self._title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title.setFixedHeight(22)
        self._title.setToolTip(self._raw_title)
        self._title.setToolTipDuration(8000)
        panel_lay.addWidget(self._title)

        v.addWidget(panel, 0, Qt.AlignLeft | Qt.AlignBottom)

        if image_url:
            req = QNetworkRequest(QUrl(image_url))
            req.setRawHeader(b"User-Agent", b"TuneSync")
            req.setRawHeader(b"Accept", b"image/*")
            try:
                req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
            except Exception:
                pass
            rep = net.get(req)

            def finished():
                rep.deleteLater()
                if rep.error() != QNetworkReply.NoError:
                    return
                data = bytes(rep.readAll())
                pm = QPixmap()
                if not pm.loadFromData(data):
                    return
                self._img.setSourcePixmap(pm)

            rep.finished.connect(finished)

        self.setStyleSheet("QFrame#SelectablePlaylistCard { border-radius: 18px; }")

        self._set_title_elided()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._img.setGeometry(0, 0, self.width(), self.height())
        self._dimmer.setGeometry(0, 0, self.width(), self.height())
        self._sel_mask.setGeometry(0, 0, self.width(), self.height())
        self._check.setGeometry(self.width() - 42, 10, 32, 32)
        for child in self.findChildren(QFrame):
            if child.objectName() == "Overlay":
                child.setGeometry(0, 0, self.width(), self.height())
        self._set_title_elided()

    def _set_title_elided(self) -> None:
        metrics = QFontMetrics(self._title.font())
        max_w = max(10, self.width() - 40)
        self._title.setText(metrics.elidedText(self._raw_title, Qt.ElideRight, max_w))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            shift = False
            try:
                shift = bool(event.modifiers() & Qt.ShiftModifier)
            except Exception:
                shift = False

            if shift:
                # Let the controller handle range selection.
                self.toggled.emit(self._pid, self._selected, True)
                event.accept()
                return

            self.set_selected(not self._selected)
            self.toggled.emit(self._pid, self._selected, False)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._sel_mask.setVisible(selected)
        self._check.setVisible(selected)


class AddPlaylistsPage(QWidget):
    added = Signal(list)
    cancelled = Signal()

    def __init__(self, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._net = QNetworkAccessManager(self)
        self._all: list[dict] = []
        self._filtered: list[dict] = []
        self._selected: set[str] = set()
        self._tile_by_pid: dict[str, SelectablePlaylistCard] = {}
        self._ordered_pids: list[str] = []
        self._selection_anchor_pid: str | None = None
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(90)
        self._reflow_timer.timeout.connect(self._relayout_only)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(18)

        # Header row (matches mock, but lives under the persistent top bar)
        head = QHBoxLayout()
        head.addStretch(1)

        self.selected_lbl = QLabel("0 Selected")
        self.selected_lbl.setStyleSheet("color: #e7eaf0; font-weight: 800;")
        head.addWidget(self.selected_lbl)

        self.add_btn = QPushButton("Add to Library")
        self.add_btn.setEnabled(False)
        self.add_btn.setFixedSize(150, 38)
        self.add_btn.setStyleSheet(
            "QPushButton {"
            f"background: {_BLUE};"
            "color: white;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "}"
            "QPushButton:disabled { background: #2b2f36; color: #7f8793; }"
        )
        self.add_btn.clicked.connect(self._add_confirm)
        head.addWidget(self.add_btn)

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(100, 38)
        cancel.setStyleSheet(
            "QPushButton {"
            "background: transparent;"
            "color: #e7eaf0;"
            "border: 1px solid #2b2f36;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "}"
            "QPushButton:hover { border-color: #3b414b; }"
        )
        cancel.clicked.connect(lambda: self.cancelled.emit())
        head.addWidget(cancel)
        outer.addLayout(head)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search library…")
        self.search.setFixedHeight(36)
        self.search.setStyleSheet(
            "QLineEdit {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 14px;"
            "color: #e7eaf0;"
            "}"
        )
        self.search.textChanged.connect(self._apply_filter)
        controls.addWidget(self.search, 2)
        controls.addStretch(1)
        outer.addLayout(controls)

        sec = QHBoxLayout()
        spot = QLabel("From Spotify")
        spot.setStyleSheet("color: #1db954; font-weight: 900; font-size: 18px;")
        sec.addWidget(spot)
        sec.addStretch(1)
        outer.addLayout(sec)

        self.loading = QLabel("Loading your playlists…")
        self.loading.setStyleSheet(f"color: {_MUTED};")
        outer.addWidget(self.loading)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Center the grid while still allowing vertical scrolling.
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(_GRID_SPACING_PX)
        self.grid.setVerticalSpacing(_GRID_SPACING_PX)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self._grid_wrapper = QWidget()
        wrap_lay = QHBoxLayout(self._grid_wrapper)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.setSpacing(0)
        wrap_lay.addStretch(1)
        wrap_lay.addWidget(self.content, 0, Qt.AlignTop)
        wrap_lay.addStretch(1)
        self.scroll.setWidget(self._grid_wrapper)
        outer.addWidget(self.scroll, 1)

        url_head = QLabel("From a URL")
        url_head.setStyleSheet("color: #e7eaf0; font-weight: 900; font-size: 18px;")
        outer.addWidget(url_head)

        self.url_in = QLineEdit()
        self.url_in.setPlaceholderText("Spotify playlist URL or URI")
        self.url_in.setFixedHeight(36)
        self.url_in.setStyleSheet(
            "QLineEdit {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 14px;"
            "color: #e7eaf0;"
            "}"
        )
        self.url_in.textChanged.connect(self._update_buttons)
        outer.addWidget(self.url_in)

    def refresh(self) -> None:
        self._selected = set()
        self.selected_lbl.setText("0 Selected")
        self.url_in.setText("")
        self.search.setText("")
        self.loading.setVisible(True)
        self.loading.setText("Loading your playlists…")

        # Avoid surprising browser popups: only load Spotify playlists if already authed.
        authed = False
        try:
            from spotify_client import SpotifyClient

            authed = SpotifyClient.has_cached_token()
        except Exception:
            authed = False

        if not authed:
            try:
                self._all = []
                self._filtered = []
                self._ordered_pids = []
                self._selected = set()
                self._selection_anchor_pid = None
                self._clear_grid()
            except Exception:
                pass
            self.loading.setText(
                "Sign in to Spotify in Settings to load your playlists.\n\n"
                "You can still add a playlist via URL below."
            )
            return

        self._start_load()

    def _start_load(self) -> None:
        self._thread = QThread(self)
        self._worker = _SpotifyPlaylistsWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(lambda _x: self._thread.quit())
        self._worker.failed.connect(lambda _x: self._thread.quit())
        self._thread.start()

    def _on_failed(self, msg: str) -> None:
        self.loading.setText(f"Failed to load Spotify playlists: {msg}")

    def _on_loaded(self, playlists: list) -> None:
        try:
            from db import get_conn

            conn = get_conn()
            rows = conn.execute("SELECT id FROM playlists").fetchall()
            already = {r["id"] for r in rows if r and r["id"]}
        except Exception:
            already = set()

        self._all = [p for p in playlists if p.get("id") and p.get("id") not in already]
        self.loading.setVisible(False)
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = (self.search.text() or "").strip().lower()
        if not q:
            self._filtered = list(self._all)
        else:
            self._filtered = [
                p
                for p in self._all
                if (q in (p.get("name") or "").lower()) or (q in (p.get("creator") or "").lower())
            ]
        self._render_grid()

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _remove_grid_items_only(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)

    def _render_grid(self) -> None:
        # Reuse existing widgets; only create/remove when the set changes.
        want = [p.get("id") for p in self._filtered if p.get("id")]
        self._ordered_pids = [pid for pid in want if isinstance(pid, str) and pid]

        want_set = set(self._ordered_pids)
        for pid in list(self._tile_by_pid.keys()):
            if pid not in want_set:
                w = self._tile_by_pid.pop(pid)
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass

        for p in self._filtered:
            pid = p.get("id")
            if not pid:
                continue
            if pid in self._tile_by_pid:
                continue
            tile = SelectablePlaylistCard(
                playlist_id=pid,
                name=p.get("name") or "",
                image_url=p.get("image_url"),
                net=self._net,
            )
            tile.toggled.connect(self._on_toggled)
            self._tile_by_pid[pid] = tile

        self._relayout_only()
        r = 0
        c = 0
        self._update_buttons()

    def _relayout_only(self) -> None:
        col_count = self._compute_columns()
        self._set_content_width(col_count)

        self._remove_grid_items_only()
        r = 0
        c = 0
        for pid in self._ordered_pids:
            tile = self._tile_by_pid.get(pid)
            if tile is None:
                continue
            tile.set_selected(pid in self._selected)
            self.grid.addWidget(tile, r, c)
            c += 1
            if c >= col_count:
                r += 1
                c = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            # Debounced reflow to avoid flicker during continuous resize.
            self._reflow_timer.start()
        except Exception:
            pass

    def _compute_columns(self) -> int:
        try:
            viewport_w = int(self.scroll.viewport().width())
        except Exception:
            viewport_w = int(self.width())

        spacing = int(self.grid.horizontalSpacing() if self.grid.horizontalSpacing() >= 0 else _GRID_SPACING_PX)
        unit = _TILE_PX + spacing
        cols = int((max(0, viewport_w) + spacing) // unit) if unit > 0 else 3
        return max(3, cols)

    def _set_content_width(self, col_count: int) -> None:
        spacing = int(self.grid.horizontalSpacing() if self.grid.horizontalSpacing() >= 0 else _GRID_SPACING_PX)
        w = (col_count * _TILE_PX) + max(0, (col_count - 1) * spacing)
        try:
            self.content.setFixedWidth(int(w))
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            new_cols = self._compute_columns()
            if getattr(self, "_last_cols", None) != new_cols:
                self._last_cols = new_cols
                self._render_grid()
        except Exception:
            pass

    def _on_toggled(self, pid: str, selected: bool, shift: bool = False) -> None:
        if shift:
            ordered = [x for x in (self._ordered_pids or []) if isinstance(x, str) and x]
            if not ordered:
                return

            anchor = getattr(self, "_selection_anchor_pid", None)
            if not anchor:
                anchor = next(iter(self._selected), pid)
                self._selection_anchor_pid = anchor

            # If clicked item is currently selected, shift-click deselects the range.
            deselect_mode = bool(selected)

            if anchor in ordered and pid in ordered:
                a = ordered.index(anchor)
                b = ordered.index(pid)
                lo, hi = (a, b) if a <= b else (b, a)
                for x in ordered[lo : hi + 1]:
                    tile = self._tile_by_pid.get(x)
                    if deselect_mode:
                        self._selected.discard(x)
                        if tile is not None:
                            tile.set_selected(False)
                    else:
                        self._selected.add(x)
                        if tile is not None:
                            tile.set_selected(True)
            else:
                # Fallback: toggle only this one.
                tile = self._tile_by_pid.get(pid)
                if deselect_mode:
                    self._selected.discard(pid)
                    if tile is not None:
                        tile.set_selected(False)
                else:
                    self._selected.add(pid)
                    if tile is not None:
                        tile.set_selected(True)

            self._update_buttons()
            return

        if selected:
            self._selected.add(pid)
        else:
            self._selected.discard(pid)
        self._selection_anchor_pid = pid
        self._update_buttons()

    def _update_buttons(self) -> None:
        url_has = bool((self.url_in.text() or "").strip())
        self.add_btn.setEnabled(bool(self._selected) or url_has)
        self.selected_lbl.setText(f"{len(self._selected)} Selected")

    @staticmethod
    def _extract_playlist_id(token: str) -> str:
        token = (token or "").strip()
        if not token:
            return ""
        if "open.spotify.com/playlist/" in token:
            return token.split("/playlist/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        if token.startswith("spotify:playlist:"):
            return token.split(":")[-1]
        return token

    @staticmethod
    def _is_valid_playlist_id(playlist_id: str) -> bool:
        # Spotify playlist IDs are base62 and typically 22 chars.
        return bool(re.fullmatch(r"[A-Za-z0-9]{22}", (playlist_id or "").strip()))

    def _add_confirm(self) -> None:
        to_add: list[tuple[str, str]] = []
        db_rows: list[dict] = []
        added_ids: list[str] = []

        by_id = {p["id"]: p for p in self._all if p.get("id")}
        for pid in sorted(self._selected):
            p = by_id.get(pid) or {}
            to_add.append((pid, p.get("name") or pid))
            added_ids.append(pid)
            db_rows.append(
                {
                    "id": pid,
                    "name": p.get("name") or pid,
                    "creator": p.get("creator"),
                    "image_url": p.get("image_url"),
                    "snapshot_id": None,
                }
            )

        raw_url = (self.url_in.text() or "").strip()
        url_pid = self._extract_playlist_id(raw_url)
        if raw_url and not url_pid:
            QMessageBox.warning(
                self,
                "Invalid playlist URL",
                "Paste a Spotify playlist URL, URI, or playlist ID.",
            )
            return

        if url_pid:
            if not self._is_valid_playlist_id(url_pid):
                QMessageBox.warning(
                    self,
                    "Invalid playlist ID",
                    "That does not look like a valid Spotify playlist ID.",
                )
                return

            if url_pid in added_ids:
                # Already selected in the Spotify list above.
                pass
            else:
                name = None
                creator = None
                image_url = None
                meta_error = ""
                try:
                    from spotify_client import SpotifyClient

                    meta = SpotifyClient.get_playlist_metadata(url_pid, silent=True)
                    name = (meta.get("name") or "").strip()
                    creator = meta.get("creator")
                    image_url = meta.get("image_url")
                except Exception as e:
                    meta_error = str(e)

                if not name:
                    detail = f"\n\nDetails: {meta_error}" if meta_error else ""
                    QMessageBox.warning(
                        self,
                        "Could not load playlist",
                        "TuneSync could not fetch that playlist from Spotify.\n\n"
                        "Sign in to Spotify in Settings and verify the playlist URL is correct."
                        f"{detail}",
                    )
                    return

                to_add.append((url_pid, name))
                added_ids.append(url_pid)
                db_rows.append(
                    {
                        "id": url_pid,
                        "name": name,
                        "creator": creator,
                        "image_url": image_url,
                        "snapshot_id": None,
                    }
                )

        if not to_add:
            return

        # Upsert into DB (and enable sync) so Home shows them immediately.
        try:
            from db import get_conn, upsert_playlist

            conn = get_conn()
            try:
                for row in db_rows:
                    upsert_playlist(conn, row)
                    try:
                        conn.execute("UPDATE playlists SET sync_enabled = 1 WHERE id = ?", (row["id"],))
                    except Exception:
                        pass
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass

        self.added.emit(added_ids)
        # HomeWindow listens to `added` and navigates back to Library.


class HomeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TuneSync")

        # Load persisted env (DOWNLOAD_ROOT etc.) for the UI.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        self._settings = QSettings("TuneSync", "TuneSync")
        self._db_load_retry_scheduled = False

        # In-memory promotion of newly-added playlists (cleared on sort change
        # or when navigating away from Home).
        self._promoted_playlist_ids: list[str] = []

        # Tutorial/onboarding state (shown only when Help is clicked).
        self._tutorial_completed = bool(self._settings.value("tutorial_completed", False) or False)
        self._tutorial_active = False
        self._tutorial_step = 0
        self._tutorial_tip_suppressed = False
        self._tutorial_step4_intro_shown = False

        # If DOWNLOAD_ROOT isn't set in the environment, fall back to saved setting.
        try:
            if not (os.getenv("DOWNLOAD_ROOT") or "").strip():
                saved_root = str(self._settings.value("download_root", "") or "").strip()
                if saved_root:
                    os.environ["DOWNLOAD_ROOT"] = saved_root
        except Exception:
            pass

        # If TUNESYNC_DB_PATH isn't set in the environment, fall back to saved setting.
        # This matters for packaged builds where the default DB location differs.
        try:
            if not (os.getenv("TUNESYNC_DB_PATH") or "").strip():
                saved_db = str(self._settings.value("db_path", "") or "").strip()
                if saved_db:
                    os.environ["TUNESYNC_DB_PATH"] = saved_db
        except Exception:
            pass
        # Resizable window. Default size is tuned for a good first impression,
        # but allow resizing and reflow the tile grid as width changes.
        outer_lr = 26 * 2
        min_w = outer_lr + (3 * _TILE_PX) + (2 * _GRID_SPACING_PX)
        self.setMinimumSize(int(min_w), 700)
        self.resize(1000, 700)

        root = QWidget()
        self.setCentralWidget(root)
        root.setStyleSheet(f"background: {_DARK_BG}; color: {_TEXT};")

        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(18)

        # Top bar (persistent across screens)
        top = QHBoxLayout()

        self.page_title = QLabel("Synced Library")
        ft = QFont()
        ft.setPointSize(22)
        ft.setBold(True)
        self.page_title.setFont(ft)
        top.addWidget(self.page_title)
        top.addStretch(1)

        self.btn_home = QToolButton()
        self.btn_home.setIcon(_svg_icon("home.svg", _TOPBAR_ICON_PX))
        self.btn_home.setIconSize(QSize(_TOPBAR_ICON_PX, _TOPBAR_ICON_PX))
        self.btn_home.setFixedSize(QSize(44, 44))
        self.btn_home.setAutoRaise(True)
        self.btn_home.setStyleSheet(
            "QToolButton {"
            "color: #d7dbe3;"
            "background: transparent;"
            "border: none;"
            "margin: 0px;"
            "padding: 4px 6px;"
            "}"
            "QToolButton:hover { background: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QToolButton:pressed { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 4px 6px; }"
        )
        self.btn_home.clicked.connect(self._show_library_page)
        top.addWidget(self.btn_home)

        self.btn_missing = QToolButton()
        self.btn_missing.setIcon(_svg_icon("missing.svg", _TOPBAR_ICON_PX))
        self.btn_missing.setIconSize(QSize(_TOPBAR_ICON_PX, _TOPBAR_ICON_PX))
        self.btn_missing.setFixedSize(QSize(44, 44))
        self.btn_missing.setAutoRaise(True)
        self.btn_missing.setStyleSheet(
            "QToolButton { background: transparent; border: none; margin: 0px; padding: 4px 6px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QToolButton:pressed { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 4px 6px; }"
        )
        self.btn_missing.clicked.connect(self._toggle_missing)
        top.addWidget(self.btn_missing)

        self.btn_notifications = QToolButton()
        self.btn_notifications.setIcon(_svg_icon("notifications.svg", _TOPBAR_ICON_PX))
        self.btn_notifications.setIconSize(QSize(_TOPBAR_ICON_PX, _TOPBAR_ICON_PX))
        self.btn_notifications.setFixedSize(QSize(44, 44))
        self.btn_notifications.setAutoRaise(True)
        self.btn_notifications.setStyleSheet(
            "QToolButton { background: transparent; border: none; margin: 0px; padding: 4px 6px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QToolButton:pressed { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 4px 6px; }"
        )
        self.btn_notifications.clicked.connect(self._toggle_notifications)
        top.addWidget(self.btn_notifications)

        self.btn_help = QToolButton()
        self.btn_help.setIcon(_svg_icon("help.svg", _TOPBAR_ICON_PX))
        self.btn_help.setIconSize(QSize(_TOPBAR_ICON_PX, _TOPBAR_ICON_PX))
        self.btn_help.setFixedSize(QSize(44, 44))
        self.btn_help.setAutoRaise(True)
        self.btn_help.setStyleSheet(
            "QToolButton { background: transparent; border: none; margin: 0px; padding: 4px 6px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QToolButton:pressed { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 4px 6px; }"
        )
        self.btn_help.clicked.connect(self._toggle_tutorial)
        top.addWidget(self.btn_help)

        self.btn_settings = QToolButton()
        self.btn_settings.setIcon(_svg_icon("settings.svg", _TOPBAR_ICON_PX))
        self.btn_settings.setIconSize(QSize(_TOPBAR_ICON_PX, _TOPBAR_ICON_PX))
        self.btn_settings.setFixedSize(QSize(44, 44))
        self.btn_settings.setAutoRaise(True)
        self.btn_settings.setStyleSheet(
            "QToolButton { background: transparent; border: none; margin: 0px; padding: 4px 6px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.06); border-radius: 8px; }"
            "QToolButton:pressed { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 4px 6px; }"
        )
        self.btn_settings.clicked.connect(self._show_settings_page)
        top.addWidget(self.btn_settings)

        outer.addLayout(top)

        # Main content switches between Library and Add Playlists
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # -----------------
        # Library page
        # -----------------
        self.library_page = QWidget()
        lib_outer = QVBoxLayout(self.library_page)
        lib_outer.setContentsMargins(0, 0, 0, 0)
        lib_outer.setSpacing(18)

        # Controls row
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search library…")
        self.search.setFixedHeight(36)
        self.search.setStyleSheet(
            "QLineEdit {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 14px;"
            "color: #e7eaf0;"
            "}"
        )
        controls.addWidget(self.search, 2)
        self.search.textChanged.connect(self._apply_filters)

        # Icon-only Filter/Sort menus (no need to show active choice).
        self._filter_choice: str | None = None
        self._sort_choice: str | None = None

        def _menu_icon_btn(icon_name: str) -> QToolButton:
            b = QToolButton()
            b.setIcon(_svg_icon(icon_name, _CONTROL_ICON_PX))
            b.setIconSize(QSize(_CONTROL_ICON_PX, _CONTROL_ICON_PX))
            b.setFixedSize(44, 36)
            b.setAutoRaise(False)
            b.setStyleSheet(
                "QToolButton {"
                "background: #0f1216;"
                "border: 1px solid #2b2f36;"
                "border-radius: 18px;"
                "padding: 0 10px;"
                "}"
                "QToolButton:hover { background: #151a20; }"
                "QToolButton::menu-indicator { image: none; }"
            )
            b.setPopupMode(QToolButton.InstantPopup)
            return b

        # Filter menu
        self.filter_btn = _menu_icon_btn("filter.svg")
        filter_menu = QMenu(self.filter_btn)
        filter_group = QActionGroup(self.filter_btn)
        filter_group.setExclusive(True)

        self._filter_actions: dict[str, QAction] = {}

        def add_filter_action(label: str, value: str | None, checked: bool = False) -> None:
            act = QAction(label, self.filter_btn)
            act.setCheckable(True)
            act.setChecked(checked)
            filter_group.addAction(act)
            filter_menu.addAction(act)

            self._filter_actions[value or ""] = act

            def on_triggered():
                self._filter_choice = value
                try:
                    self._settings.setValue("filter_choice", value or "")
                except Exception:
                    pass
                self._apply_filters()

            act.triggered.connect(on_triggered)

        add_filter_action("All", None, checked=True)
        add_filter_action("Synced", "Synced")
        add_filter_action("Needs sync", "Needs sync")
        add_filter_action("Syncing", "Syncing")
        add_filter_action("Frozen", "Frozen")
        add_filter_action("Errors", "Errors")
        self.filter_btn.setMenu(filter_menu)
        controls.addWidget(self.filter_btn, 0)

        # Restore filter selection.
        try:
            saved = str(self._settings.value("filter_choice", "") or "")
            if saved in self._filter_actions:
                self._filter_choice = saved or None
                self._filter_actions[saved].setChecked(True)
        except Exception:
            pass

        # Sort menu
        self.sort_btn = _menu_icon_btn("sort.svg")
        sort_menu = QMenu(self.sort_btn)
        sort_group = QActionGroup(self.sort_btn)
        sort_group.setExclusive(True)

        self._sort_actions: dict[str, QAction] = {}

        def add_sort_action(label: str, value: str | None, checked: bool = False) -> None:
            act = QAction(label, self.sort_btn)
            act.setCheckable(True)
            act.setChecked(checked)
            sort_group.addAction(act)
            sort_menu.addAction(act)

            self._sort_actions[value or ""] = act

            def on_triggered():
                self._sort_choice = value
                # Any manual sort change cancels "newly added" promotion.
                try:
                    self._promoted_playlist_ids = []
                except Exception:
                    pass
                try:
                    self._settings.setValue("sort_choice", value or "")
                except Exception:
                    pass
                self._apply_filters()

            act.triggered.connect(on_triggered)

        add_sort_action("Default", None, checked=True)
        add_sort_action("Latest changes", "Latest changes")
        add_sort_action("Playlist creation date", "Playlist creation date")
        add_sort_action("Sync status", "Sync status")
        add_sort_action("Playlist size", "Playlist size")
        add_sort_action("Creator", "Creator")
        self.sort_btn.setMenu(sort_menu)
        controls.addWidget(self.sort_btn, 0)

        # Restore sort selection.
        try:
            saved = str(self._settings.value("sort_choice", "") or "")
            if saved in self._sort_actions:
                self._sort_choice = saved or None
                self._sort_actions[saved].setChecked(True)
        except Exception:
            pass

        controls.addStretch(1)

        right = QHBoxLayout()
        right.setSpacing(10)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setFixedSize(140, 38)
        self.remove_btn.setEnabled(False)
        self.remove_btn.setVisible(False)
        self.remove_btn.setStyleSheet(
            "QPushButton {"
            "background: #b3261e;"
            "color: white;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "letter-spacing: 0.5px;"
            "}"
            "QPushButton:disabled { background: #2b2f36; color: #7f8793; }"
        )
        self.remove_btn.clicked.connect(self._remove_selected_playlists)
        right.addWidget(self.remove_btn, 0, Qt.AlignRight)

        self.toggle_sync_btn = QPushButton("Toggle Sync")
        self.toggle_sync_btn.setFixedSize(140, 38)
        self.toggle_sync_btn.setEnabled(False)
        self.toggle_sync_btn.setVisible(False)
        self.toggle_sync_btn.setStyleSheet(
            "QPushButton {"
            "background: #2b2f36;"
            "color: #e7eaf0;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "letter-spacing: 0.5px;"
            "}"
            "QPushButton:hover { background: #343a43; }"
            "QPushButton:disabled { background: #1a1e24; color: #7f8793; }"
        )
        self.toggle_sync_btn.clicked.connect(self._toggle_sync_selected_playlists)
        right.addWidget(self.toggle_sync_btn, 0, Qt.AlignRight)

        self.sync_now = QPushButton("SYNC NOW")
        self.sync_now.setFixedSize(140, 38)
        self._sync_now_style_default = (
            "QPushButton {"
            f"background: {_BLUE};"
            "color: white;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "letter-spacing: 0.5px;"
            "}"
            "QPushButton:hover { background: #2569d8; }"
            "QPushButton:pressed { background: #1f5fc8; }"
        )
        self._sync_now_style_running = (
            "QPushButton {"
            "background: #2b2f36;"
            "color: white;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 800;"
            "letter-spacing: 0.5px;"
            "}"
            "QPushButton:hover { background: #333842; }"
        )
        self._sync_now_style_cancel = (
            "QPushButton {"
            "background: #b3261e;"
            "color: white;"
            "border: none;"
            "border-radius: 10px;"
            "font-weight: 900;"
            "letter-spacing: 0.7px;"
            "}"
            "QPushButton:hover { background: #c53027; }"
            "QPushButton:pressed { background: #9b1f19; }"
        )
        self.sync_now.setStyleSheet(self._sync_now_style_default)
        right.addWidget(self.sync_now, 0, Qt.AlignRight)

        controls.addLayout(right)
        lib_outer.addLayout(controls)

        # Status + progress
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(14)

        self.run_status = QLabel("Idle")
        self.run_status.setStyleSheet("font-weight: 800; color: #e7eaf0;")
        status_row.addWidget(self.run_status, 0, Qt.AlignLeft)

        self.run_detail = QLabel("")
        self.run_detail.setStyleSheet(f"color: {_MUTED};")
        self.run_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_row.addWidget(self.run_detail, 1)

        self.details_btn = QToolButton()
        self.details_btn.setText("Details ▾")
        self.details_btn.setCheckable(True)
        self.details_btn.setStyleSheet(
            "QToolButton {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 10px;"
            "padding: 6px 10px;"
            "color: #e7eaf0;"
            "font-weight: 700;"
            "}"
            "QToolButton:checked { background: #151a20; }"
        )
        self.details_btn.toggled.connect(self._toggle_details)
        status_row.addWidget(self.details_btn, 0)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 5px;"
            "}"
            "QProgressBar::chunk {"
            f"background: {_BLUE};"
            "border-radius: 5px;"
            "}"
        )
        status_row.addWidget(self.progress, 1)

        lib_outer.addLayout(status_row)

        # Details dropdown (downloads list)
        self.details_panel = QFrame()
        self.details_panel.setVisible(False)
        # When hidden, collapse to 0px (used by splitter below).
        self.details_panel.setMaximumHeight(0)
        self.details_panel.setStyleSheet(
            "QFrame {"
            "background: #0b0e12;"
            "border: none;"
            "border-radius: 14px;"
            "}"
        )
        dp_lay = QVBoxLayout(self.details_panel)
        dp_lay.setContentsMargins(14, 12, 14, 12)
        dp_lay.setSpacing(10)

        self.details_title = QLabel("Downloaded")
        self.details_title.setStyleSheet("color: #e7eaf0; font-weight: 900;")
        dp_lay.addWidget(self.details_title)

        self.downloads_scroll = QScrollArea()
        self.downloads_scroll.setWidgetResizable(True)
        self.downloads_scroll.setFrameShape(QFrame.NoFrame)
        self.downloads_scroll.setStyleSheet("background: transparent;")
        self.downloads_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.downloads_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.downloads_content = QWidget()
        self.downloads_lay = QVBoxLayout(self.downloads_content)
        self.downloads_lay.setContentsMargins(0, 0, 0, 0)
        self.downloads_lay.setSpacing(8)
        self.downloads_scroll.setWidget(self.downloads_content)
        dp_lay.addWidget(self.downloads_scroll, 1)

        # Grid of cards
        self.library_scroll = QScrollArea()
        self.library_scroll.setWidgetResizable(True)
        self.library_scroll.setFrameShape(QFrame.NoFrame)
        self.library_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.library_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._library_grid_content = QWidget()
        self.grid = QGridLayout(self._library_grid_content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(_GRID_SPACING_PX)
        self.grid.setVerticalSpacing(_GRID_SPACING_PX)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self._library_grid_wrapper = QWidget()
        wrap_lay = QHBoxLayout(self._library_grid_wrapper)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.setSpacing(0)
        wrap_lay.addStretch(1)
        wrap_lay.addWidget(self._library_grid_content, 0, Qt.AlignTop)
        wrap_lay.addStretch(1)
        self.library_scroll.setWidget(self._library_grid_wrapper)

        # Splitter so the details dropdown is resizable.
        self.library_splitter = QSplitter(Qt.Vertical)
        self.library_splitter.setHandleWidth(10)
        self.library_splitter.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
            "QSplitter::handle:vertical { height: 10px; }"
        )
        self.library_splitter.setChildrenCollapsible(False)
        self.library_splitter.addWidget(self.details_panel)
        self.library_splitter.addWidget(self.library_scroll)
        self.library_splitter.setStretchFactor(0, 0)
        self.library_splitter.setStretchFactor(1, 1)
        self.library_splitter.setCollapsible(0, True)
        self.library_splitter.setCollapsible(1, False)

        lib_outer.addWidget(self.library_splitter, 1)

        self.stack.addWidget(self.library_page)

        # -----------------
        # Settings page
        # -----------------
        self.settings_page = QWidget()
        s_outer = QVBoxLayout(self.settings_page)
        s_outer.setContentsMargins(0, 0, 0, 0)
        s_outer.setSpacing(16)

        # Storage
        storage_title = QLabel("Storage")
        storage_title.setStyleSheet("font-weight: 900; font-size: 16px;")
        s_outer.addWidget(storage_title)

        storage_row = QHBoxLayout()
        storage_row.setContentsMargins(0, 0, 0, 0)
        storage_row.setSpacing(12)
        storage_lbl = QLabel("Local storage folder")
        storage_lbl.setStyleSheet(f"color: {_MUTED};")
        storage_row.addWidget(storage_lbl, 1)

        self.storage_path = QLabel("")
        self.storage_path.setStyleSheet("color: #e7eaf0;")
        self.storage_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.storage_path.setWordWrap(False)
        try:
            self.storage_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        except Exception:
            pass

        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setFixedHeight(30)
        self.browse_btn.setStyleSheet(
            "QPushButton { background: #e7eaf0; color: #111; border: none; border-radius: 10px; padding: 0 12px; font-weight: 800; }"
            "QPushButton:hover { background: white; }"
        )
        self.browse_btn.clicked.connect(self._browse_storage_folder)
        storage_row.addWidget(self.storage_path, 0, Qt.AlignVCenter)
        storage_row.addWidget(self.browse_btn, 0, Qt.AlignRight)
        s_outer.addLayout(storage_row)

        # Database
        db_row = QHBoxLayout()
        db_row.setContentsMargins(0, 0, 0, 0)
        db_row.setSpacing(12)
        db_lbl = QLabel("Database file")
        db_lbl.setStyleSheet(f"color: {_MUTED};")
        db_row.addWidget(db_lbl, 1)

        self.db_path = QLabel("")
        self.db_path.setStyleSheet("color: #e7eaf0;")
        self.db_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.db_path.setWordWrap(False)
        try:
            self.db_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        except Exception:
            pass

        self.db_browse_btn = QPushButton("Browse")
        self.db_browse_btn.setFixedHeight(30)
        self.db_browse_btn.setStyleSheet(
            "QPushButton { background: #e7eaf0; color: #111; border: none; border-radius: 10px; padding: 0 12px; font-weight: 800; }"
            "QPushButton:hover { background: white; }"
        )
        self.db_browse_btn.clicked.connect(self._browse_db_file)
        db_row.addWidget(self.db_path, 0, Qt.AlignVCenter)
        db_row.addWidget(self.db_browse_btn, 0, Qt.AlignRight)
        s_outer.addLayout(db_row)

        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(12)
        self.total_size_lbl = QLabel("Total library size")
        self.total_size_lbl.setStyleSheet(f"color: {_MUTED};")
        size_row.addWidget(self.total_size_lbl, 1)

        self.total_size_val = QLabel("—")
        self.total_size_val.setStyleSheet("color: #e7eaf0; font-weight: 800;")
        size_row.addWidget(self.total_size_val, 0, Qt.AlignRight)
        s_outer.addLayout(size_row)

        # (Removed) storage bar under the total size; it was redundant.

        # Accounts
        accounts_title = QLabel("Accounts")
        accounts_title.setStyleSheet("font-weight: 900; font-size: 16px;")
        try:
            accounts_title.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass
        s_outer.addWidget(accounts_title)

        self.spotify_card = QFrame()
        self.spotify_card.setStyleSheet("background: #14181d; border-radius: 16px;")
        sp_lay = QVBoxLayout(self.spotify_card)
        sp_lay.setContentsMargins(14, 12, 14, 12)
        sp_lay.setSpacing(8)

        sp_top = QHBoxLayout()
        sp_top.setContentsMargins(0, 0, 0, 0)
        sp_top.setSpacing(8)
        sp_icon = QLabel()
        sp_icon.setFixedSize(16, 16)
        try:
            sp_icon.setPixmap(_svg_icon("spotify.svg", 16).pixmap(16, 16))
        except Exception:
            pass
        sp_top.addWidget(sp_icon)

        sp_name = QLabel("Spotify")
        sp_name.setStyleSheet("font-weight: 900;")
        sp_top.addWidget(sp_name)
        sp_top.addStretch(1)

        self.spotify_logout = QPushButton("Log out")
        self.spotify_logout.setEnabled(False)
        self.spotify_logout.setFixedHeight(28)
        self.spotify_logout.setStyleSheet(
            "QPushButton { background: #e7eaf0; color: #111; border: none; border-radius: 10px; padding: 0 10px; font-weight: 800; }"
            "QPushButton:disabled { background: #2b2f36; color: #7f8793; }"
        )

        self.spotify_logout.clicked.connect(self._spotify_log_out)

        # Placeholder: Spotify sign-in UI is currently hard-coded; real auth wiring comes next.
        self.spotify_login = QPushButton("Sign in")
        self.spotify_login.setFixedHeight(28)
        self.spotify_login.setStyleSheet(
            "QPushButton { background: transparent; color: #e7eaf0; border: 1px solid #2b2f36; border-radius: 10px; padding: 0 10px; font-weight: 800; }"
            "QPushButton:hover { border-color: #3b414b; }"
        )

        self.spotify_login.clicked.connect(self._spotify_sign_in)

        sp_top.addWidget(self.spotify_login)
        sp_top.addWidget(self.spotify_logout)
        sp_lay.addLayout(sp_top)

        self.spotify_user = QLabel("")
        self.spotify_user.setStyleSheet(f"color: {_MUTED};")
        sp_lay.addWidget(self.spotify_user)

        self.spotify_counts = QLabel("")
        self.spotify_counts.setStyleSheet(f"color: {_MUTED}; font-weight: 400;")
        sp_lay.addWidget(self.spotify_counts)

        # Set initial auth UI state.
        self._update_spotify_auth_ui()

        s_outer.addWidget(self.spotify_card)

        # Rekordbox
        rekordbox_title = QLabel("Rekordbox")
        rekordbox_title.setStyleSheet("font-weight: 900; font-size: 16px;")
        try:
            rekordbox_title.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass
        s_outer.addWidget(rekordbox_title)

        self.rekordbox_card = QFrame()
        self.rekordbox_card.setStyleSheet("background: #14181d; border-radius: 16px;")
        rk_lay = QVBoxLayout(self.rekordbox_card)
        rk_lay.setContentsMargins(14, 12, 14, 12)
        rk_lay.setSpacing(8)

        rk_top = QHBoxLayout()
        rk_top.setContentsMargins(0, 0, 0, 0)
        rk_top.setSpacing(8)
        
        rk_name = QLabel("Connect to Rekordbox")
        rk_name.setStyleSheet("font-weight: 900;")
        rk_top.addWidget(rk_name, 1)

        self.rekordbox_learn = QPushButton("View Setup Guide")
        self.rekordbox_learn.setFixedHeight(28)
        self.rekordbox_learn.setStyleSheet(
            "QPushButton { background: transparent; color: #e7eaf0; border: 1px solid #2b2f36; border-radius: 10px; padding: 0 10px; font-weight: 800; }"
            "QPushButton:hover { border-color: #3b414b; }"
        )
        self.rekordbox_learn.clicked.connect(self._show_rekordbox_guide)
        rk_top.addWidget(self.rekordbox_learn, 0, Qt.AlignRight)
        rk_lay.addLayout(rk_top)

        rk_desc = QLabel("Learn how to set up XML import and refresh")
        rk_desc.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        rk_lay.addWidget(rk_desc)

        s_outer.addWidget(self.rekordbox_card)

        # Advanced (placed at the bottom, beneath Rekordbox)
        advanced_title = QLabel("Advanced")
        advanced_title.setStyleSheet("font-weight: 900; font-size: 16px;")
        try:
            advanced_title.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass
        s_outer.addWidget(advanced_title)

        dbg_row = QHBoxLayout()
        dbg_row.setContentsMargins(0, 0, 0, 0)
        dbg_row.setSpacing(12)
        dbg_lbl = QLabel("Debug mode")
        dbg_lbl.setStyleSheet(f"color: {_MUTED};")
        dbg_row.addWidget(dbg_lbl, 1)

        self.debug_toggle = QPushButton("Off")
        self.debug_toggle.setCheckable(True)
        self.debug_toggle.setFixedSize(72, 30)
        self.debug_toggle.setStyleSheet(
            "QPushButton { background: #2b2f36; color: #a9b0bb; border: 1px solid #2b2f36; border-radius: 15px; font-weight: 900; }"
            f"QPushButton:checked {{ background: {_BLUE}; color: white; border: 1px solid {_BLUE}; }}"
        )
        self.debug_toggle.toggled.connect(self._on_debug_mode_toggled)
        dbg_row.addWidget(self.debug_toggle, 0, Qt.AlignRight)
        s_outer.addLayout(dbg_row)

        s_outer.addStretch(1)

        self.stack.addWidget(self.settings_page)

        # Rekordbox setup guide carousel
        try:
            from pathlib import Path
            image_paths: list[str] = []
            for n in ("step1.png", "step2.png", "step3.png"):
                p = _ui_asset_path("images", n)
                if p is not None:
                    image_paths.append(str(p))
            self._rekordbox_carousel = _ImageCarouselPopover(image_paths=image_paths, parent=self)
        except Exception:
            self._rekordbox_carousel = None

        # -----------------
        # Add playlists page
        # -----------------
        self.add_page = AddPlaylistsPage(parent=self)
        self.add_page.added.connect(self._on_playlists_added)
        self.add_page.cancelled.connect(self._show_library_page)
        self.stack.addWidget(self.add_page)

        self._net = QNetworkAccessManager(self)
        self._cards_by_pid: dict[str, PlaylistCard] = {}
        self._add_tile = PlaylistCard(
            CardModel(title="Add", status=""),
            is_add=True,
            on_click=self._open_add_playlists,
        )
        self._all_playlists: list[dict] = []
        self._last_rendered_playlists: list[dict] = []
        self._pix_cache: dict[str, QPixmap] = {}
        self._sync_proc: Optional[QProcess] = None
        self._sync_cancel_requested = False
        self._sync_started_epoch: float | None = None
        self._sync_hover_cancel = False
        self.sync_now.clicked.connect(self._on_sync_button_clicked)
        try:
            self.sync_now.installEventFilter(self)
        except Exception:
            pass

        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(90)
        self._reflow_timer.timeout.connect(self._reflow_library_grid_now)

        self._downloads_timer = QTimer(self)
        self._downloads_timer.setInterval(1000)
        self._downloads_timer.timeout.connect(self._maybe_refresh_downloads)

        self._selected_playlist_ids: set[str] = set()
        self._selection_anchor_pid: str | None = None

        # Parsed run stats
        self._playlists_total: Optional[int] = None
        self._missing_total: Optional[int] = None
        self._downloaded_ok: Optional[int] = None
        self._download_total: Optional[int] = None
        self._tag_current: Optional[int] = None
        self._tag_total: Optional[int] = None
        self._last_line: str = ""
        self._run_started_at_sql: Optional[str] = None
        self._phase_text: str = ""
        # Live sync items for Details panel (queued/downloading/downloaded/failed).
        self._sync_items: dict[str, dict] = {}
        self._sync_items_order: list[str] = []
        self._sync_attempt: int | None = None
        self._sync_attempt_max: int | None = None
        self._notifications: list[dict] = []
        self._notifications_unread: int = 0
        self._notif_seq: int = 0
        self._synced_ids: set[str] = set()
        self._toggle_sync_mode: str | None = None  # "enable" | "disable" | None

        # Cursor consistency: pointer for clickable controls.
        _apply_pointer_cursor(root)

        # Notifications dropdown panel
        self.notifications_panel = QFrame(self)
        self.notifications_panel.setVisible(False)
        self.notifications_panel.setStyleSheet("background: #0b0e12; border-radius: 14px;")
        self.notifications_panel.setFixedWidth(380)
        n_lay = QVBoxLayout(self.notifications_panel)
        n_lay.setContentsMargins(14, 12, 14, 12)
        n_lay.setSpacing(10)

        n_header = QHBoxLayout()
        n_title = QLabel("Notifications")
        n_title.setStyleSheet("color: #e7eaf0; font-weight: 900;")
        n_header.addWidget(n_title, 1)

        self.notifications_clear_all = QPushButton("Clear all")
        self.notifications_clear_all.setFixedHeight(26)
        self.notifications_clear_all.setStyleSheet(
            "QPushButton { background: transparent; color: #a9b0bb; border: 1px solid #2b2f36; border-radius: 10px; padding: 0 10px; font-weight: 800; }"
            "QPushButton:hover { border-color: #3b414b; color: #e7eaf0; }"
        )
        self.notifications_clear_all.clicked.connect(self._clear_all_notifications)
        n_header.addWidget(self.notifications_clear_all, 0, Qt.AlignRight)
        n_lay.addLayout(n_header)

        self.notifications_list = QWidget()
        self.notifications_list_lay = QVBoxLayout(self.notifications_list)
        self.notifications_list_lay.setContentsMargins(0, 0, 0, 0)
        self.notifications_list_lay.setSpacing(8)
        try:
            self.notifications_list_lay.setAlignment(Qt.AlignTop)
        except Exception:
            pass
        self.notifications_scroll = QScrollArea()
        self.notifications_scroll.setWidgetResizable(True)
        self.notifications_scroll.setFrameShape(QFrame.NoFrame)
        self.notifications_scroll.setStyleSheet("background: transparent;")
        self.notifications_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.notifications_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.notifications_scroll.setWidget(self.notifications_list)
        self.notifications_scroll.setFixedHeight(320)
        n_lay.addWidget(self.notifications_scroll)

        # Missing/unavailable tracks dropdown panel
        self.missing_panel = QFrame(self)
        self.missing_panel.setVisible(False)
        self.missing_panel.setStyleSheet("background: #0b0e12; border-radius: 14px;")
        self.missing_panel.setFixedWidth(380)
        m_lay = QVBoxLayout(self.missing_panel)
        m_lay.setContentsMargins(14, 12, 14, 12)
        m_lay.setSpacing(10)

        m_title = QLabel("Missing tracks")
        m_title.setStyleSheet("color: #e7eaf0; font-weight: 900;")
        m_lay.addWidget(m_title)

        self.missing_list = QWidget()
        self.missing_list_lay = QVBoxLayout(self.missing_list)
        self.missing_list_lay.setContentsMargins(0, 0, 0, 0)
        self.missing_list_lay.setSpacing(8)
        try:
            self.missing_list_lay.setAlignment(Qt.AlignTop)
        except Exception:
            pass
        self.missing_scroll = QScrollArea()
        self.missing_scroll.setWidgetResizable(True)
        self.missing_scroll.setFrameShape(QFrame.NoFrame)
        self.missing_scroll.setStyleSheet("background: transparent;")
        self.missing_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.missing_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.missing_scroll.setWidget(self.missing_list)
        self.missing_scroll.setFixedHeight(320)
        m_lay.addWidget(self.missing_scroll)

        self._locate_threads: dict[str, QThread] = {}
        self._locate_workers: dict[str, _LocateTrackWorker] = {}

        try:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        except Exception:
            pass

        # Tutorial popover UI.
        self._spotlight = _TutorialSpotlight(self)
        self._spotlight.setGeometry(0, 0, self.width(), self.height())
        self._tip = _TipPopover(self)
        self._tip.closed.connect(self._tutorial_close)
        self._tip.next_clicked.connect(self._tutorial_next)

        # Load persisted notifications (last 10) so they survive restarts and Spotify sign-in/out.
        self._load_persisted_notifications()

        self._load_cards_from_db()

    def showEvent(self, event):
        super().showEvent(event)
        # After first show, viewport widths are reliable; reflow once.
        try:
            QTimer.singleShot(0, self._reflow_library_grid_now)
        except Exception:
            pass

    def _compute_library_columns(self) -> int:
        try:
            viewport_w = int(self.library_scroll.viewport().width())
        except Exception:
            viewport_w = int(self.width())

        spacing = int(self.grid.horizontalSpacing() if self.grid.horizontalSpacing() >= 0 else _GRID_SPACING_PX)
        unit = _TILE_PX + spacing
        cols = int((max(0, viewport_w) + spacing) // unit) if unit > 0 else 3
        return max(3, cols)

    def _set_library_content_width(self, col_count: int) -> None:
        spacing = int(self.grid.horizontalSpacing() if self.grid.horizontalSpacing() >= 0 else _GRID_SPACING_PX)
        w = (col_count * _TILE_PX) + max(0, (col_count - 1) * spacing)
        try:
            self._library_grid_content.setFixedWidth(int(w))
        except Exception:
            pass

    def _reflow_library_grid(self) -> None:
        try:
            self._reflow_timer.start()
        except Exception:
            pass

    def _reflow_library_grid_now(self) -> None:
        try:
            new_cols = self._compute_library_columns()
            if getattr(self, "_last_library_cols", None) == new_cols:
                return
            self._last_library_cols = new_cols
            self._set_library_content_width(new_cols)
            self._relayout_library_widgets(new_cols)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reflow tiles when width changes.
        self._reflow_library_grid()
        try:
            self._spotlight.setGeometry(0, 0, self.width(), self.height())
        except Exception:
            pass
        if getattr(self, "_tutorial_active", False):
            try:
                self._tutorial_refresh()
            except Exception:
                pass

    def _show_library_page(self) -> None:
        self.page_title.setText("Synced Library")
        self.stack.setCurrentWidget(self.library_page)
        self._reflow_library_grid()
        if self.notifications_panel.isVisible():
            self.notifications_panel.setVisible(False)
        if self._tutorial_active:
            # If the user navigated Home while we were on the Spotify tip,
            # treat that as "continue" to the Sync step.
            try:
                if int(getattr(self, "_tutorial_step", 0) or 0) == 2:
                    self._tutorial_step = 3
            except Exception:
                pass
            self._tutorial_refresh()

    def _show_settings_page(self) -> None:
        # Navigating away from Home clears the ephemeral "newly added" pinning.
        try:
            self._promoted_playlist_ids = []
        except Exception:
            pass
        self.page_title.setText("Settings")
        self.stack.setCurrentWidget(self.settings_page)
        self._refresh_settings()
        if self.notifications_panel.isVisible():
            self.notifications_panel.setVisible(False)
        if self._tutorial_active:
            # If the user clicked Settings directly from the welcome step,
            # advance to the Download Root step.
            try:
                if int(getattr(self, "_tutorial_step", 0) or 0) == 0:
                    # Skip Download Root if already configured.
                    if self._tutorial_has_download_root():
                        # If Spotify is also already connected, jump straight to Add.
                        if self._tutorial_has_spotify():
                            self._tutorial_step = 3
                            QTimer.singleShot(0, self._show_library_page)
                        else:
                            self._tutorial_step = 2
                    else:
                        self._tutorial_step = 1
                # If the user is on "Back to Settings" after Details, advance to Rekordbox step.
                if int(getattr(self, "_tutorial_step", 0) or 0) == 7:
                    self._tutorial_step = 8
            except Exception:
                pass
            self._tutorial_refresh()

    def _toggle_notifications(self) -> None:
        if self.notifications_panel.isVisible():
            self.notifications_panel.setVisible(False)
            return

        # Position under the notifications button, clamped within the window.
        try:
            margin = 10
            anchor = self.btn_notifications.mapTo(self, QPoint(0, self.btn_notifications.height() + 8))
            # Right-align the panel to the button.
            x = anchor.x() + self.btn_notifications.width() - self.notifications_panel.width()
            y = anchor.y()

            # Ensure the panel fits inside the window.
            max_panel_h = max(220, self.height() - margin * 2)
            self.notifications_scroll.setFixedHeight(min(320, max_panel_h - 70))
            self.notifications_panel.adjustSize()

            x = max(margin, min(x, self.width() - self.notifications_panel.width() - margin))
            y = max(margin, min(y, self.height() - self.notifications_panel.height() - margin))
            self.notifications_panel.move(QPoint(x, y))
            self.notifications_panel.raise_()
        except Exception:
            pass
        self.notifications_panel.setVisible(True)
        self._mark_notifications_read()

    def _toggle_missing(self) -> None:
        if self.missing_panel.isVisible():
            self.missing_panel.setVisible(False)
            return

        try:
            # Refresh each time it opens.
            self._refresh_missing_list()

            margin = 10
            anchor = self.btn_missing.mapTo(self, QPoint(0, self.btn_missing.height() + 8))
            x = anchor.x() + self.btn_missing.width() - self.missing_panel.width()
            y = anchor.y()

            max_panel_h = max(220, self.height() - margin * 2)
            self.missing_scroll.setFixedHeight(min(320, max_panel_h - 70))
            self.missing_panel.adjustSize()

            x = max(margin, min(x, self.width() - self.missing_panel.width() - margin))
            y = max(margin, min(y, self.height() - self.missing_panel.height() - margin))
            self.missing_panel.move(QPoint(x, y))
            self.missing_panel.raise_()
        except Exception:
            pass
        self.missing_panel.setVisible(True)

    def eventFilter(self, obj: QObject, event: object) -> bool:
        try:
            # Hover-to-cancel on the Sync button while syncing.
            if obj is getattr(self, "sync_now", None) and isinstance(event, QEvent):
                et = event.type()
                if et in {QEvent.Enter, QEvent.HoverEnter}:
                    self._sync_hover_cancel = True
                    self._refresh_sync_button_state()
                if et in {QEvent.Leave, QEvent.HoverLeave}:
                    self._sync_hover_cancel = False
                    self._refresh_sync_button_state()

            if isinstance(event, QEvent) and event.type() == QEvent.MouseButtonPress:
                try:
                    gp = event.globalPosition().toPoint()  # type: ignore[attr-defined]
                except Exception:
                    gp = event.globalPos()  # type: ignore[attr-defined]
                p = self.mapFromGlobal(gp)

                if self.notifications_panel.isVisible():
                    btn_p = self.btn_notifications.mapFromGlobal(gp)
                    if not self.notifications_panel.geometry().contains(p) and not self.btn_notifications.rect().contains(btn_p):
                        self.notifications_panel.setVisible(False)

                if self.missing_panel.isVisible():
                    btn_p = self.btn_missing.mapFromGlobal(gp)
                    if not self.missing_panel.geometry().contains(p) and not self.btn_missing.rect().contains(btn_p):
                        self.missing_panel.setVisible(False)
        except Exception:
            pass
        return super().eventFilter(obj, event)  # type: ignore[arg-type]

    def _is_sync_running(self) -> bool:
        try:
            return self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning
        except Exception:
            return False

    def _refresh_sync_button_state(self) -> None:
        # Keep the Sync button usable while a sync is running so the user can cancel.
        if self._is_sync_running():
            try:
                self.sync_now.setEnabled(True)
            except Exception:
                pass

            if getattr(self, "_sync_hover_cancel", False):
                try:
                    self.sync_now.setText("CANCEL")
                    self.sync_now.setStyleSheet(self._sync_now_style_cancel)
                except Exception:
                    pass
            else:
                try:
                    self.sync_now.setText("SYNCING…")
                    self.sync_now.setStyleSheet(self._sync_now_style_running)
                except Exception:
                    pass
            return

        # Not running
        try:
            self.sync_now.setEnabled(True)
            self.sync_now.setText("SYNC NOW")
            self.sync_now.setStyleSheet(self._sync_now_style_default)
        except Exception:
            pass

    def _on_sync_button_clicked(self) -> None:
        if self._is_sync_running():
            # Only treat it as cancel when the user is explicitly in the cancel affordance.
            if not getattr(self, "_sync_hover_cancel", False):
                return
            self._confirm_cancel_sync()
            return
        self._start_sync()

    def _confirm_cancel_sync(self) -> None:
        btn = QMessageBox.question(
            self,
            "Cancel sync",
            "Cancel the current sync and roll back changes from this run?\n\n"
            "This deletes files (and partial files) downloaded during this sync.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if btn != QMessageBox.Yes:
            return
        self._cancel_sync_and_rollback()

    def _cancel_sync_and_rollback(self) -> None:
        if not self._is_sync_running():
            return
        self._sync_cancel_requested = True
        try:
            self.run_status.setText("Cancelling…")
        except Exception:
            pass
        try:
            self._push_notification("Cancelling sync…", kind="info")
        except Exception:
            pass

        proc = self._sync_proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

        def _hard_kill():
            try:
                if self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning:
                    self._sync_proc.kill()
            except Exception:
                pass

        QTimer.singleShot(2500, _hard_kill)

    def _rollback_sync_run(self) -> None:
        # Best-effort rollback: remove DB file rows from this run and delete the corresponding files.
        from pathlib import Path

        root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
        if not root:
            return
        download_root = Path(root)
        if not download_root.exists():
            return

        start_sql = getattr(self, "_run_started_at_sql", None)
        start_epoch = getattr(self, "_sync_started_epoch", None)

        deleted = 0
        try:
            from db import get_conn

            conn = get_conn()
            try:
                if start_sql:
                    rows = conn.execute(
                        "SELECT track_id, file_path FROM files WHERE downloaded_at >= ?",
                        (start_sql,),
                    ).fetchall()
                else:
                    rows = []

                for r in rows:
                    try:
                        rel = str(r["file_path"])
                        p = download_root / rel
                        if p.exists():
                            p.unlink()
                            deleted += 1
                    except Exception:
                        pass

                if start_sql:
                    conn.execute("DELETE FROM files WHERE downloaded_at >= ?", (start_sql,))
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass

        # Also delete common partial-download artifacts created during this run.
        if start_epoch is not None:
            partial_exts = {".part", ".tmp", ".download"}
            try:
                for dirpath, _dirnames, filenames in os.walk(str(download_root)):
                    for fn in filenames:
                        try:
                            p = Path(dirpath) / fn
                            if p.suffix.lower() not in partial_exts and not fn.lower().endswith(".part"):
                                continue
                            st = p.stat()
                            if float(st.st_mtime) >= float(start_epoch) - 2.0:
                                p.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            self._load_cards_from_db()
            self._load_downloaded_details()
        except Exception:
            pass

        try:
            if deleted > 0:
                self._push_notification(f"Sync cancelled. Rolled back {deleted} file(s).", kind="info")
            else:
                self._push_notification("Sync cancelled. Rollback complete.", kind="info")
        except Exception:
            pass

    def _refresh_missing_list(self) -> None:
        while self.missing_list_lay.count():
            it = self.missing_list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        try:
            from db import get_conn, get_unavailable_tracks

            conn = get_conn()
            try:
                active = get_unavailable_tracks(conn, archived=False)
                archived = get_unavailable_tracks(conn, archived=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            active = []
            archived = []

        self._set_missing_icon(has_missing=bool(active or archived))

        if not active and not archived:
            empty = QLabel("No missing tracks.")
            empty.setStyleSheet("color: #a9b0bb;")
            self.missing_list_lay.addWidget(empty)
            return

        for r in active:
            cover_url = r["cover_url"] if (hasattr(r, "keys") and "cover_url" in r.keys()) else None
            self.missing_list_lay.addWidget(
                _MissingTrackRow(
                    track_id=r["id"],
                    title=r["name"],
                    artist=r["artist"],
                    cover_url=cover_url,
                    archived=bool(int(r["unavailable_archived"])),
                    net=self._net,
                    on_locate=self._locate_missing_track,
                    on_archive_toggle=self._archive_missing_track,
                )
            )

        if archived:
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: #2b2f36;")
            self.missing_list_lay.addWidget(sep)
            hdr = QLabel("Archived")
            hdr.setStyleSheet("color: #a9b0bb; font-weight: 800;")
            self.missing_list_lay.addWidget(hdr)
            for r in archived:
                cover_url = r["cover_url"] if (hasattr(r, "keys") and "cover_url" in r.keys()) else None
                self.missing_list_lay.addWidget(
                    _MissingTrackRow(
                        track_id=r["id"],
                        title=r["name"],
                        artist=r["artist"],
                        cover_url=cover_url,
                        archived=bool(int(r["unavailable_archived"])),
                        net=self._net,
                        on_locate=self._locate_missing_track,
                        on_archive_toggle=self._archive_missing_track,
                    )
                )

        self.missing_list_lay.addStretch(1)

    def _archive_missing_track(self, track_id: str, archived: bool) -> None:
        try:
            from db import get_conn, set_track_unavailable_archived

            conn = get_conn()
            set_track_unavailable_archived(conn, track_id, archived)
            conn.commit()
        except Exception:
            pass
        self._refresh_missing_list()

    def _locate_missing_track(self, track_id: str) -> None:
        url, ok = QInputDialog.getText(self, "Locate track", "Paste a YouTube or SoundCloud URL")
        if not ok:
            return
        url = (url or "").strip()
        if not url:
            return

        if track_id in self._locate_threads:
            self._push_notification("Locate already running for this track", kind="info")
            return

        thread = QThread(self)
        worker = _LocateTrackWorker(track_id=track_id, url=url)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _done(_tid: str):
            self._push_notification("Track located and downloaded", kind="success")
            try:
                self._load_cards_from_db()
            except Exception:
                pass
            try:
                self._refresh_missing_list()
            except Exception:
                pass

        def _failed(_tid: str, msg: str):
            self._push_notification(f"Locate failed: {msg}", kind="error")
            try:
                self._refresh_missing_list()
            except Exception:
                pass

        worker.done.connect(_done)
        worker.failed.connect(_failed)
        worker.done.connect(lambda _x: thread.quit())
        worker.failed.connect(lambda _x, _y: thread.quit())

        def _cleanup():
            self._locate_threads.pop(track_id, None)
            self._locate_workers.pop(track_id, None)
            try:
                thread.deleteLater()
            except Exception:
                pass

        thread.finished.connect(_cleanup)

        self._locate_threads[track_id] = thread
        self._locate_workers[track_id] = worker
        thread.start()

    def _push_notification(self, text: str, *, kind: str = "info") -> None:
        try:
            self._notif_seq = int(getattr(self, "_notif_seq", 0) or 0) + 1
        except Exception:
            self._notif_seq = 1
        nid = f"{int(time.time() * 1000)}-{self._notif_seq}"

        self._notifications.insert(0, {"id": nid, "text": text, "kind": kind, "ts": int(time.time())})
        # Persist only the most recent 10 (requirement).
        self._notifications = self._notifications[:10]
        self._notifications_unread += 1
        self._persist_notifications()
        self._refresh_notifications_icon()
        self._refresh_notifications_list()

    def _persist_notifications(self) -> None:
        try:
            payload = self._notifications[:10]
            self._settings.setValue("notifications_json", json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass

    def _load_persisted_notifications(self) -> None:
        try:
            raw = str(self._settings.value("notifications_json", "") or "").strip()
            if not raw:
                self._notifications = []
            else:
                data = json.loads(raw)
                if isinstance(data, list):
                    cleaned: list[dict] = []
                    for item in data[:10]:
                        if not isinstance(item, dict):
                            continue
                        text = str(item.get("text") or "").strip()
                        if not text:
                            continue
                        cleaned.append(
                            {
                                "id": str(item.get("id") or ""),
                                "text": text,
                                "kind": str(item.get("kind") or "info"),
                                "ts": int(item.get("ts") or 0),
                            }
                        )
                    self._notifications = cleaned[:10]
                else:
                    self._notifications = []
        except Exception:
            self._notifications = []

        # Persisted notifications are treated as already read on launch.
        self._notifications_unread = 0
        try:
            self._refresh_notifications_icon()
            self._refresh_notifications_list()
        except Exception:
            pass

    def _dismiss_notification(self, nid: str) -> None:
        nid = (nid or "").strip()
        if not nid:
            return
        try:
            self._notifications = [n for n in self._notifications if str(n.get("id") or "") != nid]
            self._persist_notifications()
            self._refresh_notifications_list()
        except Exception:
            pass

    def _clear_all_notifications(self) -> None:
        self._notifications = []
        self._notifications_unread = 0
        self._persist_notifications()
        self._refresh_notifications_icon()
        self._refresh_notifications_list()

    def _mark_notifications_read(self) -> None:
        self._notifications_unread = 0
        self._refresh_notifications_icon()

    def _refresh_notifications_icon(self) -> None:
        icon_name = "notification_unread.svg" if self._notifications_unread > 0 else "notifications.svg"
        self.btn_notifications.setIcon(_svg_icon(icon_name, _TOPBAR_ICON_PX))

    def _set_missing_icon(self, *, has_missing: bool) -> None:
        icon_name = "missing.svg" if has_missing else "no_missing.svg"
        try:
            self.btn_missing.setIcon(_svg_icon(icon_name, _TOPBAR_ICON_PX))
        except Exception:
            pass

    def _refresh_missing_icon(self) -> None:
        # Missing/unavailable tracks are those marked unavailable (regardless of archived)
        # and referenced by sync-enabled playlists, with no local file.
        has_missing = False
        try:
            from db import get_conn

            conn = get_conn()
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(DISTINCT t.id) AS n
                    FROM tracks t
                    JOIN playlist_tracks pt ON pt.track_id = t.id
                    JOIN playlists p ON p.id = pt.playlist_id
                    LEFT JOIN files f ON f.track_id = t.id
                    WHERE COALESCE(p.sync_enabled, 1) = 1
                      AND f.track_id IS NULL
                      AND COALESCE(t.unavailable, 0) = 1
                    """
                ).fetchone()
                n = (
                    int(row["n"])
                    if row
                    and hasattr(row, "keys")
                    and "n" in row.keys()
                    and row["n"] is not None
                    else 0
                )
                has_missing = n > 0
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            has_missing = False

        self._set_missing_icon(has_missing=has_missing)

    def _refresh_notifications_list(self) -> None:
        while self.notifications_list_lay.count():
            it = self.notifications_list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        if not self._notifications:
            empty = QLabel("No notifications yet.")
            empty.setStyleSheet("color: #a9b0bb;")
            self.notifications_list_lay.addWidget(empty)
            self.notifications_list_lay.addStretch(1)
            return

        for n in self._notifications[:10]:
            nid = str(n.get("id") or "")
            self.notifications_list_lay.addWidget(
                _NotificationRow(
                    n["text"],
                    kind=n.get("kind") or "info",
                    on_close=(lambda _nid=nid: self._dismiss_notification(_nid)),
                )
            )

        self.notifications_list_lay.addStretch(1)

    def _refresh_settings(self) -> None:
        self._refresh_storage_stats()
        try:
            self._refresh_db_stats()
        except Exception:
            pass
        self._refresh_account_stats()
        try:
            self._refresh_debug_toggle_ui()
        except Exception:
            pass

    def _browse_db_file(self) -> None:
        try:
            current = (os.getenv("TUNESYNC_DB_PATH") or "").strip()
            start_dir = ""
            try:
                from pathlib import Path

                if current:
                    start_dir = str(Path(current).expanduser().resolve().parent)
            except Exception:
                start_dir = ""

            chosen, _filter = QFileDialog.getOpenFileName(
                self,
                "Select TuneSync database",
                start_dir,
                "SQLite DB (*.sqlite3 *.sqlite *.db);;All files (*)",
            )
            if not chosen:
                return

            msg = (
                "Use this database file?\n\n"
                "TuneSync will read/write playlists and track state from this file. "
                "If you select your existing DB, your playlists should appear immediately.\n\n"
                f"Selected: {chosen}"
            )
            btn = QMessageBox.question(self, "Set Database File", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return

            os.environ["TUNESYNC_DB_PATH"] = chosen
            try:
                self._settings.setValue("db_path", chosen)
            except Exception:
                pass

            # Persist to a local .env file for the CLI pipeline.
            try:
                from pathlib import Path

                env_path = Path(".env")
                lines = env_path.read_text().splitlines() if env_path.exists() else []
                out: list[str] = []
                wrote = False
                for line in lines:
                    if line.strip().startswith("TUNESYNC_DB_PATH="):
                        out.append(f"TUNESYNC_DB_PATH={chosen}")
                        wrote = True
                    else:
                        out.append(line)
                if not wrote:
                    out.append(f"TUNESYNC_DB_PATH={chosen}")
                env_path.write_text("\n".join(out).rstrip() + "\n")
            except Exception:
                pass

            # Ensure schema exists and reload UI from the chosen DB.
            try:
                from db import init_db

                init_db()
            except Exception:
                pass
            try:
                self._load_downloaded_details()
            except Exception:
                pass
            try:
                self._load_cards_from_db()
            except Exception:
                pass
        except Exception:
            return
        self._refresh_db_stats()

    def _refresh_db_stats(self) -> None:
        try:
            from db import get_db_path

            full = str(get_db_path())
        except Exception:
            full = (os.getenv("TUNESYNC_DB_PATH") or "").strip() or "(default)"

        try:
            self.db_path.setToolTip(full)
        except Exception:
            pass
        try:
            fm = self.db_path.fontMetrics()
            self.db_path.setText(fm.elidedText(full, Qt.ElideMiddle, 420))
        except Exception:
            self.db_path.setText(full)

    def _browse_storage_folder(self) -> None:
        try:
            from PySide6.QtWidgets import QFileDialog

            current = (os.getenv("DOWNLOAD_ROOT") or "").strip()
            start_dir = current if current else ""

            chosen = QFileDialog.getExistingDirectory(self, "Select local storage folder", start_dir)

            if not chosen:
                return

            msg = (
                "Use this folder as your Download Root?\n\n"
                "TuneSync will NOT modify or overwrite anything in this folder. "
                "It will only use it as the location for downloads/rescans.\n\n"
                f"Selected: {chosen}"
            )
            btn = QMessageBox.question(self, "Set Download Root", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return

            os.environ["DOWNLOAD_ROOT"] = chosen
            try:
                self._settings.setValue("download_root", chosen)
            except Exception:
                pass

            # Tutorial: choosing the folder completes the Download Root step.
            try:
                if getattr(self, "_tutorial_active", False) and int(getattr(self, "_tutorial_step", 0) or 0) == 1:
                    self._tutorial_step = 2
                    self._tutorial_refresh()
            except Exception:
                pass
            # Persist to a local .env file for the CLI pipeline.
            from pathlib import Path

            env_path = Path(".env")
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            out: list[str] = []
            wrote = False
            for line in lines:
                if line.strip().startswith("DOWNLOAD_ROOT="):
                    out.append(f"DOWNLOAD_ROOT={chosen}")
                    wrote = True
                else:
                    out.append(line)
            if not wrote:
                out.append(f"DOWNLOAD_ROOT={chosen}")
            env_path.write_text("\n".join(out).rstrip() + "\n")

            # Safety: normalize any absolute paths stored in DB to be relative to the chosen root.
            # This updates DB only; it does not touch files on disk.
            try:
                from pathlib import Path

                from db import get_conn, migrate_files_to_relative

                conn = get_conn()
                updated = migrate_files_to_relative(conn, Path(chosen))
                if updated > 0:
                    self._push_notification(f"Storage updated: normalized {updated} file paths", kind="info")
            except Exception:
                pass
        except Exception:
            return
        self._refresh_storage_stats()
        if self._tutorial_active:
            self._tutorial_refresh()

    def _refresh_storage_stats(self) -> None:
        from pathlib import Path

        root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
        full = root or "(DOWNLOAD_ROOT not set)"
        try:
            self.storage_path.setToolTip(full)
        except Exception:
            pass
        try:
            fm = self.storage_path.fontMetrics()
            self.storage_path.setText(fm.elidedText(full, Qt.ElideMiddle, 420))
        except Exception:
            self.storage_path.setText(full)

        if not root:
            self.total_size_val.setText("—")
            # storage bar removed
            return

        download_root = Path(root)
        referenced_size = 0
        orphan_size = 0

        try:
            from db import get_conn

            conn = get_conn()
            rows = conn.execute(
                """
                SELECT
                  f.track_id,
                  f.file_path,
                  CASE WHEN EXISTS(
                    SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = f.track_id
                  ) THEN 1 ELSE 0 END AS referenced
                FROM files f
                """
            ).fetchall()
        except Exception:
            rows = []

        for r in rows:
            try:
                p = Path(r["file_path"])
                if not p.is_absolute():
                    p = download_root / p
                if not p.exists():
                    continue
                sz = p.stat().st_size
                if int(r["referenced"]) == 1:
                    referenced_size += sz
                else:
                    orphan_size += sz
            except Exception:
                continue

        total = referenced_size + orphan_size
        # storage bar removed

        def fmt(n: int) -> str:
            gb = n / (1024 * 1024 * 1024)
            if gb >= 1:
                return f"{gb:.1f}GB"
            mb = n / (1024 * 1024)
            return f"{mb:.0f}MB"

        self.total_size_val.setText(fmt(total) if total > 0 else "0MB")

    def _refresh_account_stats(self) -> None:
        try:
            from db import get_conn

            conn = get_conn()
            pl = conn.execute("SELECT COUNT(*) AS n FROM playlists").fetchone()["n"]
            songs = conn.execute("SELECT COUNT(DISTINCT track_id) AS n FROM playlist_tracks").fetchone()["n"]
            self.spotify_counts.setText(f"{pl} synced playlists      {songs} synced songs")
        except Exception:
            self.spotify_counts.setText("—")

    def _update_spotify_auth_ui(self, *, force_authed: bool | None = None) -> None:
        authed = False
        if force_authed is None:
            try:
                from spotify_client import SpotifyClient

                authed = SpotifyClient.is_authenticated()
            except Exception:
                authed = False
        else:
            authed = bool(force_authed)

        try:
            self.spotify_login.setEnabled(not authed)
            self.spotify_logout.setEnabled(authed)
            self.spotify_login.setText("Signed in" if authed else "Sign in")
        except Exception:
            pass

        try:
            if authed:
                name = str(self._settings.value("spotify_user_display_name", "") or "").strip()
                uid = str(self._settings.value("spotify_user_id", "") or "").strip()
                who = name or uid
                if not who:
                    # Token exists but we haven't cached identity yet (e.g. older installs).
                    try:
                        from spotify_client import SpotifyClient

                        me = SpotifyClient.get_current_user() or {}
                        uid = (me.get("id") or "").strip() if isinstance(me, dict) else ""
                        name = (me.get("display_name") or uid or "").strip() if isinstance(me, dict) else ""
                        if uid:
                            self._settings.setValue("spotify_user_id", uid)
                        if name:
                            self._settings.setValue("spotify_user_display_name", name)
                        who = name or uid
                    except Exception:
                        pass
                self.spotify_user.setText(f"Signed in as {who}" if who else "Signed in")
            else:
                self.spotify_user.setText("Not signed in")
        except Exception:
            pass

    def _spotify_sign_in(self) -> None:
        # Request playlist scopes plus profile-read so we can show account identity.
        scope = "playlist-read-private playlist-read-collaborative user-read-private"

        try:
            self.spotify_login.setEnabled(False)
            self.spotify_login.setText("Signing in…")
        except Exception:
            pass

        thread = QThread(self)
        worker = _SpotifyAuthWorker(scope=scope)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _done(who: str):
            try:
                self._push_notification(f"Spotify connected: {who}", kind="success")
            except Exception:
                pass
            try:
                try:
                    from spotify_client import SpotifyClient

                    me = SpotifyClient.get_current_user() or {}
                    uid = (me.get("id") or "").strip() if isinstance(me, dict) else ""
                    name = (me.get("display_name") or uid or "").strip() if isinstance(me, dict) else ""
                    if uid:
                        self._settings.setValue("spotify_user_id", uid)
                    if name:
                        self._settings.setValue("spotify_user_display_name", name)
                except Exception:
                    pass
                self._update_spotify_auth_ui()
            finally:
                thread.quit()

        def _failed(msg: str):
            try:
                self._push_notification(f"Spotify sign-in failed: {msg}", kind="error")
            except Exception:
                pass
            try:
                self.spotify_login.setEnabled(True)
                self.spotify_login.setText("Sign in")
            except Exception:
                pass
            thread.quit()

        worker.done.connect(_done)
        worker.failed.connect(_failed)
        worker.done.connect(lambda _x: thread.quit())
        worker.failed.connect(lambda _x: thread.quit())

        def _cleanup():
            try:
                worker.deleteLater()
            except Exception:
                pass
            try:
                thread.deleteLater()
            except Exception:
                pass

        thread.finished.connect(_cleanup)
        thread.start()

    def _spotify_log_out(self) -> None:
        user_id = ""
        try:
            user_id = str(self._settings.value("spotify_user_id", "") or "").strip()
        except Exception:
            user_id = ""

        try:
            from spotify_client import SpotifyClient

            SpotifyClient.logout()
        except Exception:
            pass

        # Freeze known-private playlists owned by the signed-out user.
        if user_id:
            try:
                from db import get_conn

                conn = get_conn()
                try:
                    conn.execute(
                        "UPDATE playlists SET sync_enabled = 0 "
                        "WHERE spotify_owner_id = ? AND spotify_is_public = 0",
                        (user_id,),
                    )
                    conn.commit()
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            self._settings.setValue("spotify_user_id", "")
            self._settings.setValue("spotify_user_display_name", "")
        except Exception:
            pass

        try:
            self._push_notification("Spotify logged out", kind="info")
        except Exception:
            pass
        self._update_spotify_auth_ui(force_authed=False)
        self._load_cards_from_db()

    def _show_rekordbox_guide(self) -> None:
        if self._rekordbox_carousel is not None:
            self._rekordbox_carousel.show_centered()
        # Tutorial: opening the guide is the final action.
        try:
            if getattr(self, "_tutorial_active", False) and int(getattr(self, "_tutorial_step", 0) or 0) == 8:
                self._tutorial_hide_forever()
        except Exception:
            pass

    def _show_add_page(self) -> None:
        self.page_title.setText("Add Playlists")
        self.stack.setCurrentWidget(self.add_page)
        self.add_page.refresh()
        if self.notifications_panel.isVisible():
            self.notifications_panel.setVisible(False)

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _remove_grid_items_only(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)

    def _load_cards_from_db(self) -> None:
        try:
            from db import get_conn

            conn = get_conn()
            try:
                playlists = conn.execute(
                    """
                    SELECT
                      p.id,
                      p.name,
                      p.creator,
                      COALESCE(p.sync_enabled, 1) AS sync_enabled,
                      p.image_url,
                      p.updated_at,
                      p.created_at,
                      p.spotify_created_at,
                      COUNT(pt.track_id) AS track_count,
                      COALESCE(SUM(CASE
                        WHEN pt.track_id IS NOT NULL AND f.track_id IS NULL AND COALESCE(t.unavailable, 0) = 0 THEN 1
                        ELSE 0
                      END), 0) AS missing_count
                    FROM playlists p
                    LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
                    LEFT JOIN tracks t ON t.id = pt.track_id
                    LEFT JOIN files f ON f.track_id = pt.track_id
                    GROUP BY p.id
                    ORDER BY p.name
                    """
                ).fetchall()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            # Avoid blanking the grid on transient DB errors (e.g., locked).
            # Keep current UI and try again shortly.
            try:
                if not getattr(self, "_db_load_retry_scheduled", False):
                    self._db_load_retry_scheduled = True

                    def _retry():
                        try:
                            self._db_load_retry_scheduled = False
                        except Exception:
                            pass
                        self._load_cards_from_db()

                    QTimer.singleShot(750, _retry)
            except Exception:
                pass
            return

        enabled_ids: set[str] = set()

        computed: list[dict] = []
        for p in playlists:
            pid = p["id"]
            name = p["name"]
            creator = p["creator"] if "creator" in p.keys() else None
            image_url = p["image_url"] if "image_url" in p.keys() else None

            sync_enabled = 1
            try:
                if "sync_enabled" in p.keys() and p["sync_enabled"] is not None:
                    sync_enabled = int(p["sync_enabled"])
            except Exception:
                sync_enabled = 1
            if sync_enabled == 1:
                enabled_ids.add(pid)

            updated_at = p["updated_at"] if "updated_at" in p.keys() else None
            spotify_created_at = p["spotify_created_at"] if "spotify_created_at" in p.keys() else None
            created_at = spotify_created_at
            track_count = int(p["track_count"]) if "track_count" in p.keys() and p["track_count"] is not None else 0

            # Disabled playlists are frozen/stopped.
            if sync_enabled == 0:
                status = "Frozen"
            else:
                status = "Synced"
            try:
                missing = int(p["missing_count"]) if "missing_count" in p.keys() and p["missing_count"] is not None else 0
                if status != "Frozen":
                    if track_count == 0:
                        status = "Needs sync"
                    elif missing > 0:
                        status = "Needs sync"
            except Exception:
                pass

            computed.append(
                {
                    "id": pid,
                    "name": name,
                    "creator": creator,
                    "image_url": image_url,
                    "status": status,
                    "sync_enabled": sync_enabled,
                    "updated_at": updated_at,
                    "created_at": created_at,
                    "track_count": track_count,
                }
            )

        self._all_playlists = computed
        self._synced_ids = set(enabled_ids)
        self._apply_filters()
        self._refresh_missing_icon()

    def _apply_filters(self) -> None:
        items = list(self._all_playlists)

        filter_choice = getattr(self, "_filter_choice", None)
        if filter_choice:
            want = (filter_choice or "").strip().lower()
            items = [p for p in items if (p.get("status") or "").strip().lower() == want]

        q = (self.search.text() or "").strip().lower()
        if q:
            items = [
                p
                for p in items
                if (q in (p.get("name") or "").lower()) or (q in (p.get("creator") or "").lower())
            ]

        sort_choice = getattr(self, "_sort_choice", None) or ""
        if sort_choice == "Latest changes":
            items.sort(key=lambda p: (p.get("updated_at") or ""), reverse=True)
        elif sort_choice == "Playlist creation date":
            items.sort(key=lambda p: (p.get("created_at") or ""), reverse=True)
        elif sort_choice == "Sync status":
            # Group by computed status (actionable first), then name.
            rank = {
                "Needs sync": 0,
                "Synced": 1,
                "Syncing": 2,
                "Errors": 3,
                "Frozen": 4,
            }
            items.sort(key=lambda p: (rank.get(p.get("status") or "", 99), (p.get("name") or "").lower()))
        elif sort_choice == "Playlist size":
            items.sort(key=lambda p: int(p.get("track_count") or 0), reverse=True)
        elif sort_choice == "Creator":
            items.sort(key=lambda p: ((p.get("creator") or "").lower(), (p.get("name") or "").lower()))

        # Temporary: newly-added playlists appear at the top until the user
        # changes sort (or navigates away).
        if not sort_choice:
            try:
                promoted = [pid for pid in (self._promoted_playlist_ids or []) if isinstance(pid, str) and pid]
            except Exception:
                promoted = []
            if promoted:
                want = set(promoted)
                promoted_items = [p for p in items if p.get("id") in want]
                rest = [p for p in items if p.get("id") not in want]
                # Keep promoted in the exact order recorded.
                by_id = {p.get("id"): p for p in promoted_items if p.get("id")}
                ordered_promoted = [by_id[pid] for pid in promoted if pid in by_id]
                items = ordered_promoted + rest

        self._last_rendered_playlists = list(items)
        self._render_playlist_cards(items)

    def _ensure_library_cards(self, playlists: list[dict]) -> list[str]:
        want_ids = [p.get("id") for p in playlists if p.get("id")]
        ordered_ids = [pid for pid in want_ids if isinstance(pid, str) and pid]
        want_set = set(ordered_ids)

        # Drop cards that are no longer present.
        for pid in list(self._cards_by_pid.keys()):
            if pid not in want_set:
                w = self._cards_by_pid.pop(pid)
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass

        # Create/update cards.
        for p in playlists:
            pid = p.get("id")
            if not pid:
                continue
            name = p.get("name") or pid
            image_url = p.get("image_url")
            status = p.get("status") or "Synced"
            creator = p.get("creator")

            model = CardModel(title=name, status=status, creator=creator, image_url=image_url, playlist_id=pid)

            card = self._cards_by_pid.get(pid)
            if card is None:
                card = PlaylistCard(
                    model,
                    on_toggle_selected=(lambda selected, shift=False, pid=pid: self._on_playlist_selected(pid, selected, shift)),
                )
                self._cards_by_pid[pid] = card
            else:
                card.update_model(model)

            card.set_selection_enabled(True)
            card.set_selected(pid in self._selected_playlist_ids)

            if image_url:
                if image_url in self._pix_cache:
                    card.set_cover_pixmap(self._pix_cache[image_url])
                else:
                    self._fetch_cover(pid, image_url)

        return ordered_ids

    def _relayout_library_widgets(self, col_count: int) -> None:
        ordered_ids = [p.get("id") for p in (getattr(self, "_last_rendered_playlists", []) or []) if p.get("id")]
        ordered_ids = [pid for pid in ordered_ids if isinstance(pid, str) and pid]

        self._remove_grid_items_only()
        self.grid.addWidget(self._add_tile, 0, 0)

        r = 0
        c = 1
        for pid in ordered_ids:
            card = self._cards_by_pid.get(pid)
            if card is None:
                continue
            self.grid.addWidget(card, r, c)
            c += 1
            if c >= col_count:
                r += 1
                c = 0

    def _render_playlist_cards(self, playlists: list[dict]) -> None:
        ordered_ids = self._ensure_library_cards(playlists)
        # Keep the order source-of-truth for relayout-only operations.
        by_id = {p.get("id"): p for p in playlists if p.get("id")}
        self._last_rendered_playlists = [by_id[pid] for pid in ordered_ids if pid in by_id]

        col_count = self._compute_library_columns()
        self._set_library_content_width(col_count)
        self._relayout_library_widgets(col_count)

    def _open_add_playlists(self) -> None:
        self._show_add_page()
        # Tutorial: clicking Add should advance immediately.
        try:
            if getattr(self, "_tutorial_active", False) and int(getattr(self, "_tutorial_step", 0) or 0) == 3:
                self._tutorial_step = 4
                self._tutorial_refresh()
        except Exception:
            pass

    def _on_playlist_selected(self, pid: str, selected: bool, shift: bool = False) -> None:
        # Shift-click selects a contiguous range from the anchor to pid.
        if shift:
            ordered_ids = [
                p.get("id")
                for p in (getattr(self, "_last_rendered_playlists", []) or [])
                if p.get("id")
            ]
            ordered_ids = [x for x in ordered_ids if isinstance(x, str) and x]

            # If the clicked card is currently selected, shift-click deselects the range.
            deselect_mode = bool(selected)

            anchor = getattr(self, "_selection_anchor_pid", None)
            if not anchor:
                # Fall back to any existing selection (or current click).
                anchor = next(iter(self._selected_playlist_ids), pid)
                self._selection_anchor_pid = anchor

            if anchor in ordered_ids and pid in ordered_ids:
                a = ordered_ids.index(anchor)
                b = ordered_ids.index(pid)
                lo, hi = (a, b) if a <= b else (b, a)
                for x in ordered_ids[lo : hi + 1]:
                    card = self._cards_by_pid.get(x)
                    if deselect_mode:
                        self._selected_playlist_ids.discard(x)
                        if card is not None:
                            card.set_selected(False)
                    else:
                        self._selected_playlist_ids.add(x)
                        if card is not None:
                            card.set_selected(True)
            else:
                # Fallback behavior: just select this one.
                if deselect_mode:
                    self._selected_playlist_ids.discard(pid)
                else:
                    self._selected_playlist_ids.add(pid)
                card = self._cards_by_pid.get(pid)
                if card is not None:
                    card.set_selected(not deselect_mode)

            self._update_remove_button()
            return

        if selected:
            self._selected_playlist_ids.add(pid)
        else:
            self._selected_playlist_ids.discard(pid)
        # Update the range anchor on normal clicks.
        self._selection_anchor_pid = pid
        self._update_remove_button()

    def _update_remove_button(self) -> None:
        n = len(self._selected_playlist_ids)
        self.remove_btn.setVisible(n > 0)
        self.remove_btn.setEnabled(n > 0)
        self.remove_btn.setText(f"Remove ({n})" if n else "Remove")

        self.toggle_sync_btn.setVisible(n > 0)

        if n == 0:
            self._toggle_sync_mode = None
            self.toggle_sync_btn.setEnabled(False)
            self.toggle_sync_btn.setText("Toggle Sync")
            return

        selected = sorted(self._selected_playlist_ids)
        states = {pid in getattr(self, "_synced_ids", set()) for pid in selected}
        if len(states) != 1:
            # Mixed selection => ambiguous action.
            self._toggle_sync_mode = None
            self.toggle_sync_btn.setEnabled(False)
            self.toggle_sync_btn.setText("Toggle Sync")
            return

        enabled = True in states
        if enabled:
            self._toggle_sync_mode = "disable"
            self.toggle_sync_btn.setEnabled(True)
            self.toggle_sync_btn.setText("Disable Sync")
        else:
            self._toggle_sync_mode = "enable"
            self.toggle_sync_btn.setEnabled(True)
            self.toggle_sync_btn.setText("Enable Sync")

    def _toggle_sync_selected_playlists(self) -> None:
        ids = sorted(self._selected_playlist_ids)
        if not ids:
            return

        mode = getattr(self, "_toggle_sync_mode", None)
        if mode not in {"enable", "disable"}:
            return

        if mode == "disable":
            msg = (
                f"Disable syncing for {len(ids)} playlist(s)?\n\n"
                "This keeps your downloaded songs and the database entries, but future syncs will not update them."
            )
            btn = QMessageBox.question(self, "Toggle Sync", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return
            try:
                from db import get_conn

                conn = get_conn()
                q = ",".join("?" for _ in ids)
                conn.execute(f"UPDATE playlists SET sync_enabled = 0 WHERE id IN ({q})", tuple(ids))
                conn.commit()
            except Exception:
                pass
            note = f"Sync disabled for {len(ids)} playlist(s)"
        else:
            # If signed out, block enabling sync for known-private playlists.
            try:
                from spotify_client import SpotifyClient

                if not SpotifyClient.is_authenticated():
                    from db import get_conn

                    conn = get_conn()
                    try:
                        q = ",".join("?" for _ in ids)
                        rows = conn.execute(
                            f"SELECT id, name FROM playlists WHERE id IN ({q}) AND spotify_is_public = 0",
                            tuple(ids),
                        ).fetchall()
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass

                    if rows:
                        names = [str(r["name"] or r["id"]) for r in rows if r]
                        shown = "\n".join(f"• {n}" for n in names[:6])
                        if len(names) > 6:
                            shown += f"\n• (+{len(names) - 6} more)"
                        QMessageBox.information(
                            self,
                            "Sign in required",
                            "These playlist(s) appear to be private.\n\n"
                            "Sign in to Spotify to unfreeze them, or make them public on Spotify.\n\n"
                            + shown,
                        )
                        return
            except Exception:
                pass

            msg = (
                f"Enable syncing for {len(ids)} playlist(s)?\n\n"
                "Future syncs will update them."
            )
            btn = QMessageBox.question(self, "Enable Sync", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                return
            try:
                from db import get_conn

                conn = get_conn()
                q = ",".join("?" for _ in ids)
                conn.execute(f"UPDATE playlists SET sync_enabled = 1 WHERE id IN ({q})", tuple(ids))
                conn.commit()
            except Exception:
                pass
            note = f"Sync enabled for {len(ids)} playlist(s)"

        self._selected_playlist_ids.clear()
        self._selection_anchor_pid = None
        self._toggle_sync_mode = None
        self._update_remove_button()
        self._load_cards_from_db()
        self._push_notification(note, kind="info")

    def _remove_selected_playlists(self) -> None:
        from pathlib import Path

        ids = sorted(self._selected_playlist_ids)
        if not ids:
            return

        # Confirm intent
        msg = (
            f"Remove {len(ids)} playlist(s) from your synced library?\n\n"
            "This will also delete downloaded songs that become unreferenced by any playlist."
        )
        btn = QMessageBox.question(self, "Remove playlists", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return

        # Best-effort: determine download root for deleting audio files.
        download_root: Path | None = None
        try:
            root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
            if root:
                p = Path(root)
                if p.exists():
                    download_root = p
        except Exception:
            download_root = None

        # Remove from DB and delete newly-orphaned tracks/files
        deleted_files = 0
        try:
            from db import get_conn, get_file_path_for_track

            conn = get_conn()

            qmarks = ",".join("?" for _ in ids)
            affected = conn.execute(
                f"SELECT DISTINCT track_id FROM playlist_tracks WHERE playlist_id IN ({qmarks})",
                tuple(ids),
            ).fetchall()
            affected_track_ids = [r["track_id"] for r in affected]

            for pid in ids:
                conn.execute("DELETE FROM playlists WHERE id = ?", (pid,))

            # Only delete tracks that were in removed playlists AND are now unreferenced.
            to_delete: list[str] = []
            for tid in affected_track_ids:
                row = conn.execute(
                    "SELECT 1 FROM playlist_tracks WHERE track_id = ? LIMIT 1",
                    (tid,),
                ).fetchone()
                if row is None:
                    to_delete.append(tid)

            if to_delete and download_root is not None:
                for tid in to_delete:
                    try:
                        p = get_file_path_for_track(conn, tid, download_root)
                        if p is not None and p.exists():
                            p.unlink()
                            deleted_files += 1
                    except Exception:
                        pass

            if to_delete:
                qmarks2 = ",".join("?" for _ in to_delete)
                conn.execute(f"DELETE FROM files WHERE track_id IN ({qmarks2})", tuple(to_delete))
                conn.execute(f"DELETE FROM tracks WHERE id IN ({qmarks2})", tuple(to_delete))

            conn.commit()
        except Exception:
            pass

        self._selected_playlist_ids.clear()
        self._selection_anchor_pid = None
        self._update_remove_button()
        self._load_cards_from_db()

        if download_root is None:
            QMessageBox.information(
                self,
                "Removed",
                "Playlists removed. DOWNLOAD_ROOT is not set/valid, so audio files were not deleted.",
            )
        else:
            QMessageBox.information(self, "Removed", f"Playlists removed. Deleted {deleted_files} file(s).")

    def _on_playlists_added(self, added_ids: list[str] | None = None) -> None:
        # Pin newly-added playlists to the top until the user changes sort
        # (or navigates away to another page).
        try:
            ids = [x for x in (added_ids or []) if isinstance(x, str) and x]
            # Most recent first.
            self._promoted_playlist_ids = list(reversed(ids))
        except Exception:
            pass
        self._load_cards_from_db()
        self._show_library_page()
        # Tutorial: once playlists are added, resume on the Home flow.
        try:
            if getattr(self, "_tutorial_active", False) and int(getattr(self, "_tutorial_step", 0) or 0) == 4:
                self._tutorial_tip_suppressed = False
                self._tutorial_step = 5
                self._tutorial_refresh()
        except Exception:
            pass

    def _fetch_cover(self, playlist_id: str, url: str) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"TuneSync")
        req.setRawHeader(b"Accept", b"image/*")
        try:
            req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        except Exception:
            pass
        rep = self._net.get(req)

        def finished():
            rep.deleteLater()
            status = rep.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if rep.error() != QNetworkReply.NoError:
                if _DEBUG_IMAGES:
                    print(f"[img] FAIL playlist={playlist_id} status={status} err={rep.error()} url={url} msg={rep.errorString()}")
                return
            data = bytes(rep.readAll())
            pm = QPixmap()
            if not pm.loadFromData(data):
                if _DEBUG_IMAGES:
                    ct = bytes(rep.rawHeader(b"Content-Type")).decode("utf-8", errors="ignore") if rep.hasRawHeader(b"Content-Type") else ""
                    print(f"[img] DECODE_FAIL playlist={playlist_id} status={status} ct={ct} bytes={len(data)} url={url}")
                return
            card = self._cards_by_pid.get(playlist_id)
            if card is not None:
                card.set_cover_pixmap(pm)

            # Cache by URL so filtering doesn't refetch.
            self._pix_cache[url] = pm

            if _DEBUG_IMAGES:
                print(f"[img] OK playlist={playlist_id} status={status} bytes={len(data)}")

        rep.finished.connect(finished)

    def _start_sync(self) -> None:
        # Spawn a subprocess that runs the sync pipeline and streams progress to stdout.
        # In dev: `python -u -m tunesync_app --sync-worker`
        # In packaged app: `<TuneSync> --sync-worker`
        if self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning:
            return

        self._sync_cancel_requested = False
        try:
            self._sync_started_epoch = time.time()
        except Exception:
            self._sync_started_epoch = None

        if self._tutorial_active:
            # If user starts syncing earlier than expected, jump them forward.
            try:
                s = int(getattr(self, "_tutorial_step", 0) or 0)
            except Exception:
                s = 0

            if s <= 4:
                self._tutorial_step = 5
                QTimer.singleShot(150, self._tutorial_refresh)
            elif s == 5:
                # They clicked SYNC NOW as instructed; move on automatically.
                self._tutorial_step = 6
                QTimer.singleShot(150, self._tutorial_refresh)

        self._sync_hover_cancel = False
        self._refresh_sync_button_state()

        self._reset_run_stats()
        self._sync_items = {}
        self._sync_items_order = []
        self._sync_attempt = None
        self._sync_attempt_max = None
        self.run_status.setText("Syncing…")
        if not self._debug_mode_enabled():
            self._set_phase("Fetching info from Spotify…")
        self._set_progress_indeterminate(True)
        self._update_run_detail()

        # No popup for progress; Details pane shows live status.

        # Mark the start time in SQLite's CURRENT_TIMESTAMP format (UTC)
        try:
            from datetime import datetime, timezone

            self._run_started_at_sql = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            self._run_started_at_sql = None

        proc = QProcess(self)
        proc.setWorkingDirectory(str(QUrl.fromLocalFile(".").toLocalFile()))
        # Ensure we run from repo root by resolving relative to this file.
        # QProcess working dir is set again below using a best-effort absolute path.
        try:
            import os
            from pathlib import Path

            repo_root = Path(__file__).resolve().parents[2]
            proc.setWorkingDirectory(str(repo_root))
        except Exception:
            pass

        import sys

        # Enable structured @TS events from the backend so the UI can show
        # per-track progress and retry behavior as one seamless sync.
        try:
            env = [f"{k}={v}" for k, v in os.environ.items()]
            env.append("TUNESYNC_UI=1")
            if self._debug_mode_enabled():
                env.append("TUNESYNC_DEBUG=1")
            proc.setEnvironment(env)
        except Exception:
            pass

        is_frozen = bool(getattr(sys, "frozen", False))
        proc.setProgram(sys.executable)
        if is_frozen:
            proc.setArguments(["--sync-worker"])
        else:
            proc.setArguments(["-u", "-m", "tunesync_app", "--sync-worker"])
        proc.setProcessChannelMode(QProcess.MergedChannels)

        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))

        def finished(_code, _status):
            self._sync_proc = None
            cancelled = bool(getattr(self, "_sync_cancel_requested", False))
            self._sync_hover_cancel = False
            self._refresh_sync_button_state()

            if self._downloads_timer.isActive():
                self._downloads_timer.stop()

            self._set_progress_indeterminate(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

            # Ensure the detail line doesn't get stuck on an in-progress phase.
            if not self._debug_mode_enabled():
                self._phase_text = "Finished"

            self._update_run_detail(final=True)
            self._load_downloaded_details()
            self._load_cards_from_db()

            if cancelled:
                try:
                    self.run_status.setText("Cancelled")
                except Exception:
                    pass
                try:
                    self._rollback_sync_run()
                except Exception:
                    pass
                return

            if int(_code or 0) != 0:
                try:
                    self.run_status.setText("Sync failed")
                except Exception:
                    pass
                tail = (self._last_line or "").strip()
                self._push_notification(f"Download failed" + (f": {tail}" if tail else ""), kind="error")
            else:
                try:
                    self.run_status.setText("Synced successfully")
                except Exception:
                    pass
                if self._downloaded_ok is not None and int(self._downloaded_ok) > 0:
                    self._push_notification(f"Success: {int(self._downloaded_ok)} songs downloaded", kind="success")
                else:
                    self._push_notification("Sync finished", kind="success")

        proc.finished.connect(finished)

        def on_error(_err):
            tail = (self._last_line or "").strip()
            if getattr(self, "_sync_cancel_requested", False):
                return
            self._push_notification(f"Download failed" + (f": {tail}" if tail else ""), kind="error")

        try:
            proc.errorOccurred.connect(on_error)
        except Exception:
            pass
        self._sync_proc = proc
        if not self._downloads_timer.isActive():
            self._downloads_timer.start()
        proc.start()


    def _toggle_tutorial(self) -> None:
        if getattr(self, "_tutorial_active", False) and self._tip.isVisible():
            self._tutorial_close()
            return

        if getattr(self, "_tutorial_completed", False):
            # Allow help to re-run tips even if previously hidden/completed.
            self._tutorial_completed = False
            try:
                self._settings.setValue("tutorial_completed", False)
            except Exception:
                pass

        self._tutorial_active = True
        self._tutorial_tip_suppressed = False
        self._tutorial_step4_intro_shown = False

        # Start the tutorial at the first *necessary* step.
        # - If Download Root is already set and Spotify is already connected, jump straight to Add.
        # - If Download Root is set but Spotify isn't, start at Spotify.
        # - Otherwise start at the welcome step that guides the user into Settings.
        try:
            if self._tutorial_has_download_root():
                if self._tutorial_has_spotify():
                    self._tutorial_step = 3
                    self._show_library_page()
                    return
                self._tutorial_step = 2
                self._show_settings_page()
                return
        except Exception:
            pass

        self._tutorial_step = 0
        self._tutorial_refresh()

    def _tutorial_has_download_root(self) -> bool:
        try:
            return bool((os.getenv("DOWNLOAD_ROOT") or "").strip())
        except Exception:
            return False

    def _tutorial_has_spotify(self) -> bool:
        try:
            from spotify_client import SpotifyClient

            if SpotifyClient.has_cached_token():
                return True
        except Exception:
            pass
        try:
            uid = str(self._settings.value("spotify_user_id", "") or "").strip()
            return bool(uid)
        except Exception:
            return False

    def _tutorial_close(self) -> None:
        self._tutorial_active = False
        self._tutorial_tip_suppressed = False
        self._tutorial_step4_intro_shown = False
        try:
            self._tip.setVisible(False)
        except Exception:
            pass
        try:
            self._spotlight.setVisible(False)
            self._spotlight.set_target_widget(None)
        except Exception:
            pass

    def _tutorial_soft_hide(self) -> None:
        # Hide the current tip/spotlight but keep tutorial state active.
        try:
            self._tip.setVisible(False)
        except Exception:
            pass
        try:
            self._spotlight.setVisible(False)
            self._spotlight.set_target_widget(None)
        except Exception:
            pass

    def _tutorial_hide_forever(self) -> None:
        self._tutorial_completed = True
        try:
            self._settings.setValue("tutorial_completed", True)
        except Exception:
            pass
        self._tutorial_close()

    def _tutorial_next(self) -> None:
        if not getattr(self, "_tutorial_active", False):
            return
        s = int(getattr(self, "_tutorial_step", 0) or 0)

        # Skip steps that are already satisfied.
        # (We intentionally do NOT skip the playlist-add flow yet for testing.)
        try:
            if s == 1 and self._tutorial_has_download_root():
                self._tutorial_step = 2
                QTimer.singleShot(0, self._tutorial_refresh)
                return
            if s == 2 and self._tutorial_has_spotify():
                self._tutorial_step = 3
                QTimer.singleShot(0, self._show_library_page)
                return
        except Exception:
            pass
        # Step transitions also drive navigation so tips always make sense.
        if s == 0:
            self._tutorial_step = 1
            self._show_settings_page()
            return
        if s == 1:
            root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
            if not root:
                # Stay on this step until user actually chooses a folder.
                self._tutorial_refresh()
                return
            self._tutorial_step = 2
            self._tutorial_refresh()
            return
        if s == 2:
            self._tutorial_step = 3
            self._show_library_page()
            return
        if s == 3:
            self._tutorial_step = 4
            self._open_add_playlists()
            return
        if s == 4:
            # Step 4 is special: the "Got it" button should just dismiss the tip
            # while the user selects playlists. We advance when playlists are added.
            try:
                if getattr(self, "_tutorial_step4_intro_shown", False) and not getattr(self, "_tutorial_tip_suppressed", False):
                    self._tutorial_tip_suppressed = True
                    self._tutorial_soft_hide()
                    return
            except Exception:
                pass
            self._tutorial_refresh()
            return
        if s == 5:
            running = False
            try:
                running = self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning
            except Exception:
                running = False
            if not running:
                # User hasn't started sync yet; keep them here.
                self._tutorial_refresh()
                return
            self._tutorial_step = 6
            self._tutorial_refresh()
            return
        if s == 6:
            # Prefer user clicking Details; fallback continues to "Back to Settings".
            self._tutorial_step = 7
            self._tutorial_refresh()
            return
        if s == 7:
            self._tutorial_step = 8
            self._show_settings_page()
            return
        # Final
        self._tutorial_hide_forever()

    def _tutorial_refresh(self) -> None:
        if not getattr(self, "_tutorial_active", False):
            return

        # Don’t show if user hid them.
        if getattr(self, "_tutorial_completed", False):
            return

        s = int(getattr(self, "_tutorial_step", 0) or 0)

        # Only step 4 uses suppression; reset elsewhere.
        if s != 4:
            self._tutorial_tip_suppressed = False

        # Helper to show the popover anchored to a widget.
        def show_for(
            w: QWidget | None,
            *,
            title: str,
            body: str,
            next_text: str = "Next",
            show_next: bool = True,
            next_enabled_override: bool | None = None,
            prefer_below: bool = True,
            arrow_gap: int = 10,
        ) -> None:
            if w is None or not w.isVisible():
                return
            # Show spotlight overlay and aim it at the target.
            try:
                self._spotlight.setVisible(True)
                self._spotlight.raise_()
                self._spotlight.set_target_widget(w)
            except Exception:
                pass

            # Enable/disable Next for gated steps.
            next_enabled = True
            if getattr(self, "_tutorial_step", 0) == 1:
                next_enabled = bool((os.getenv("DOWNLOAD_ROOT") or "").strip())
            if getattr(self, "_tutorial_step", 0) == 4:
                # Step 4 is instructional; we wait for Add to Library.
                next_enabled = False
            if getattr(self, "_tutorial_step", 0) == 5:
                try:
                    next_enabled = self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning
                except Exception:
                    next_enabled = False
            if getattr(self, "_tutorial_step", 0) == 6:
                # Keep this step until the user opens Details.
                next_enabled = False

            if next_enabled_override is not None:
                next_enabled = bool(next_enabled_override)

            self._tip.set_content(
                title=title,
                body=body,
                next_text=next_text,
                next_enabled=next_enabled,
                show_next=show_next,
            )
            self._tip.resize(self._tip.sizeHint())

            try:
                # If we prefer showing the tip ABOVE the target, anchor to the target's top edge
                # so the popover never overlaps the control.
                anchor_y = int(w.height()) if prefer_below else 0
                center = w.mapToGlobal(QPoint(int(w.width() / 2), anchor_y))
            except Exception:
                center = self.mapToGlobal(QPoint(int(self.width() / 2), 0))

            try:
                tl = self.mapToGlobal(QPoint(0, 0))
                wr = QRectF(tl.x(), tl.y(), float(self.width()), float(self.height()))
            except Exception:
                wr = None

            self._tip.set_anchor(
                anchor_global=center,
                prefer_below=prefer_below,
                window_rect_global=wr,
                arrow_gap=arrow_gap,
            )
            self._tip.raise_()
            self._tip.setVisible(True)

        # Step 0: Home -> Settings
        if s == 0:
            show_for(
                self.btn_settings,
                title="Welcome",
                body="Click Settings to choose where your downloads and Rekordbox XML will live.",
                show_next=False,
            )
            return

        # Step 1: Settings -> Download root
        if s == 1:
            if self.stack.currentWidget() is not self.settings_page:
                self._show_settings_page()
                return
            root = (os.getenv("DOWNLOAD_ROOT") or "").strip()
            note = ""
            if not root:
                note = "\n\nClick Browse and pick a folder."
            show_for(
                self.browse_btn,
                title="Set your Download Root",
                body="This is where songs download, and where the Rekordbox XML export will go." + note,
                show_next=False,
            )
            return

        # Step 2: Settings -> Spotify
        if s == 2:
            if self.stack.currentWidget() is not self.settings_page:
                self._show_settings_page()
                return
            show_for(
                getattr(self, "spotify_login", None),
                title="Spotify (optional)",
                body="If you want, click Sign in to load your playlists automatically.\n\nOtherwise, you can add a playlist by URL later.",
                next_text="Back to Home",
                show_next=True,
            )
            return

        # Step 3: Home -> Add playlist
        if s == 3:
            if self.stack.currentWidget() is not self.library_page:
                self._show_library_page()
                return

            target = getattr(self, "_add_tile", None)
            if target is None:
                self._tutorial_step = 4
                self._tutorial_refresh()
                return

            show_for(
                target,
                title="Add a playlist",
                body="Click Add to choose a Spotify playlist and add it to your library.",
                show_next=False,
            )
            return

        # Step 4: Add page -> confirm add
        if s == 4:
            if self.stack.currentWidget() is not self.add_page:
                # If the user navigated away (Home/Cancel), don’t force them back.
                if getattr(self, "_tutorial_step4_intro_shown", False):
                    self._tutorial_soft_hide()
                    return
                self._show_add_page()
                return
            if getattr(self, "_tutorial_tip_suppressed", False):
                self._tutorial_soft_hide()
                return

            if not getattr(self, "_tutorial_step4_intro_shown", False):
                show_for(
                    getattr(self.add_page, "selected_lbl", None),
                    title="Select playlists",
                    body="Select one or more playlists (or paste a URL), then click Add to Library.",
                    next_text="Got it",
                    show_next=True,
                    next_enabled_override=True,
                )
                self._tutorial_step4_intro_shown = True
                return

            # Safety fallback: if we ever re-enter step 4, keep it hidden.
            self._tutorial_tip_suppressed = True
            self._tutorial_soft_hide()
            return

        # Step 5: Home -> Sync
        if s == 5:
            if self.stack.currentWidget() is not self.library_page:
                self._show_library_page()
                return
            show_for(
                self.sync_now,
                title="Start your first sync",
                body="Click SYNC NOW to download missing songs and update the Rekordbox XML.",
                show_next=False,
            )
            return

        # Step 6: Sync started -> Details
        if s == 6:
            if self.stack.currentWidget() is not self.library_page:
                self._show_library_page()
                return
            show_for(
                self.details_btn,
                title="Sync started",
                body="Click Details to watch progress.",
                show_next=False,
            )
            return

        # Step 7: Back to Settings (after opening Details)
        if s == 7:
            if self.stack.currentWidget() is not self.library_page:
                self._show_library_page()
                return
            show_for(
                self.btn_settings,
                title="Next: Rekordbox",
                body="Go to Settings to connect Rekordbox XML import and refresh.",
                show_next=False,
            )
            return

        # Step 8: Settings -> Rekordbox setup
        if s == 8:
            if self.stack.currentWidget() is not self.settings_page:
                self._show_settings_page()
                return
            show_for(
                getattr(self, "rekordbox_learn", None),
                title="Connect to Rekordbox",
                body="Open the setup guide and follow the steps to enable XML import and refresh.",
                show_next=False,
                prefer_below=False,
                arrow_gap=28,
            )
            return

    def _toggle_details(self, on: bool) -> None:
        self.details_btn.setText("Details ▴" if on else "Details ▾")

        splitter = getattr(self, "library_splitter", None)
        if on:
            self.details_panel.setMaximumHeight(16777215)
            self.details_panel.setVisible(True)
            if splitter is not None:
                total_h = max(1, splitter.height())
                open_h = min(360, max(220, total_h // 3))
                splitter.setSizes([open_h, max(1, total_h - open_h)])
            if self._is_sync_running():
                self._render_sync_details()
            else:
                self._load_downloaded_details()
            # Tutorial: once Details is opened, advance to "Back to Settings".
            try:
                if getattr(self, "_tutorial_active", False) and int(getattr(self, "_tutorial_step", 0) or 0) == 6:
                    self._tutorial_step = 7
                    self._tutorial_refresh()
            except Exception:
                pass
        else:
            if splitter is not None:
                total_h = max(1, splitter.height())
                splitter.setSizes([0, total_h])
            self.details_panel.setVisible(False)
            self.details_panel.setMaximumHeight(0)

    def _clear_downloads(self) -> None:
        while self.downloads_lay.count():
            item = self.downloads_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _load_downloaded_details(self) -> None:
        self._clear_downloads()

        try:
            from db import get_conn

            conn = get_conn()
            if not self._run_started_at_sql:
                rows = []
            else:
                rows = conn.execute(
                    """
                    SELECT f.track_id, f.file_path, f.downloaded_at,
                           t.name AS track_name, t.artist AS track_artist, t.cover_url AS cover_url
                    FROM files f
                    JOIN tracks t ON t.id = f.track_id
                    WHERE f.downloaded_at >= ?
                    ORDER BY f.downloaded_at DESC
                    LIMIT 50
                    """,
                    (self._run_started_at_sql,),
                ).fetchall()
        except Exception:
            rows = []

        count = len(rows)
        self.details_title.setText(f"Downloaded ({count})")
        if count == 0:
            empty = QLabel("No new downloads in this run.")
            empty.setStyleSheet("color: #a9b0bb;")
            self.downloads_lay.addWidget(empty)
            return

        for r in rows:
            tid = r["track_id"]
            title = f"{r['track_artist']} — {r['track_name']}"
            try:
                pls = conn.execute(
                    """
                    SELECT DISTINCT p.name
                    FROM playlist_tracks pt
                    JOIN playlists p ON p.id = pt.playlist_id
                    WHERE pt.track_id = ?
                    ORDER BY p.name
                    """,
                    (tid,),
                ).fetchall()
                pl_names = ", ".join([p["name"] for p in pls])
            except Exception:
                pl_names = ""
            subtitle = pl_names or "(no playlist)"
            self.downloads_lay.addWidget(
                DownloadRow(title=title, subtitle=subtitle, image_url=r["cover_url"], net=self._net)
            )

        self.downloads_lay.addStretch(1)

    def _reset_run_stats(self) -> None:
        self._playlists_total = None
        self._missing_total = None
        self._downloaded_ok = None
        self._download_total = None
        self._tag_current = None
        self._tag_total = None
        self._last_line = ""
        self._phase_text = ""

    def _debug_mode_enabled(self) -> bool:
        try:
            v = self._settings.value("debug_mode", False)
            if isinstance(v, bool):
                return bool(v)
            s = str(v or "").strip().lower()
            return s in {"1", "true", "yes", "on"}
        except Exception:
            return False

    def _on_debug_mode_toggled(self, checked: bool) -> None:
        try:
            self._settings.setValue("debug_mode", bool(checked))
        except Exception:
            pass
        try:
            self.debug_toggle.setText("On" if checked else "Off")
        except Exception:
            pass

    def _refresh_debug_toggle_ui(self) -> None:
        try:
            on = self._debug_mode_enabled()
            self.debug_toggle.blockSignals(True)
            self.debug_toggle.setChecked(on)
            self.debug_toggle.setText("On" if on else "Off")
        except Exception:
            pass
        finally:
            try:
                self.debug_toggle.blockSignals(False)
            except Exception:
                pass

    def _set_phase(self, text: str) -> None:
        self._phase_text = text
        try:
            if self._is_sync_running():
                self.run_detail.setText(text)
        except Exception:
            pass

    def _set_progress_indeterminate(self, on: bool) -> None:
        if on:
            self.progress.setRange(0, 0)  # busy
        else:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 1)
                self.progress.setValue(0)

    def _update_run_detail(self, *, final: bool = False) -> None:
        parts = []
        if self._playlists_total is not None:
            parts.append(f"Playlists: {self._playlists_total}")
        if self._missing_total is not None:
            parts.append(f"Missing: {self._missing_total}")
        if self._downloaded_ok is not None and self._download_total is not None:
            parts.append(f"Downloaded: {self._downloaded_ok}/{self._download_total}")
        if self._tag_current is not None and self._tag_total is not None:
            parts.append(f"Tagging: {self._tag_current}/{self._tag_total}")

        msg = " • ".join(parts)

        if self._debug_mode_enabled():
            if not msg and self._last_line:
                msg = self._last_line
            if final and self._last_line:
                # Keep the last line around in case the run didn't emit all counters.
                msg = msg or self._last_line
        else:
            # In non-debug mode, avoid noisy raw subprocess lines.
            if not msg and self._phase_text:
                msg = self._phase_text
        self.run_detail.setText(msg)

    def _on_proc_output(self, proc: QProcess) -> None:
        raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        if not raw:
            return

        # main.py and helpers often print progress with carriage returns
        raw = raw.replace("\r", "\n")
        for line in [ln.strip() for ln in raw.splitlines() if ln.strip()]:
            if self._debug_mode_enabled():
                self._last_line = line
            self._parse_line(line)
        self._update_run_detail()
        self._maybe_refresh_downloads()

    def _maybe_refresh_downloads(self) -> None:
        if not self.details_panel.isVisible():
            return
        # While syncing, show live items rather than only DB-backed downloads.
        if self._is_sync_running() and self._sync_items:
            self._render_sync_details()
            return
        if not self._run_started_at_sql:
            return
        self._load_downloaded_details()

    def _render_sync_details(self) -> None:
        self._clear_downloads()

        total = len(self._sync_items_order)
        show_cap = 200
        shown = min(total, show_cap)

        if total == 0:
            self.details_title.setText("Details")
            empty = QLabel("Waiting for tracks…")
            empty.setStyleSheet("color: #a9b0bb;")
            self.downloads_lay.addWidget(empty)
            return

        if total > show_cap:
            self.details_title.setText(f"Sync items ({shown} of {total})")
        else:
            self.details_title.setText(f"Sync items ({total})")

        for tid in self._sync_items_order[:show_cap]:
            it = self._sync_items.get(tid) or {}
            title = it.get("title") or tid
            subtitle = it.get("status") or "Queued"
            self.downloads_lay.addWidget(
                DownloadRow(
                    title=title,
                    subtitle=subtitle,
                    image_url=it.get("cover_url"),
                    net=self._net,
                )
            )

        if total > show_cap:
            more = QLabel(f"… and {total - show_cap} more")
            more.setStyleSheet("color: #a9b0bb;")
            self.downloads_lay.addWidget(more)

        self.downloads_lay.addStretch(1)

    def _parse_line(self, line: str) -> None:
        # Hide noisy backend debug output unless Debug mode is enabled.
        if not self._debug_mode_enabled():
            if line.startswith("[sync]") or line.startswith("[spotify]"):
                return

        # Phase mapping (friendly UI text when Debug mode is off).
        if not self._debug_mode_enabled():
            low = line.lower()
            if "syncing" in low and "spotify" in low:
                self._set_phase("Fetching info from Spotify…")
            elif "searching" in low or "tier" in low:
                self._set_phase("Searching internet for songs…")
            elif "downloading" in low:
                self._set_phase("Downloading…")
            elif "tagging" in low:
                self._set_phase("Tagging…")
            elif "export" in low:
                self._set_phase("Exporting…")

        if line.startswith("@TS "):
            try:
                payload = json.loads(line[4:].strip())
            except Exception:
                return

            t = str(payload.get("type") or "")

            if t == "retry":
                try:
                    self._sync_attempt = int(payload.get("attempt") or 0) or None
                except Exception:
                    self._sync_attempt = None
                try:
                    self._sync_attempt_max = int(payload.get("max") or 0) or None
                except Exception:
                    self._sync_attempt_max = None

                remaining = payload.get("remaining")
                if remaining is not None:
                    try:
                        remaining_i = int(remaining)
                        self._download_total = remaining_i
                        self._downloaded_ok = 0
                        self.progress.setRange(0, max(1, remaining_i))
                        self.progress.setValue(0)
                    except Exception:
                        pass

                try:
                    if self._sync_attempt and self._sync_attempt > 1 and self._sync_attempt_max:
                        self.run_status.setText(f"Syncing… (retry {self._sync_attempt}/{self._sync_attempt_max})")
                except Exception:
                    pass
                return

            if t == "queue":
                tid = str(payload.get("track_id") or "").strip()
                if not tid:
                    return
                artist = str(payload.get("artist") or "").strip()
                title = str(payload.get("title") or "").strip()
                full_title = f"{artist} — {title}" if artist and title else (title or artist or tid)

                if tid not in self._sync_items:
                    self._sync_items_order.append(tid)
                self._sync_items[tid] = {
                    "title": full_title,
                    "cover_url": payload.get("cover_url"),
                    "status": "Queued",
                }
                if self.details_panel.isVisible() and self._is_sync_running():
                    self._render_sync_details()
                return

            if t == "queue_truncated":
                # No popup; Details already reflects the truncation.
                return

            if t == "download_start":
                tid = str(payload.get("track_id") or "").strip()
                if not tid:
                    return
                if not self._debug_mode_enabled():
                    self._set_phase("Downloading…")
                it = self._sync_items.get(tid) or {}
                it["status"] = "Downloading…"
                self._sync_items[tid] = it
                if self.details_panel.isVisible() and self._is_sync_running():
                    self._render_sync_details()
                return

            if t == "download_done":
                tid = str(payload.get("track_id") or "").strip()
                if not tid:
                    return

                ok = bool(payload.get("ok"))
                err = str(payload.get("error") or "").strip()
                it = self._sync_items.get(tid) or {}
                it["status"] = "Downloaded" if ok else (f"Failed: {err}" if err else "Failed")
                self._sync_items[tid] = it

                # Update determinate progress for this attempt.
                try:
                    completed = int(payload.get("completed") or 0)
                    total = int(payload.get("total") or 0)
                    if total > 0:
                        self.progress.setRange(0, total)
                        self.progress.setValue(min(completed, total))
                        self._download_total = total
                        if ok:
                            self._downloaded_ok = int(self._downloaded_ok or 0) + 1
                except Exception:
                    pass

                if self.details_panel.isVisible() and self._is_sync_running():
                    self._render_sync_details()
                return

            if t == "missing_final":
                # No popup; missing items remain visible in the library.
                return

            return

        # Examples we want to parse:
        # "🔄 Syncing 74 playlists from Spotify..."
        # "📊 Status: 9 to download"
        # "⬇️  Downloading 9 tracks..."
        # "✅ Downloaded 9/9 tracks"
        # "  Tagging: 3/9  (...)"

        m = re.search(r"Syncing\s+(\d+)\s+playlists", line, re.IGNORECASE)
        if m:
            self._playlists_total = int(m.group(1))
            return

        m = re.search(r"Status:\s*(\d+)\s+to download", line, re.IGNORECASE)
        if m:
            self._missing_total = int(m.group(1))
            self._download_total = self._missing_total
            return

        m = re.search(r"Downloading\s+(\d+)\s+tracks", line, re.IGNORECASE)
        if m:
            self._download_total = int(m.group(1))
            if self._missing_total is None:
                self._missing_total = self._download_total
            # If we don't have structured per-track events, keep the bar indeterminate.
            if self._sync_attempt is None:
                self._set_progress_indeterminate(True)
            return

        m = re.search(r"Downloaded\s+(\d+)/(\d+)\s+tracks", line, re.IGNORECASE)
        if m:
            self._downloaded_ok = int(m.group(1))
            self._download_total = int(m.group(2))
            return

        m = re.search(r"Tagging:\s*(\d+)/(\d+)", line, re.IGNORECASE)
        if m:
            self._tag_current = int(m.group(1))
            self._tag_total = int(m.group(2))
            self.progress.setRange(0, self._tag_total)
            self.progress.setValue(self._tag_current)
            return
