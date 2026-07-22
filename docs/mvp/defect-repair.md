# Defect registry and bounded repair

The remote MVP keeps outcome-facing defect policy in
`src/open_storyline/mvp/defects.py`. Registry v1 covers codes that can enter a
job outcome, promotion decision, fallback ledger, retry decision, repair
decision, audit summary, public activity failure, or workspace presentation.
Unrelated operator-command, pagination, and internal activity-schema validation
codes remain explicitly inventoried outside the registry.

Each registered definition records its domain, default severity, public
visibility, repair strategy and phase, evidence requirements, safe fallback,
retry action, promotion class, and English/Spanish presentation. Unknown codes
fail closed, cannot become LLM-repairable, and remain visible as raw safe codes
for support and audit.

Outcome reports embed a bounded presentation snapshot next to each raw code.
The browser uses the Spanish registry title and description instead of deriving
labels by lowercasing codes. Historical `outcome_report.v1` documents remain
readable: readers derive current presentation metadata without rewriting the
stored document or changing its original grade and code.

Strict JSON Schema and the repair loop are introduced in later rollout stages.
Registry metadata never replaces detector-owned technical classification,
Pydantic/domain validation, FFmpeg preflight, post-render QA, or promotion
checks. A model may propose a corrected candidate only for an eligible code;
the backend always decides whether the defect was resolved.

Repairable composition geometry is detected before provider repair and FFmpeg
execution with the same crop-feasibility calculation used by the compositor.
The bounded finding records only normalized identifiers and numeric geometry;
it never stores frames or raw provider content. Composition configuration,
source integrity, executable validation, authentication, database, and unsafe
path failures remain deterministic fail-closed boundaries.
