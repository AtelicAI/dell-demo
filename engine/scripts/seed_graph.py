import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.data import load_documents,load_facility,load_json
from app.graph import Graph
graph=Graph()
try:
    facility=load_facility();graph.ingest(facility,load_json('maintenance.json'),load_documents(),load_json('scenarios/scenarios.json'),load_json('scenarios/detections.json'))
    print(graph.context('XV-2104'))
finally:graph.close()
