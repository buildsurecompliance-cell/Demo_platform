# BuildSure Compliance Build Guide

This document is the official architecture and product guide for BuildSure Compliance. It exists to keep every future change aligned with the same product vision, domain model, and engineering principles.

## 1. Product Vision

BuildSure Compliance is not an ERP.

BuildSure Compliance is not Procore.

BuildSure Compliance is not construction management software.

BuildSure Compliance is a specialized B2B SaaS platform focused exclusively on Document Compliance for General Contractors.

The central product question is:

> Can this subcontractor work today on this project?

Every future feature must help answer that question more simply, more accurately, or more trustworthily.

If a feature does not improve the ability to determine whether a subcontractor can work on a specific project today, it should be questioned before it is built.

## 2. Product Principles

- Simplicity above feature volume.
- AI never makes final compliance decisions.
- AI provides evidence.
- Readiness makes decisions.
- Do not duplicate business logic.
- Prefer one source of truth for each decision.
- Security before convenience.
- UX should reduce clicks and uncertainty.
- Preserve traceability from decision to evidence.
- Be conservative when compliance data conflicts.
- Keep product scope narrow and valuable.
- Build for General Contractors first.

## 3. Architecture

```text
Organization
  |
  v
Upload
  |
  v
Document Intelligence
  |
  v
Validators
  |
  v
Compliance Evidence
  |
  v
Readiness Engine
  |
  v
Dashboard
  |
  v
User
```

### Upload

Uploads are the entry point for compliance documents. Upload flows are responsible for securely accepting files, associating them with the correct entity, preserving versioning, and making the physical document available to downstream services.

Upload code should not decide compliance status.

### Document Intelligence

Document Intelligence extracts structured information from uploaded documents using OCR and AI-assisted parsing.

This layer may identify document type, dates, coverage limits, carriers, policy numbers, and other useful structured fields.

Document Intelligence does not decide whether a subcontractor is ready.

### Validators

Validators inspect extracted data and determine whether the extraction is usable for a specific compliance purpose.

Validators are responsible for rejecting missing, malformed, low-confidence, unsupported, incomplete, or inconsistent extraction results.

Validators do not make final readiness decisions.

### Compliance Evidence

Compliance Evidence is the controlled internal representation of validated or rejected information produced from documents.

Readiness must consume Compliance Evidence, not raw AI JSON.

Evidence should include source, confidence, validation state, value, and document reference. Evidence allows the system to explain why a decision was made without allowing the AI to become the decision maker.

### Readiness Engine

The Readiness Engine is the only layer that decides READY, PENDING, or BLOCKED.

It combines validated evidence, manual fields, project requirements, dates, coverage, and conservative conflict handling into a single decision.

### Dashboard

The Dashboard presents readiness outcomes and supporting context to users.

The Dashboard should not recalculate readiness or inspect raw AI output. It should display decisions produced by the Readiness Engine.

### User

The user needs a simple answer, not a pile of documents:

> Can this subcontractor work today on this project?

The product should make the answer clear, explainable, and actionable.

## 4. Core Domain

### User

The account owner or authenticated actor using the platform.

Users belong to one or more Organizations through memberships.

Users do not own projects, subcontractors, or documents directly in the product domain. User identity is still important for authentication and audit trails, such as who uploaded a document or sent an invitation.

### Organization

The customer company using BuildSure Compliance.

Organization is the tenant boundary. Projects, subcontractors, document access, dashboards, and compliance workflows are scoped to the active Organization.

Commercial plans may allow multiple users in the same Organization, but the data belongs to the company, not to an individual user account.

### OrganizationMembership

The relationship between a User and an Organization.

Membership defines whether a user can access the Organization and which simple role they have.

V1 roles are:

- OWNER
- ADMIN
- MEMBER

Roles must be centralized in code and not repeated as free-form strings across routes or templates.

### Project

A construction project owned by an Organization.

Projects define the context in which subcontractors are evaluated. Project-specific requirements can affect readiness, including required coverage or future Compliance Profiles.

### Subcontractor

A company or trade partner that may work on projects.

Subcontractors belong to an Organization. They hold general compliance information and documents, but readiness is always evaluated in the context of a project.

### ProjectSubcontractor

The relationship between a Project and a Subcontractor.

This is the central operational link for readiness. The product question is answered for this relationship, not for a subcontractor globally.

### Document

A stored uploaded file with metadata, ownership, entity association, versioning, and optional AI analysis output.

Documents are evidence sources. They are not compliance decisions.

The `uploaded_by` user reference is an audit field. It should not be used as the ownership boundary.

### ComplianceEvidence

An internal, non-database model that represents information derived from documents after validation.

ComplianceEvidence protects the Readiness Engine from raw AI output and gives the system a controlled way to reason about extracted facts.

### Readiness

The decision state for a ProjectSubcontractor.

Readiness produces a status, reasons, severity, and checked timestamp. It is the single decision authority for whether a subcontractor can work on a project today.

## 5. Readiness

Readiness has three official statuses:

### READY

The subcontractor has no blocking or pending compliance issues for the project based on the currently available data.

READY means the system has enough validated or acceptable information to allow work from a compliance perspective.

### PENDING

The subcontractor is not blocked, but compliance information needs attention, review, completion, or time-sensitive follow-up.

Examples include low-confidence AI evidence, failed validation, unreadable documents, partial processing, or documents expiring soon.

### BLOCKED

The subcontractor cannot work on the project from a compliance perspective.

Examples include missing required COI information, expired COI, or insufficient coverage when project requirements and usable coverage values exist.

### Priority

Readiness priority is:

```text
BLOCKED > PENDING > READY
```

If any blocking reason exists, the final status is BLOCKED.

If no blocking reason exists but at least one warning or pending reason exists, the final status is PENDING.

Only when no blocking or pending reasons exist can the final status be READY.

Only the Readiness Engine may decide READY, PENDING, or BLOCKED.

Routes, templates, dashboards, AI services, validators, and models must not create separate readiness engines.

## 6. AI

BuildSure may use AI to accelerate document understanding, but AI is not the compliance authority.

### OpenAI

OpenAI can assist with extraction, classification, summarization, and structured parsing.

OpenAI output must be treated as input to validation, not as a final decision.

### OCR

OCR converts document content into machine-readable text.

OCR may fail, produce incomplete text, or misread fields. OCR output must be validated before it influences readiness.

### Validators

Validators determine whether extracted AI data is usable.

Validators should reject low-confidence, malformed, incomplete, contradictory, unsupported, or missing data.

### Evidence

Validated AI output becomes Compliance Evidence.

Rejected AI output can still become evidence of uncertainty, causing PENDING rather than READY.

AI never releases a subcontractor to work.

The Readiness Engine releases, pauses, or blocks based on evidence and rules.

## 6.5 Organization and Team Access

BuildSure uses Organization-level tenancy.

The active Organization determines which projects, subcontractors, documents, dashboard metrics, reminders, analysis actions, and team records a user can access.

Users in the same Organization share the same compliance workspace. Users in different Organizations must not see each other's projects, subcontractors, documents, or membership records.

Role semantics:

- OWNER has full access to the Organization and can manage members.
- ADMIN can manage operational data and invite members.
- MEMBER can use normal compliance workflows but cannot manage team access.

V1 does not include project-level permissions, departments, crews, billing seats, or complex access policies.

Team invitations are token-based:

- store only a hash of the token;
- expire invitations;
- use invitations once;
- require the accepting user's email to match the invitation;
- do not reveal whether an email has an account outside the Organization.

Organization access must be checked before storage access, AI analysis, document download, delete, reminders, or any mutable action.

In V1, the active Organization is resolved from the session only after
verifying that the current user has a membership in that Organization. Invalid
or cross-Organization session values are ignored and replaced with the first
membership in deterministic order. There is no visual multi-Organization
selector in this sprint.

`Project.organization_id` and `Subcontractor.organization_id` are required and
are the only operational tenancy boundary for those records.

`Project.user_id` and `Subcontractor.user_id` may remain temporarily as
legacy/audit context for who created or originally owned records before
Organization tenancy. They must not authorize access, filter tenant data, or
act as a fallback when `organization_id` is missing. A record without
`organization_id` is invalid for operational use and must be corrected before
deployment migration completes.

Future work may remove the legacy `user_id` columns in a separate migration
after audit needs and historical references are reviewed.

## 7. Compliance Profiles

Compliance Profiles are the next major product capability.

A Compliance Profile will define the required document and rule set for a project, trade, client, jurisdiction, or risk category.

The expected architecture is:

```text
Project
  |
  v
Compliance Profile
  |
  v
Required Documents and Rules
  |
  v
Document Intelligence and Validators
  |
  v
Compliance Evidence
  |
  v
Readiness Engine
```

Compliance Profiles should not create a second decision system.

They should feed requirements into the existing Readiness Engine.

Examples of future profile rules:

- Required document types.
- Minimum coverage by trade.
- Required endorsements.
- Waiver requirements.
- Expiration windows.
- Jurisdiction-specific compliance rules.
- Client-specific requirements.

No Compliance Profiles code should be implemented until the domain model and rule boundaries are intentionally designed.

## 8. AI Compliance Officer

The AI Compliance Officer is a future assistant-like layer that helps users understand, review, and act on compliance issues.

It may eventually:

- Explain why a subcontractor is PENDING or BLOCKED.
- Suggest what document is missing.
- Summarize evidence.
- Draft requests to subcontractors.
- Highlight conflicts between manual data and document evidence.
- Help a user prioritize compliance work.

The AI Compliance Officer must not bypass the Readiness Engine.

It may explain decisions, but it must not invent or replace them.

## 9. Code Standards

- Services concentrate business rules.
- Routes coordinate requests, authorization, persistence, and responses.
- Templates display state and should not contain business logic.
- Models represent domain entities and relationships, but should not concentrate complex rules.
- Do not duplicate readiness, storage, validation, or evidence logic.
- Keep AI prompts, extraction, validation, evidence, and readiness as separate responsibilities.
- Prefer small focused helpers over repeated inline logic.
- Preserve ownership checks before accessing user data or files.
- Validate backend inputs even when templates provide constrained options.
- Keep tests close to business-critical behavior.

## 10. Never Turn BuildSure Into Procore

BuildSure Compliance must not become a broad construction management platform.

Do not add:

- Do not add Estimating.
- Do not add Scheduling.
- Do not add Pay Apps.
- Do not add RFIs.
- Do not add Submittals.
- Do not add Cost Management.
- Do not add Accounting.
- Do not add ERP.

Those products already exist.

BuildSure wins by staying focused on compliance.

The product should be excellent at answering:

> Can this subcontractor work today on this project?

## 11. Roadmap

### DONE

- Document Storage
- AI Extraction
- Validators
- Evidence
- Readiness

### NEXT

- Compliance Profiles
- AI Compliance Officer
- Executive Dashboard
- Analytics
- Audit Trail

## 12. Philosophy

The purpose of BuildSure is not to store documents.

Its purpose is to make compliance decisions simple, trustworthy, and immediate.
