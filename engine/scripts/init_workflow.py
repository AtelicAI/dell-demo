"""Initialize the owned workflow schema and provision the local demo engineer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.workflow import WorkflowStore
import os

if __name__ == '__main__':
    store = WorkflowStore(os.getenv('WORKFLOW_ADMIN_DATABASE_URL') or os.environ['WORKFLOW_DATABASE_URL'])
    store.initialize()
    store.configure_engineer()
    print('Workflow schema initialized; local engineer credential hash provisioned.')
