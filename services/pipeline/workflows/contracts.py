"""Workflow input/output shapes only -- deliberately importing nothing else.

kyc_case.py (the workflow) and services/intake/temporal.py (the starter) both need
KycCaseInput, but activities.py imports boto3/easyocr/google-genai at module level (easyocr
alone pulls in torch). Importing kyc_case.py directly from intake would transitively import
all of that into the intake process too, for code that never uses any of it -- confirmed by
watching intake's startup balloon from near-instant to ~12s once services/intake/temporal.py
started importing the workflow class directly. Splitting the plain data contracts out here
lets intake start workflows by string type name (Client.start_workflow accepts either) without
ever importing activities.py.
"""

from dataclasses import dataclass

# Temporal's default registered workflow type name is the class name (KycCaseWorkflow, in
# kyc_case.py) -- named here so callers that only import this lightweight module (not
# kyc_case.py itself) can still start it by string type without a magic string duplicated
# across files.
KYC_CASE_WORKFLOW_NAME = "KycCaseWorkflow"


@dataclass
class KycCaseInput:
    tenant_id: str
    case_id: str


@dataclass
class KycCaseResult:
    status: str
    confidence: float | None = None
    method: str | None = None
    reason: str | None = None
    face_match_score: float | None = None
    sanctions_hit_count: int | None = None
