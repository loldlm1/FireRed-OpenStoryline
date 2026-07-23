# Agentic Video Review Evaluation

The rendered critic and candidate comparator are evaluated as bounded editorial
assistants, not as sources of truth about virality or retention. Reviewers score
the original and repaired candidates independently before seeing the model
selection.

## Rubric

Score each dimension from 1 (poor) to 5 (strong), or mark `tie`/`uncertain`:

- visual clarity and framing;
- caption readability and timing;
- pacing and rhythm;
- narrative coherence and emphasis;
- effect appropriateness and restraint;
- instruction/creative-intent fidelity;
- overall preference.

Reviewers must cite supplied evidence timestamps and may not use technical
playability to override the deterministic gate. A candidate with a technical
block is ineligible for creative preference. Ties and uncertainty remain valid
outcomes.

## Promotion evidence

Aggregate results are recorded outside Git for the authorized private fixture
set. The minimum report contains fixture count, per-dimension mean, preference
rate, tie rate, uncertainty rate, new-defect rate, and exact image/schema/prompt
lineage. No single model score is ground truth, and a narrow canary does not
support a broad quality claim.
