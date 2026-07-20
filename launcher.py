import subprocess
import sys
import os
from pathlib import Path

launcher_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
repo_dir = launcher_dir.parent
os.chdir(str(repo_dir))

subprocess.run(["git", "pull", "--ff-only"], capture_output=True, shell=True)

gui_script = repo_dir / "fh6_gui.py"
subprocess.Popen(["pythonw", str(gui_script)], cwd=str(repo_dir))
