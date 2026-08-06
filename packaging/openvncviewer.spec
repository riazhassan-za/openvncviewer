# PyInstaller spec: one self-contained OpenVNCViewer.exe
#
# Run from the repository root:
#     pyinstaller --noconfirm --clean packaging/openvncviewer.spec
#
# The viewer only needs QtCore/QtGui/QtWidgets. PySide6-Addons ships a lot more
# (WebEngine, Quick, 3D, Multimedia...), so everything unused is excluded to
# keep the single file down to a sane size.

import os

ROOT = os.path.dirname(SPECPATH)  # noqa: F821 - SPECPATH is injected
SRC = os.path.join(ROOT, "src")

EXCLUDED_QT = [
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic",
    "Qt3DRender", "QtBluetooth", "QtCharts", "QtConcurrent",
    "QtDataVisualization", "QtDesigner", "QtGraphs", "QtGraphsWidgets",
    "QtHelp", "QtHttpServer", "QtLocation", "QtMultimedia",
    "QtMultimediaWidgets", "QtNetworkAuth", "QtNfc", "QtOpenGL",
    "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtPositioning",
    "QtQml", "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickTest",
    "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors",
    "QtSerialBus", "QtSerialPort", "QtSpatialAudio", "QtSql",
    "QtStateMachine", "QtTest", "QtTextToSpeech", "QtUiTools",
    "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick",
    "QtWebEngineWidgets", "QtWebSockets", "QtXml",
]

excludes = [f"PySide6.{name}" for name in EXCLUDED_QT]
excludes += ["tkinter", "unittest", "pydoc", "doctest", "numpy", "PIL"]

a = Analysis(
    # entry.py, not the package's __main__.py: PyInstaller runs the entry
    # script without a package context, which would break relative imports.
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OpenVNCViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
