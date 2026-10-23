# Opportunities, Phase 1

Job postings are separate from private briefs. Only explicitly approved form data
and the supplied public brand name appear publicly. Draft URLs return 404 even
for their owner. Published jobs remain public when applications close. A deadline
is inclusive in Asia/Kolkata; an expired open posting is effectively closed without
requiring a scheduler. Reopening validates dates again. Drafts need only a title;
publication requires project, output, budget and timing details. Optional taxonomies
express no preference when empty. Editing public content requires fresh approval.

All writes are owner-scoped, POST/CSRF protected, transactional and version-checked.
The migration only creates new tables, constraints and indexes. Do not run it against
production without deployment approval.

## Phase 2 boundaries

There is intentionally no application creation endpoint, creator discovery page,
review-state mutation, notification, contact exchange or AI button. Application
lifecycle and brand consideration are separate; **Considering is not contact consent**.
Implement a separate, explicit, revocable consent flow with privacy checks before
any future contact disclosure. Existing applicant messages are brand-private.

`applicant_candidates(brand, posting_id)` limits future ranking to active creator
accounts with submitted applications to that brand's posting; withdrawn applicants
are excluded. `matching_requirements` supplies the canonical taxonomy and requirement
fields used by `discovery.matching.score_creator`. Use that scorer through an explicit
requirements adapter, extending shared missing-field handling where necessary, instead
of copying weights or ranking the directory. Brief-specific audience/licensing inputs
must not be inferred from arbitrary job text.

`discovery.semantic` currently caches by Brief. Never reuse a job ID as a Brief ID
or write fake private briefs. Future job semantic insights/results need their own
posting-scoped cache with the job version, application membership, profile/portfolio
fingerprints, scorer version and expiry. Obtain explicit Gemini consent and preserve
the current local fallback. The applicant view currently displays “Not evaluated”;
no compatibility score or explanation is fabricated or stored in Phase 1.
