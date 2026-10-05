from core.orchestrator import WorkflowEngine
from core.tasks import TaskManager

workflow_engine = WorkflowEngine()
task_manager = TaskManager(workflow_engine)


def get_workflow_engine() -> WorkflowEngine:
    return workflow_engine
