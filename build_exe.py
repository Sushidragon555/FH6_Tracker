"""Build FH6 Tracker as a Windows .exe using PyInstaller.

Usage:
    python build_exe.py

Output:
    dist/FH6_Tracker/  (folder with .exe and data files)
"""
import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
APP_NAME = "FH6_Tracker"

DATA_FILES = [
    "auto_log.py",
    "car_lookup.py",
    "import_owned_cars.py",
    "fh6_id_reference.json",
    "fh6_master_list.json",
    "owned_cars.json",
    "requirements.txt",
]

EXTRA_DIRS = ["races", "tests"]


def build():
    # Clean previous builds
    for d in [BUILD_DIR, os.path.join(DIST_DIR, APP_NAME)]:
        if os.path.exists(d):
            shutil.rmtree(d)

    # Build the spec
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--hidden-import", "car_lookup",
        "--hidden-import", "auto_log",
    ]

    # Add data files (auto_log.py etc. next to the exe)
    for df in DATA_FILES:
        src = os.path.join(PROJECT_DIR, df)
        if os.path.exists(src):
            cmd.extend(["--add-data", f"{src};."])

    # Add extra directories
    for d in EXTRA_DIRS:
        src = os.path.join(PROJECT_DIR, d)
        if os.path.isdir(src):
            cmd.extend(["--add-data", f"{src};{d}"])

    cmd.append(os.path.join(PROJECT_DIR, "run.py"))

    print("Building FH6 Tracker .exe ...")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        print("Build failed!")
        sys.exit(1)

    print(f"\nBuild complete! Output in: {os.path.join(DIST_DIR, APP_NAME)}")
    print(f"  -> {APP_NAME}.exe")

    # Create races/ directory next to the exe for user race recordings
    races_dst = os.path.join(DIST_DIR, APP_NAME, "races")
    if not os.path.isdir(races_dst):
        os.makedirs(races_dst, exist_ok=True)

    print(f"\nTo distribute: zip the '{APP_NAME}' folder and share it.")


if __name__ == "__main__":
    build()
