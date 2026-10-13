"""Pydantic models for the Enterprise Schema Integrator."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CanonicalCustomer(BaseModel):
    """Target database schema for a customer record."""

    customer_id: str = Field(..., description="Unique customer identifier")
    full_name: str = Field(..., description="Customer's full name")
    email: str = Field(..., description="Customer's email address")
    signup_date: str = Field(..., description="Signup date, ISO 8601 (YYYY-MM-DD)")
    lifetime_value: float = Field(..., description="Total customer lifetime value")
    account_status: Literal["active", "inactive", "churned"] = Field(
        ..., description="Current account status"
    )


class FieldMapping(BaseModel):
    """Mapping from a source column to a CanonicalCustomer field."""

    source_field_name: str = Field(..., description="Column name in the source data")
    target_field_name: str = Field(..., description="Field name in CanonicalCustomer")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Mapping confidence from 0 to 1"
    )
    reasoning: str = Field(..., description="Why this mapping was chosen")


class DataIssue(BaseModel):
    """A data quality problem found in the source data."""

    row_index: Optional[int] = Field(
        None, description="Affected row index, or None for column-level issues"
    )
    field_name: str = Field(..., description="Affected field")
    issue_type: str = Field(
        ..., description="Issue category, e.g. missing_value, invalid_format, duplicate"
    )
    description: str = Field(..., description="Human-readable description of the issue")
    severity: Literal["CRITICAL", "WARNING", "INFO"] = Field(
        ..., description="Issue severity"
    )


class ReadinessReport(BaseModel):
    """Migration readiness assessment for a source dataset."""

    data_health_score: int = Field(
        ..., ge=0, le=100, description="Overall data health score from 0 to 100"
    )
    total_records_processed: int = Field(..., ge=0, description="Number of records analyzed")
    mapped_fields: List[FieldMapping] = Field(
        default_factory=list, description="Source-to-target field mappings"
    )
    data_issues: List[DataIssue] = Field(
        default_factory=list, description="Data quality issues found"
    )
    executive_summary: str = Field(..., description="Summary for business stakeholders")
    key_action_items: List[str] = Field(
        default_factory=list, description="Recommended next steps"
    )
