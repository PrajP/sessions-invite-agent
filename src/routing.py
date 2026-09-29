"""Strategic Model Routing for Payment Reconciliation and Session Enrollment Agent.

Directs requests to the most optimal model based on task complexity:
- High-throughput multimodal OCR, image extraction, and PII masking -> gemini-2.5-flash
- Complex multi-factor financial reconciliation, dispute reasoning, and planning -> gemini-2.5-pro
"""

from enum import Enum
from typing import Any, Dict
from pydantic import BaseModel

from src.logging_tracer import log_agent_lifecycle


class TaskType(str, Enum):
    """Categorization of agent operations for model routing."""

    OCR_EXTRACTION = "ocr_extraction"
    PII_MASKING = "pii_masking"
    TRIAGE_VALIDATION = "triage_validation"
    RECONCILIATION = "reconciliation"
    FINANCIAL_DISPUTE = "financial_dispute"
    COMPLEX_PLANNING = "complex_planning"


class ModelRouteConfig(BaseModel):
    """Configuration associated with a model route."""

    model_name: str
    temperature: float
    max_output_tokens: int
    rationale: str


# Model configuration map enforcing strategic routing
ROUTING_TABLE: Dict[TaskType, ModelRouteConfig] = {
    TaskType.OCR_EXTRACTION: ModelRouteConfig(
        model_name="gemini-2.5-flash",
        temperature=0.0,
        max_output_tokens=1024,
        rationale="High-speed vision OCR with strict deterministic output schema.",
    ),
    TaskType.PII_MASKING: ModelRouteConfig(
        model_name="gemini-2.5-flash",
        temperature=0.0,
        max_output_tokens=512,
        rationale="Low-latency token stream for sanitization and redacting identifiers.",
    ),
    TaskType.TRIAGE_VALIDATION: ModelRouteConfig(
        model_name="gemini-2.5-flash",
        temperature=0.1,
        max_output_tokens=512,
        rationale="Rapid field presence verification and channel detection.",
    ),
    TaskType.RECONCILIATION: ModelRouteConfig(
        model_name="gemini-2.5-pro",
        temperature=0.0,
        max_output_tokens=2048,
        rationale="Deep multi-attribute cross-referencing and mathematical comparison.",
    ),
    TaskType.FINANCIAL_DISPUTE: ModelRouteConfig(
        model_name="gemini-2.5-pro",
        temperature=0.2,
        max_output_tokens=2048,
        rationale="High-reasoning model required for third-party payee and ambiguous UTR evaluation.",
    ),
    TaskType.COMPLEX_PLANNING: ModelRouteConfig(
        model_name="gemini-2.5-pro",
        temperature=0.2,
        max_output_tokens=4096,
        rationale="Multi-agent step coordination and policy alignment.",
    ),
}


def route_model_for_task(task_type: TaskType) -> ModelRouteConfig:
    """Selects and returns the optimal model configuration for a given agent task.

    Args:
        task_type: TaskType enum describing the operation.

    Returns:
        ModelRouteConfig containing model_name, temperature, tokens, and rationale.
    """
    route = ROUTING_TABLE.get(
        task_type,
        ModelRouteConfig(
            model_name="gemini-2.5-flash",
            temperature=0.1,
            max_output_tokens=1024,
            rationale="Default fallback to fast flash model.",
        ),
    )

    log_agent_lifecycle(
        intent=f"Route model for task {task_type.value}",
        outcome=f"ROUTED_TO_{route.model_name}",
        metadata={"task": task_type.value, "model": route.model_name, "rationale": route.rationale},
    )

    return route
