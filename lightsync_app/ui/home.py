from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re
import os

from PySide6.QtCore import Qt, QUrl, QSize, QRectF, QObject, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QFontMetrics
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QProcess


_DEBUG_IMAGES = os.getenv("LIGHTSYNC_DEBUG_IMAGES", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


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
            req.setRawHeader(b"User-Agent", b"LightSync")
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


class PlaylistCard(QFrame):
    def __init__(self, model: CardModel, *, is_add: bool = False, on_click=None, on_toggle_selected=None):
        super().__init__()
        self.setObjectName("PlaylistCard")
        self.setFixedSize(170, 170)

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
        self._dot_label: Optional[QLabel] = None
        self._raw_title: str = model.title
        self._raw_creator: str = model.creator or ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        if is_add:
            lay.addStretch(1)
            plus = QLabel("+")
            plus.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            f = QFont()
            f.setPointSize(34)
            f.setBold(True)
            plus.setFont(f)
            plus.setStyleSheet(f"color: {_TEXT};")
            lay.addWidget(plus)

            add = QLabel("Add")
            add.setAlignment(Qt.AlignHCenter)
            f2 = QFont()
            f2.setPointSize(14)
            f2.setBold(True)
            add.setFont(f2)
            add.setStyleSheet(f"color: {_TEXT};")
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
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        # Status dot only (tooltip shows status text)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        dot = QLabel("●")
        dot.setFixedSize(14, 14)
        dot.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        dot.setStyleSheet("background: transparent;")
        dot.setToolTip(model.status)
        dot.setToolTipDuration(5000)
        self._dot_label = dot

        if model.status.lower().startswith("synced"):
            dot.setStyleSheet(f"color: {_GREEN}; background: transparent;")
        elif model.status.lower().startswith("sync"):
            dot.setStyleSheet(f"color: {_ORANGE}; background: transparent;")
        else:
            dot.setStyleSheet("color: #5b6270; background: transparent;")

        top_row.addWidget(dot)
        top_row.addStretch(1)
        v.addLayout(top_row)

        # Bottom-left info panel: creator above title
        v.addStretch(1)

        panel = QFrame()
        panel.setObjectName("TextPanel")
        panel.setStyleSheet(
            "QFrame#TextPanel {"
            "background: rgba(0, 0, 0, 0.45);"
            "border-radius: 12px;"
            "}"
        )
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(10, 8, 10, 8)
        panel_lay.setSpacing(4)

        self._creator_label = QLabel(model.creator or "")
        self._creator_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._creator_label.setStyleSheet("color: #a9b0bb; font-weight: 700; background: transparent;")
        self._creator_label.setFixedHeight(18)
        self._creator_label.setToolTip(self._raw_creator)
        self._creator_label.setToolTipDuration(8000)
        self._creator_label.setWordWrap(False)
        if not (self._raw_creator or "").strip():
            self._creator_label.setVisible(False)
            self._creator_label.setFixedHeight(0)
        panel_lay.addWidget(self._creator_label)

        self._title_label = QLabel()
        ft = QFont()
        ft.setPointSize(13)
        ft.setBold(True)
        self._title_label.setFont(ft)
        self._title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title_label.setStyleSheet("color: white; background: transparent;")
        self._title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self._title_label.setFixedHeight(22)
        self._title_label.setToolTip(self._raw_title)
        self._title_label.setToolTipDuration(8000)
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

    def mousePressEvent(self, event):
        if self._selection_enabled and event.button() == Qt.LeftButton:
            self.set_selected(not self._selected)
            if self._on_toggle_selected is not None:
                try:
                    self._on_toggle_selected(self._selected)
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


class SelectablePlaylistCard(QFrame):
    toggled = Signal(str, bool)  # playlist_id, selected

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
        self.setFixedSize(170, 170)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("SelectablePlaylistCard")

        # Cover
        self._img = RoundedPixmapLabel(radius=18, parent=self)
        self._img.setGeometry(0, 0, self.width(), self.height())
        self._img.setStyleSheet("border-radius: 18px; background: #1a1e24;")

        # Dimmer
        self._dimmer = QFrame(self)
        self._dimmer.setGeometry(0, 0, self.width(), self.height())
        self._dimmer.setStyleSheet("background: rgba(0,0,0,0.35); border-radius: 18px;")

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

        # Bottom-left title panel (matches home formatting)
        overlay = QFrame(self)
        overlay.setGeometry(0, 0, self.width(), self.height())
        overlay.setStyleSheet("background: transparent; border-radius: 18px;")
        v = QVBoxLayout(overlay)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        v.addStretch(1)

        panel = QFrame()
        panel.setObjectName("TextPanel")
        panel.setStyleSheet(
            "QFrame#TextPanel {"
            "background: rgba(0, 0, 0, 0.45);"
            "border-radius: 12px;"
            "}"
        )
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(10, 8, 10, 8)
        panel_lay.setSpacing(0)

        self._title = QLabel(name or "")
        ft = QFont()
        ft.setPointSize(13)
        ft.setBold(True)
        self._title.setFont(ft)
        self._title.setStyleSheet("color: white; background: transparent;")
        self._title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title.setFixedHeight(22)
        self._title.setToolTip(name or "")
        self._title.setToolTipDuration(8000)
        panel_lay.addWidget(self._title)

        v.addWidget(panel, 0, Qt.AlignLeft | Qt.AlignBottom)

        if image_url:
            req = QNetworkRequest(QUrl(image_url))
            req.setRawHeader(b"User-Agent", b"LightSync")
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._img.setGeometry(0, 0, self.width(), self.height())
        self._dimmer.setGeometry(0, 0, self.width(), self.height())
        self._sel_mask.setGeometry(0, 0, self.width(), self.height())
        self._check.setGeometry(self.width() - 42, 10, 32, 32)
        for child in self.findChildren(QFrame):
            if child is not self._dimmer and child is not self._sel_mask and child.objectName() == "":
                # ignore
                pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.set_selected(not self._selected)
            self.toggled.emit(self._pid, self._selected)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._sel_mask.setVisible(selected)
        self._check.setVisible(selected)


class AddPlaylistsPage(QWidget):
    added = Signal()
    cancelled = Signal()

    def __init__(self, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._net = QNetworkAccessManager(self)
        self._all: list[dict] = []
        self._filtered: list[dict] = []
        self._selected: set[str] = set()

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

        self.filter = QComboBox()
        self.filter.addItems(["Filter", "All"])
        self.filter.setFixedHeight(36)
        self.filter.setStyleSheet(
            "QComboBox {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 12px;"
            "color: #e7eaf0;"
            "}"
            "QComboBox::drop-down { border: none; }"
        )
        controls.addWidget(self.filter, 0)
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

        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.content)
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
            from sync import read_synced

            already = set(read_synced())
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

    def _render_grid(self) -> None:
        self._clear_grid()
        col_count = 6
        r = 0
        c = 0
        for p in self._filtered:
            pid = p["id"]
            tile = SelectablePlaylistCard(
                playlist_id=pid,
                name=p.get("name") or "",
                image_url=p.get("image_url"),
                net=self._net,
            )
            if pid in self._selected:
                tile.set_selected(True)
            tile.toggled.connect(self._on_toggled)
            self.grid.addWidget(tile, r, c)
            c += 1
            if c >= col_count:
                r += 1
                c = 0
        self._update_buttons()

    def _on_toggled(self, pid: str, selected: bool) -> None:
        if selected:
            self._selected.add(pid)
        else:
            self._selected.discard(pid)
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

    def _append_to_synced_txt(self, entries: list[tuple[str, str]]) -> None:
        from pathlib import Path

        path = Path("synced.txt")
        existing = path.read_text().splitlines() if path.exists() else []

        existing_ids = set()
        try:
            from sync import read_synced

            existing_ids = set(read_synced())
        except Exception:
            pass

        out = list(existing)
        if not out:
            out.append("# LightSync Playlist Configuration")
            out.append("# Format: PLAYLIST_NAME = spotify_playlist_url")
            out.append("")

        for pid, name in entries:
            if pid in existing_ids:
                continue
            url = f"https://open.spotify.com/playlist/{pid}"
            safe_name = (name or pid).replace("\n", " ").strip()
            out.append(f"{safe_name} = {url}")
            existing_ids.add(pid)

        path.write_text("\n".join(out).rstrip() + "\n")

    def _add_confirm(self) -> None:
        to_add: list[tuple[str, str]] = []
        db_rows: list[dict] = []

        by_id = {p["id"]: p for p in self._all if p.get("id")}
        for pid in sorted(self._selected):
            p = by_id.get(pid) or {}
            to_add.append((pid, p.get("name") or pid))
            db_rows.append(
                {
                    "id": pid,
                    "name": p.get("name") or pid,
                    "creator": p.get("creator"),
                    "image_url": p.get("image_url"),
                    "snapshot_id": None,
                }
            )

        url_pid = self._extract_playlist_id(self.url_in.text() or "")
        if url_pid:
            name = url_pid
            creator = None
            image_url = None
            try:
                from spotify_client import SpotifyClient

                meta = SpotifyClient.get_playlist_metadata(url_pid)
                name = meta.get("name") or name
                creator = meta.get("creator")
                image_url = meta.get("image_url")
            except Exception:
                pass
            to_add.append((url_pid, name))
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

        self._append_to_synced_txt(to_add)

        # Best-effort: upsert into DB so Home shows them immediately.
        try:
            from db import get_conn, upsert_playlist

            conn = get_conn()
            for row in db_rows:
                upsert_playlist(conn, row)
            conn.commit()
        except Exception:
            pass

        self.added.emit()
        # HomeWindow listens to `added` and navigates back to Library.


class HomeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LightSync")
        # Fixed-width window (5 tiles across); allow vertical resizing only.
        fixed_w = 1000
        self.setMinimumSize(fixed_w, 700)
        self.setMinimumWidth(fixed_w)
        self.setMaximumWidth(fixed_w)

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

        user = QLabel("pete.cy")
        user.setStyleSheet(f"color: {_MUTED};")
        top.addWidget(user)

        avatar = QLabel(" ")
        avatar.setFixedSize(26, 26)
        avatar.setStyleSheet("background: white; border-radius: 13px;")
        top.addWidget(avatar)

        self.btn_home = QToolButton()
        self.btn_home.setText("⌂")
        self.btn_home.setStyleSheet(
            "QToolButton {"
            "color: #d7dbe3;"
            "background: transparent;"
            "font-size: 16px;"
            "padding: 4px 6px;"
            "}"
            "QToolButton:hover { color: white; }"
        )
        self.btn_home.clicked.connect(self._show_library_page)
        top.addWidget(self.btn_home)

        for icon in ["🔔", "⚙"]:
            b = QToolButton()
            b.setText(icon)
            b.setStyleSheet(
                "QToolButton {"
                "color: #d7dbe3;"
                "background: transparent;"
                "font-size: 16px;"
                "padding: 4px 6px;"
                "}"
                "QToolButton:hover { color: white; }"
            )
            top.addWidget(b)

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

        self.filter = QComboBox()
        self.filter.addItems(["Filter", "Synced", "Syncing", "Frozen", "Errors"])
        self.filter.setFixedHeight(36)
        self.filter.setStyleSheet(
            "QComboBox {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 12px;"
            "color: #e7eaf0;"
            "}"
            "QComboBox::drop-down { border: none; }"
        )
        controls.addWidget(self.filter, 0)

        self.sort = QComboBox()
        self.sort.addItems(["Sort", "Latest changes", "Playlist creation date", "Playlist size", "Creator"])
        self.sort.setFixedHeight(36)
        self.sort.setStyleSheet(
            "QComboBox {"
            "background: #0f1216;"
            "border: 1px solid #2b2f36;"
            "border-radius: 18px;"
            "padding: 0 12px;"
            "color: #e7eaf0;"
            "}"
            "QComboBox::drop-down { border: none; }"
        )
        self.sort.currentIndexChanged.connect(lambda _i: self._apply_filters())
        controls.addWidget(self.sort, 0)

        controls.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(10)

        self.remove_btn = QPushButton("REMOVE")
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

        self.sync_now = QPushButton("SYNC NOW")
        self.sync_now.setFixedSize(140, 38)
        self.sync_now.setStyleSheet(
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
        right.addWidget(self.sync_now, 0, Qt.AlignRight)

        auto_row = QHBoxLayout()
        auto = QLabel("AUTO SYNC")
        auto.setStyleSheet("font-weight: 800;")
        auto_row.addWidget(auto)

        self.auto_sync = QToolButton()
        self.auto_sync.setCheckable(True)
        self.auto_sync.setChecked(True)
        self._update_toggle_style()
        self.auto_sync.clicked.connect(self._update_toggle_style)
        auto_row.addWidget(self.auto_sync)
        auto_row.setAlignment(Qt.AlignRight)
        right.addLayout(auto_row)

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
        self.downloads_content = QWidget()
        self.downloads_lay = QVBoxLayout(self.downloads_content)
        self.downloads_lay.setContentsMargins(0, 0, 0, 0)
        self.downloads_lay.setSpacing(8)
        self.downloads_scroll.setWidget(self.downloads_content)
        dp_lay.addWidget(self.downloads_scroll, 1)

        lib_outer.addWidget(self.details_panel)

        # Grid of cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(content)
        lib_outer.addWidget(scroll, 1)

        self.stack.addWidget(self.library_page)

        # -----------------
        # Add playlists page
        # -----------------
        self.add_page = AddPlaylistsPage(parent=self)
        self.add_page.added.connect(self._on_playlists_added)
        self.add_page.cancelled.connect(self._show_library_page)
        self.stack.addWidget(self.add_page)

        self._net = QNetworkAccessManager(self)
        self._cards_by_pid = {}
        self._all_playlists: list[dict] = []
        self._pix_cache: dict[str, QPixmap] = {}
        self._sync_proc: Optional[QProcess] = None
        self.sync_now.clicked.connect(self._start_sync)

        self._downloads_timer = QTimer(self)
        self._downloads_timer.setInterval(1000)
        self._downloads_timer.timeout.connect(self._maybe_refresh_downloads)

        self._selected_playlist_ids: set[str] = set()

        # Parsed run stats
        self._playlists_total: Optional[int] = None
        self._missing_total: Optional[int] = None
        self._downloaded_ok: Optional[int] = None
        self._download_total: Optional[int] = None
        self._tag_current: Optional[int] = None
        self._tag_total: Optional[int] = None
        self._last_line: str = ""
        self._run_started_at_sql: Optional[str] = None
        self._load_cards_from_db()

    def _show_library_page(self) -> None:
        self.page_title.setText("Synced Library")
        self.stack.setCurrentWidget(self.library_page)

    def _show_add_page(self) -> None:
        self.page_title.setText("Add Playlists")
        self.stack.setCurrentWidget(self.add_page)
        self.add_page.refresh()

    def _update_toggle_style(self):
        on = self.auto_sync.isChecked()
        self.auto_sync.setText("   " if on else "   ")
        self.auto_sync.setFixedSize(46, 22)
        self.auto_sync.setStyleSheet(
            "QToolButton {"
            f"background: {(_BLUE if on else '#2b2f36')};"
            "border-radius: 11px;"
            "}"
        )

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _load_cards_from_db(self) -> None:
        try:
            from db import get_conn

            conn = get_conn()
            playlists = conn.execute(
                """
                SELECT
                  p.id,
                  p.name,
                  p.creator,
                  p.image_url,
                  p.updated_at,
                  p.created_at,
                  COUNT(pt.track_id) AS track_count,
                  COALESCE(SUM(CASE
                    WHEN pt.track_id IS NOT NULL AND f.track_id IS NULL THEN 1
                    ELSE 0
                  END), 0) AS missing_count
                FROM playlists p
                LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
                LEFT JOIN files f ON f.track_id = pt.track_id
                GROUP BY p.id
                ORDER BY p.name
                """
            ).fetchall()
        except Exception:
            playlists = []

        computed: list[dict] = []
        for p in playlists:
            pid = p["id"]
            name = p["name"]
            creator = p["creator"] if "creator" in p.keys() else None
            image_url = p["image_url"] if "image_url" in p.keys() else None

            updated_at = p["updated_at"] if "updated_at" in p.keys() else None
            created_at = p["created_at"] if "created_at" in p.keys() else None
            track_count = int(p["track_count"]) if "track_count" in p.keys() and p["track_count"] is not None else 0

            status = "Synced"
            try:
                missing = int(p["missing_count"]) if "missing_count" in p.keys() and p["missing_count"] is not None else 0
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
                    "updated_at": updated_at,
                    "created_at": created_at,
                    "track_count": track_count,
                }
            )

        self._all_playlists = computed
        self._apply_filters()

    def _apply_filters(self) -> None:
        q = (self.search.text() or "").strip().lower()
        # For now, search only. (Filter dropdown can be layered on later.)
        if not q:
            items = list(self._all_playlists)
        else:
            items = [
                p
                for p in self._all_playlists
                if (q in (p.get("name") or "").lower()) or (q in (p.get("creator") or "").lower())
            ]

        sort_choice = (self.sort.currentText() if hasattr(self, "sort") else "Sort")
        if sort_choice == "Latest changes":
            items.sort(key=lambda p: (p.get("updated_at") or ""), reverse=True)
        elif sort_choice == "Playlist creation date":
            items.sort(key=lambda p: (p.get("created_at") or ""), reverse=True)
        elif sort_choice == "Playlist size":
            items.sort(key=lambda p: int(p.get("track_count") or 0), reverse=True)
        elif sort_choice == "Creator":
            items.sort(key=lambda p: ((p.get("creator") or "").lower(), (p.get("name") or "").lower()))

        self._render_playlist_cards(items)

    def _render_playlist_cards(self, playlists: list[dict]) -> None:
        self._clear_grid()
        self._cards_by_pid = {}

        # Add tile first
        self.grid.addWidget(
            PlaylistCard(
                CardModel(title="Add", status=""),
                is_add=True,
                on_click=self._open_add_playlists,
            ),
            0,
            0,
        )

        col_count = 5
        r = 0
        c = 1

        for p in playlists:
            pid = p["id"]
            name = p["name"]
            image_url = p.get("image_url")
            status = p.get("status") or "Synced"
            creator = p.get("creator")

            card = PlaylistCard(
                CardModel(title=name, status=status, creator=creator, image_url=image_url),
                on_toggle_selected=(lambda selected, pid=pid: self._on_playlist_selected(pid, selected)),
            )
            card.set_selection_enabled(True)
            card.set_selected(pid in self._selected_playlist_ids)
            self.grid.addWidget(card, r, c)
            self._cards_by_pid[pid] = card

            if image_url:
                if image_url in self._pix_cache:
                    card.set_cover_pixmap(self._pix_cache[image_url])
                else:
                    self._fetch_cover(pid, image_url)

            c += 1
            if c >= col_count:
                r += 1
                c = 0

        # No expanding spacer here; grid alignment keeps cards top-left.

    def _open_add_playlists(self) -> None:
        self._show_add_page()

    def _on_playlist_selected(self, pid: str, selected: bool) -> None:
        if selected:
            self._selected_playlist_ids.add(pid)
        else:
            self._selected_playlist_ids.discard(pid)
        self._update_remove_button()

    def _update_remove_button(self) -> None:
        n = len(self._selected_playlist_ids)
        self.remove_btn.setVisible(n > 0)
        self.remove_btn.setEnabled(n > 0)
        self.remove_btn.setText(f"REMOVE ({n})" if n else "REMOVE")

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

        # Update synced.txt (remove lines whose playlist id matches)
        try:
            from sync import _parse_synced_line  # type: ignore

            parse_line = _parse_synced_line
        except Exception:

            def parse_line(line: str):
                raw = (line or "").strip()
                if not raw or raw.startswith("#"):
                    return None
                # strip trailing comment
                if "#" in raw:
                    raw = raw.split("#", 1)[0].strip()
                if ("=" in raw) or ("|" in raw):
                    right = raw.split("=", 1)[1] if "=" in raw else raw.split("|", 1)[1]
                    token = right.strip()
                else:
                    token = raw
                token = token.strip()
                if "open.spotify.com/playlist/" in token:
                    pid = token.split("/playlist/", 1)[1].split("?", 1)[0].split("/", 1)[0]
                    return (pid, None)
                if token.startswith("spotify:playlist:"):
                    return (token.split(":")[-1], None)
                return (token, None)

        synced_path = Path("synced.txt")
        if synced_path.exists():
            kept: list[str] = []
            for line in synced_path.read_text().splitlines():
                parsed = parse_line(line)
                if parsed and parsed[0] in ids:
                    continue
                kept.append(line)
            synced_path.write_text("\n".join(kept).rstrip() + "\n")

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

    def _on_playlists_added(self) -> None:
        self._load_cards_from_db()
        self._show_library_page()

    def _fetch_cover(self, playlist_id: str, url: str) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"LightSync")
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
        # Run existing main.py in the background so we reuse the current pipeline.
        if self._sync_proc is not None and self._sync_proc.state() != QProcess.NotRunning:
            return

        self.sync_now.setEnabled(False)
        self.sync_now.setText("SYNCING…")

        self._reset_run_stats()
        self.run_status.setText("Syncing…")
        self._set_progress_indeterminate(True)
        self._update_run_detail()

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

        proc.setProgram(sys.executable)
        proc.setArguments(["-u", "main.py"])
        proc.setProcessChannelMode(QProcess.MergedChannels)

        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))

        def finished(_code, _status):
            self._sync_proc = None
            self.sync_now.setEnabled(True)
            self.sync_now.setText("SYNC NOW")

            if self._downloads_timer.isActive():
                self._downloads_timer.stop()

            self._set_progress_indeterminate(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.run_status.setText("Done")
            self._update_run_detail(final=True)
            self._load_downloaded_details()
            self._load_cards_from_db()

        proc.finished.connect(finished)
        self._sync_proc = proc
        if not self._downloads_timer.isActive():
            self._downloads_timer.start()
        proc.start()

    def _toggle_details(self, on: bool) -> None:
        self.details_panel.setVisible(on)
        self.details_btn.setText("Details ▴" if on else "Details ▾")
        if on:
            self._load_downloaded_details()

    def _clear_downloads(self) -> None:
        while self.downloads_lay.count():
            item = self.downloads_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

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
        if not msg and self._last_line:
            msg = self._last_line
        if final and self._last_line:
            # Keep the last line around in case the run didn't emit all counters.
            msg = msg or self._last_line
        self.run_detail.setText(msg)

    def _on_proc_output(self, proc: QProcess) -> None:
        raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="ignore")
        if not raw:
            return

        # main.py and helpers often print progress with carriage returns
        raw = raw.replace("\r", "\n")
        for line in [ln.strip() for ln in raw.splitlines() if ln.strip()]:
            self._last_line = line
            self._parse_line(line)
        self._update_run_detail()
        self._maybe_refresh_downloads()

    def _maybe_refresh_downloads(self) -> None:
        if not self.details_panel.isVisible():
            return
        if not self._run_started_at_sql:
            return
        self._load_downloaded_details()

    def _parse_line(self, line: str) -> None:
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
            # We don't have per-track download progress from main.py reliably,
            # so keep the bar indeterminate during download.
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
