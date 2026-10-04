# PyInstaller: сборка Rem.exe (папкой — быстрее запуск, чем один файл).
# Запуск из корня репозитория:  pyinstaller packaging/rem.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

datas = (
    collect_data_files("onnx_asr")           # предобработчики звука (*.onnx) внутри пакета
    + collect_data_files("vosk")
    + copy_metadata("onnx_asr")
)
binaries = collect_dynamic_libs("vosk")      # libvosk.dll и зависимости

a = Analysis(
    ["launcher.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "pystray._win32",                    # бэкенд трея выбирается динамически
        "win32com.client", "pythoncom",
        "pycaw.pycaw", "comtypes.stream",
        "webrtcvad",
    ],
    excludes=["matplotlib", "scipy", "pandas", "torch", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Rem",
    icon="rem.ico",
    console=False,                           # без чёрного окна
)
coll = COLLECT(exe, a.binaries, a.datas, name="Rem")
