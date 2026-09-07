"""Tests for Phase 3 workflow automation engine."""

from src.workflow import WorkflowEngine, EventBus, StateMachine, WorkflowTask, WorkflowRun, WorkflowInstance
from src.workflow.models import WorkflowTask as WTask, WorkflowRun as WRun, WorkflowInstance as WInstance
from src.workflow.templates import get_workflow, SALES_ONBOARDING_WORKFLOW, PROJECT_KICKOFF_WORKFLOW, ERROR_HANDLING_WORKFLOW


def test_workflow_engine_initialization():
    """Test WorkflowEngine can be initialized."""
    engine = WorkflowEngine()
    assert engine is not None
    assert engine.event_bus is not None


def test_event_bus():
    """Test EventBus subscribe/publish functionality."""
    bus = EventBus()
    results = []

    def handler(event):
        results.append(event)

    bus.subscribe("test.event", handler)
    bus.publish({"type": "test.event", "data": "hello"})
    assert len(results) == 1
    assert results[0]["data"] == "hello"


def test_state_machine():
    """Test StateMachine transitions."""
    sm = StateMachine(initial_state="pending")
    assert sm.state == "pending"
    
    # Test valid transition
    assert sm.transition("running") == True
    assert sm.state == "running"
    
    # Test invalid transition
    assert sm.transition("pending") == False  # cannot go back from running to pending in our schema
    
    # Test completing
    assert sm.transition("completed") == True
    assert sm.state == "completed"


def test_workflow_task():
    """Test WorkflowTask creation and metadata."""
    task = WorkflowTask(title="Test Task", description="A test task")
    assert task.title == "Test Task"
    assert task.worker == "worker"
    assert task.status == "pending"
    assert task.id is not None
    
    # Test with metadata
    task2 = WorkflowTask(title="Task 2", metadata={"key": "value"})
    assert task2.metadata["key"] == "value"


def test_workflow_run():
    """Test WorkflowRun creation."""
    run = WorkflowRun(workflow_name="test_workflow", status="pending")
    assert run.workflow_name == "test_workflow"
    assert run.status == "pending"
    assert run.id is not None


def test_workflow_instance():
    """Test WorkflowInstance creation."""
    instance = WorkflowInstance(name="sales_onboarding", current_step=0, total_steps=4)
    assert instance.name == "sales_onboarding"
    assert instance.current_step == 0
    assert instance.total_steps == 4


def test_execute_task():
    """Test execute_task basic functionality."""
    task = WorkflowTask(title="Test Task")
    engine = WorkflowEngine()
    result = engine.execute_task(task)
    
    assert result.success == True
    assert result.output["task_id"] is not None
    assert task.status == "completed"


def test_execute_workflow():
    """Test execute_workflow basic functionality."""
    task = WorkflowTask(title="Test Workflow Task")
    workflow = {"steps": [{"name": "step1"}, {"name": "step2"}, {"name": "step3"}]}
    
    result = WorkflowEngine().execute_workflow(workflow, task)
    
    assert result["task_status"] == "completed"
    assert result["metadata"]["security_status"] == "passed"


def test_get_workflow():
    """Test get_workflow function."""
    workflow = get_workflow("sales_onboarding")
    assert "steps" in workflow
    assert workflow["description"] == "Sales team onboarding workflow"
    
    # Test default
    default = get_workflow()
    assert "steps" in default


def test_sales_onboarding_workflow():
    """Test sales onboarding workflow template."""
    workflow = SALES_ONBOARDING_WORKFLOW
    assert "steps" in workflow
    assert len(workflow["steps"]) == 4
    assert workflow["description"] == "Sales team onboarding workflow"


def test_project_kickoff_workflow():
    """Test project kickoff workflow template."""
    workflow = PROJECT_KICKOFF_WORKFLOW
    assert "steps" in workflow
    assert len(workflow["steps"]) == 4
    assert workflow["description"] == "Project kickoff workflow"


def test_error_handling_workflow():
    """Test error handling workflow template."""
    workflow = ERROR_HANDLING_WORKFLOW
    assert "steps" in workflow
    assert len(workflow["steps"]) == 5
    assert workflow["description"] == "Error handling and remediation workflow"