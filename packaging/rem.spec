# PyInstaller: сборка Rem.exe (папкой — быстрее запуск, чем один файл).
# Запуск из корня репозитория:  pyinstaller packaging/rem.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))   # корень репозитория, где лежит пакет rem
sys.path.insert(0, ROOT)                                 # чтобы collect_submodules нашёл rem

datas = (
    collect_data_files("onnx_asr")           # предобработчики звука (*.onnx) внутри пакета
    + collect_data_files("vosk")
    + copy_metadata("onnx_asr")
    + [(os.path.join(ROOT, "rem", "clips", "*.wav"), "rem/clips")]   # готовые фразы голосом Рем
)
binaries = collect_dynamic_libs("vosk") + collect_dynamic_libs("pyaudiowpatch")   # libvosk.dll, portaudio

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("rem") + [
        "pystray._win32",                    # бэкенд трея выбирается динамически
        "win32com.client", "pythoncom",
        "pycaw.pycaw", "comtypes.stream",
        "pyaudiowpatch",                     # звук колонок (импорт внутри функции)
    ],
    excludes=["matplotlib", "scipy", "pandas", "torch", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Rem",
    icon=os.path.join(SPECPATH, "rem.ico"),
    console=False,                           # без чёрного окна
)
coll = COLLECT(exe, a.binaries, a.datas, name="Rem")
