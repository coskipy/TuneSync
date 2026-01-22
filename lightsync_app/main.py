from __future__ import annotations

import sys
from PySide6.QtWidgets import QApplication

from lightsync_app.ui.home import HomeWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LightSync")

    # Ensure DB schema exists before the UI queries it.
    try:
        from db import init_db

        init_db()
    except Exception:
        # UI can still launch with an empty state.
        pass

    w = HomeWindow()
    w.show()

    return app.exec()
