from datetime import datetime, timezone

from sqlalchemy.dialects.mysql import LONGTEXT

from .extensions import db

# TEXT in MySQL caps at 64 KB; prompts and raw LLM output can exceed that.
LongText = db.Text().with_variant(LONGTEXT(), "mysql")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


class Query(db.Model):
    """One user question: the entities searched, the relation filter and the evidence collected."""

    __tablename__ = "queries"

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("queries.id"), nullable=True, index=True)
    entities = db.Column(db.JSON, nullable=False)
    entity_type = db.Column(db.String(16), nullable=False, default="auto")  # auto | gene | drug
    relation_types = db.Column(db.JSON, nullable=False)
    followup_question = db.Column(db.Text)
    # Normalised evidence bundle: {items, table, network, source_status, counts}
    evidence = db.Column(db.JSON)
    status = db.Column(db.String(16), nullable=False, default="pending")  # pending | complete | failed
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    reports = db.relationship(
        "Report", back_populates="query", order_by="Report.id", cascade="all, delete-orphan"
    )

    def to_dict(self, include_evidence=True):
        data = {
            "id": self.id,
            "parent_id": self.parent_id,
            "entities": self.entities,
            "entity_type": self.entity_type,
            "relation_types": self.relation_types,
            "followup_question": self.followup_question,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": iso(self.created_at),
            "reports": [r.to_dict() for r in self.reports],
        }
        if include_evidence:
            data["evidence"] = self.evidence
        return data


class PromptTemplate(db.Model):
    """Versioned prompt template. Editing creates a new version so old logs stay reproducible."""

    __tablename__ = "prompt_templates"
    __table_args__ = (db.UniqueConstraint("name", "version", name="uq_template_name_version"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    system_prompt = db.Column(LongText, nullable=False)
    user_template = db.Column(LongText, nullable=False)  # Jinja2 (sandboxed)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "system_prompt": self.system_prompt,
            "user_template": self.user_template,
            "notes": self.notes,
            "is_active": self.is_active,
            "created_at": iso(self.created_at),
        }


class Report(db.Model):
    """An LLM-written narrative for a query. One per model (primary + optional comparison)."""

    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey("queries.id"), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("prompt_templates.id"), nullable=True)
    provider = db.Column(db.String(32), nullable=False)  # openai | anthropic
    model = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="primary")  # primary | comparison
    content_md = db.Column(LongText)
    citations = db.Column(db.JSON)  # evidence IDs cited, in order of first use
    invalid_citations = db.Column(db.JSON)  # cited IDs that don't exist in the evidence
    # success | flagged (answered but failed a quality check) | failed (no answer)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    error_message = db.Column(db.Text)
    latency_ms = db.Column(db.Integer)
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    query = db.relationship("Query", back_populates="reports")
    template = db.relationship("PromptTemplate")
    feedback = db.relationship("Feedback", back_populates="report", cascade="all, delete-orphan")
    prompt_logs = db.relationship("PromptLog", back_populates="report", cascade="all, delete-orphan")

    def to_dict(self):
        ratings = [f.rating for f in self.feedback if f.rating]
        return {
            "id": self.id,
            "query_id": self.query_id,
            "template_id": self.template_id,
            "template_version": self.template.version if self.template else None,
            "provider": self.provider,
            "model": self.model,
            "role": self.role,
            "content_md": self.content_md,
            "citations": self.citations or [],
            "invalid_citations": self.invalid_citations or [],
            "status": self.status,
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": iso(self.created_at),
            "feedback_count": len(self.feedback),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        }


class PromptLog(db.Model):
    """Exact prompt sent and raw response received, for every LLM call (including failures)."""

    __tablename__ = "prompt_logs"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), nullable=True, index=True)
    query_id = db.Column(db.Integer, db.ForeignKey("queries.id"), nullable=True, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("prompt_templates.id"), nullable=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    model = db.Column(db.String(100), nullable=False)
    system_prompt = db.Column(LongText)
    user_prompt = db.Column(LongText)
    raw_response = db.Column(LongText)
    status = db.Column(db.String(16), nullable=False, index=True)
    error_message = db.Column(db.Text)
    latency_ms = db.Column(db.Integer)
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    report = db.relationship("Report", back_populates="prompt_logs")

    def to_dict(self, full=False):
        data = {
            "id": self.id,
            "report_id": self.report_id,
            "query_id": self.query_id,
            "template_id": self.template_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": iso(self.created_at),
        }
        if full:
            data.update(
                system_prompt=self.system_prompt,
                user_prompt=self.user_prompt,
                raw_response=self.raw_response,
            )
        return data


class Feedback(db.Model):
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), nullable=False, index=True)
    rating = db.Column(db.SmallInteger)  # 1-5
    comment = db.Column(db.Text)
    suggestion = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report", back_populates="feedback")

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "rating": self.rating,
            "comment": self.comment,
            "suggestion": self.suggestion,
            "created_at": iso(self.created_at),
        }
