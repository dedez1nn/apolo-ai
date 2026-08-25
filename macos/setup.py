"""Empacota macos/apolo_tray.py num app bundle via py2app.

Rodar através de macos/build.sh, que gera o apolo.icns antes (via `sips`/
`iconutil`, nativos do macOS) e cria um venv de build isolado.
"""

from setuptools import setup

APP = ["apolo_tray.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "apolo.icns",
    "plist": {
        "LSUIElement": True,  # só barra de menu, sem ícone no Dock
        "CFBundleName": "Apolo",
        "CFBundleDisplayName": "Apolo",
        "CFBundleIdentifier": "dev.apolo.tray",
        "CFBundleShortVersionString": "1.0.0",
    },
    "packages": ["rumps"],
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
