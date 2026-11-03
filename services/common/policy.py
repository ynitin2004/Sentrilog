"""The per-tenant risk policy document (improvements.md P-3, roadmap Phase 13b).

Before Phase 13 the risk rule was three constants in services/pipeline/risk_scoring.py, the same
for every tenant. A policy makes those a tenant's choice, versioned: each change is a new row in
risk_policies, never an edit, and every case records the version it was created under, so a
decision can always be explained by the rules in force when the case arrived (STUDY.md, Phase
13).

What a policy cannot do is as deliberate as what it can:

- No action is ever "reject". A case is never auto-rejected (D11); every rule here can only
  send a case to a human. There is no field that could express anything else.
- The hard stops are not configurable. A sanctions hit and an undetectable face always go to
  review, whatever the policy says (RULES.md: a hard red flag always goes to a human).

Shared by services/intake (validating what an admin submits) and services/pipeline (evaluating
it), hence services/common.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Version 0 is the built-in policy every tenant has until it writes one: exactly the three
# constants risk_scoring.py held before Phase 13, so cases created before policies existed are
# explained by it too.
BUILT_IN_VERSION = 0


class AutoClear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_combined: float = Field(gt=0.0, le=1.0)
    face_match_weight: float = Field(ge=0.0, le=1.0)
    extraction_weight: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "AutoClear":
        if abs(self.face_match_weight + self.extraction_weight - 1.0) > 1e-9:
            raise ValueError("face_match_weight + extraction_weight must equal 1.0")
        return self


class DocumentRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # An expired document goes to review. Switchable only so the built-in policy can stay
    # exactly what the code did before policies existed; a tenant policy defaults to on.
    review_expired: bool = True
    expiring_within_days: int | None = Field(default=None, ge=1, le=3650)


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_clear: AutoClear
    document_rules: DocumentRules = DocumentRules()
    # Below this face-match score a case goes to review even if the weighted score clears it.
    min_face_match: float | None = Field(default=None, ge=0.0, le=1.0)
    # ISO 3166 alpha-3 nationalities, as the MRZ prints them, that always go to review.
    review_nationalities: list[str] = Field(default_factory=list, max_length=300)

    @field_validator("review_nationalities")
    @classmethod
    def _alpha3(cls, values: list[str]) -> list[str]:
        normalized = sorted({v.strip().upper() for v in values})
        for v in normalized:
            if len(v) != 3 or not v.isalpha():
                raise ValueError(f"not an ISO 3166 alpha-3 code: {v!r}")
        return normalized


BUILT_IN_POLICY = RiskPolicy(
    auto_clear=AutoClear(min_combined=0.85, face_match_weight=0.6, extraction_weight=0.4),
    document_rules=DocumentRules(review_expired=False),
)
