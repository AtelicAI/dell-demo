import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data import load_facility
from app.retrieval import Documents

if __name__ == "__main__":
    docs = Documents()
    try:
        print(f"Indexed {docs.seed(load_facility())} canonical synthetic documents into isolated Qdrant")
    finally:
        docs.close()
