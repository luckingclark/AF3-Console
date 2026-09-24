#!/usr/bin/env python3
"""Render documentation previews with empty data, placeholder paths, and isolated settings."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))


def main():
    destination = ROOT / "docs/images"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="af3-public-preview-") as temporary:
        os.environ.update(HOME=temporary, USERPROFILE=temporary,
                          AF3_CONFIG=str(Path(temporary) / "config.json"),
                          AF3_PAE_CACHE=str(Path(temporary) / "pae"),
                          QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
        os.environ.pop("AF3_BASE", None)
        os.environ.pop("AF3_SNAPSHOT", None)
        import af3_qt_app as Q
        import af3_ui_backend as B
        from PySide6 import QtCore, QtWidgets
        settings_class = QtCore.QSettings
        def isolated_settings(*args, **kwargs):
            return settings_class(str(Path(temporary) / "preview.ini"), settings_class.IniFormat)
        app = QtWidgets.QApplication([])
        app.setStyle("Fusion")
        family = Q._register_cjk_font(app)
        font = app.font()
        if family:
            font.setFamily(family)
        font.setPointSize(11)
        app.setFont(font)
        cfg = B.current_config()
        paths = {
            "HOST_BASE": "/path/to/your/workspace", "HOST_SIF": "", "HOST_DB_SOURCE": "",
            "HOST_MODELS": "/path/to/your/model_parameters",
            "HOST_OUTPUT": "/path/to/your/workspace/output",
            "HOST_MSA_DATA": "/path/to/your/workspace/msa_data",
            "HOST_INFER_DATA": "/path/to/your/workspace/infer_data",
            "HOST_CACHE": "/path/to/your/workspace/cache",
            "HOST_JAX_CACHE": "/path/to/your/workspace/af3_buckets_cache",
            "HOST_SSD_CACHE": "", "AF3_PY": "/path/to/your/af3-console/af3.py",
            "PYTHON": "/path/to/your/environment/bin/python",
        }
        cfg["editable"].update({k: v for k, v in paths.items() if k in cfg["editable"]})
        cfg["derived"].update(paths)
        for k, v in paths.items():
            if hasattr(B, k):
                setattr(B, k, v)
        Q.USER, Q.HOST = "user", "cluster"
        Q.qta = None
        # No background result scans, scheduler requests, user saves or network
        # requests are allowed while making documentation screenshots.
        with patch.object(Q, "enqueue", return_value=None), \
                patch.object(QtCore, "QSettings", side_effect=isolated_settings), \
                patch.object(B, "current_config", return_value=cfg), \
                patch.object(B.R, "CONFIG_ENV_KEYS", ()), \
                patch.object(B.R, "config_path", return_value="/path/to/your/private/config.json"):
            win = Q.App()
            assert Path(temporary) in Path(win._settings.fileName()).resolve().parents
            win._timer.stop()
            win._dash_timer.stop()
            win.resize(1440, 1080)
            win.show()
            for language, theme in (("en", "light"), ("zh", "light"), ("en", "dark"), ("zh", "dark")):
                win._set_lang(language)
                win._set_theme(theme)
                win.tabs.setCurrentIndex(0)
                for _ in range(3):
                    app.processEvents()
                if theme == "light":
                    win.grab().save(str(destination / f"setup-{language}-{theme}.png"))
                win.tabs.setCurrentIndex(win.tabs.count() - 1)
                win.help_search.clear()
                win._help_topic_key = "about"
                win._help_filter()
                for _ in range(3):
                    app.processEvents()
                win.grab().save(str(destination / f"about-{language}-{theme}.png"))
            win.close()
            app.processEvents()
        print("Six documentation previews rendered with placeholder paths and empty task lists.")


if __name__ == "__main__":
    main()
