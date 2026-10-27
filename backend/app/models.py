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
    # Who asked (query history); NULL for anonymous searches and command-line runs
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    entities = db.Column(db.JSON, nullable=False)  # [gene]; older queries may hold several names
    entity_type = db.Column(db.String(16), nullable=False, default="gene")  # gene | drug | vcf
    relation_types = db.Column(db.JSON, nullable=False)
    followup_question = db.Column(db.Text)
    # The full evidence bundle as shown on the results page ({items, table, network, source_status, ...}). Its
    # drug–gene pairs are also stored relationally in query_interactions, and report citations in citations.
    evidence = db.Column(db.JSON)
    status = db.Column(db.String(16), nullable=False, default="pending")  # pending | complete | failed
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    reports = db.relationship(
        "Report", back_populates="query", order_by="Report.id", cascade="all, delete-orphan"
    )
    interactions = db.relationship(
        "QueryInteraction", back_populates="query", order_by="QueryInteraction.id", cascade="all, delete-orphan"
    )
    user = db.relationship("User", back_populates="queries")

    def to_dict(self, include_evidence=True):
        data = {
            "id": self.id,
            "parent_id": self.parent_id,
            "user_id": self.user_id,
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
    citation_rows = db.relationship("Citation", back_populates="report", order_by="Citation.position",
                                    cascade="all, delete-orphan")

    @property
    def citations(self):
        """Evidence IDs cited (and present in the evidence), in order of first use."""
        return [c.evidence_id for c in self.citation_rows if c.valid]

    @property
    def invalid_citations(self):
        """Cited IDs that were not in the prompt's evidence (a hallucinated reference)."""
        return [c.evidence_id for c in self.citation_rows if not c.valid]

    def set_citations(self, used, invalid, items):
        """used: every cited ID in order of first use; items: {evidence ID: evidence item} from the query."""
        self.citation_rows = [Citation.from_item(i, eid, eid not in invalid, items.get(eid))
                              for i, eid in enumerate(used, 1)]

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
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    rating = db.Column(db.SmallInteger)  # 1-5
    comment = db.Column(db.Text)
    suggestion = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report", back_populates="feedback")

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "user_id": self.user_id,
            "rating": self.rating,
            "comment": self.comment,
            "suggestion": self.suggestion,
            "created_at": iso(self.created_at),
        }


class User(db.Model):
    """A user account for the explorer. Only the password hash is stored."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True)  # stored lower-cased
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    queries = db.relationship("Query", back_populates="user", passive_deletes=True)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "is_admin": self.is_admin,
            "created_at": iso(self.created_at),
            "last_login_at": iso(self.last_login_at),
        }


class Citation(db.Model):
    """One evidence item cited by a report, in order of first use. valid = the ID was in the prompt's evidence."""

    __tablename__ = "citations"
    __table_args__ = (db.UniqueConstraint("report_id", "evidence_id", name="uq_citation_report_evidence"),)

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
    position = db.Column(db.SmallInteger, nullable=False)
    evidence_id = db.Column(db.String(16), nullable=False)  # E3, as written in the report
    valid = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(32))  # PubTator, CIViC, DGIdb, ChEMBL, CPIC, PharmGKB, ...
    kind = db.Column(db.String(32))  # publication, civic, interaction, mechanism, guideline, ...
    pmid = db.Column(db.Integer, index=True)  # the PubMed ID behind it, when it has exactly one
    url = db.Column(db.String(500))

    report = db.relationship("Report", back_populates="citation_rows")

    @classmethod
    def from_item(cls, position, evidence_id, valid, item):
        item = item or {}
        pmid = item.get("pmid") or (item["pmids"][0] if isinstance(item.get("pmids"), list) and len(item["pmids"]) == 1
                                    else None)
        return cls(position=position, evidence_id=evidence_id, valid=valid, source=item.get("source"),
                   kind=item.get("kind"), pmid=int(pmid) if str(pmid or "").isdigit() else None,
                   url=(item.get("url") or "")[:500] or None)


class QueryInteraction(db.Model):
    """A drug–gene pair a query found, one row per evidence item: the relational view of the DGI table.

    hgnc_id / chembl_id refer to genes / drugs. They are indexed but not declared as foreign keys, because
    import-data replaces those reference tables wholesale; the IDs themselves are stable across releases."""

    __tablename__ = "query_interactions"

    id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(db.Integer, db.ForeignKey("queries.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id = db.Column(db.String(16), nullable=False)
    source = db.Column(db.String(32), nullable=False)  # DGIdb, ChEMBL, CPIC, PharmGKB, CIViC, OncoKB
    gene_symbol = db.Column(db.String(64), nullable=False, index=True)
    hgnc_id = db.Column(db.String(20), index=True)
    drug_name = db.Column(db.String(255), nullable=False, index=True)
    chembl_id = db.Column(db.String(20), index=True)
    relation = db.Column(db.String(255))  # interaction type, action type or clinical significance
    level = db.Column(db.String(16))  # CPIC / PharmGKB / CIViC / OncoKB evidence level
    score = db.Column(db.Float)  # DGIdb interaction score

    query = db.relationship("Query", back_populates="interactions")

    def to_dict(self):
        return {k: getattr(self, k) for k in ("evidence_id", "source", "gene_symbol", "hgnc_id", "drug_name",
                                              "chembl_id", "relation", "level", "score")}
