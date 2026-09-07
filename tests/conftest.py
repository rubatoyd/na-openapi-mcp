"""테스트가 설치본이 아닌 `src/` 소스를 직접 임포트하도록 경로 주입."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))   # samples.py 직접 import
