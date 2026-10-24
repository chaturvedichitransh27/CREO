# CREO Verified Reputation

## Eligibility and provenance

The existing Engagement and EngagementEvent workflow is the source of truth.
An eligible engagement is completed, has an HTTPS delivery URL, belongs to a
non-demo Creator account, and is not associated with a demo posting. Its ordered
event history must contain acceptance, work start, and delivery by that creator,
followed by completion by the source brief/posting's owning brand. Any declined
or cancelled event disqualifies it. Historic records without this evidence do not
receive inferred completion credit. No completion events or ratings are backfilled.

Submission requires the authenticated, active owning Brand account. Staff and
superusers are explicitly denied, including when they own the engagement.
The centralized submit_review service resolves ownership before locking the
engagement within a transaction and rechecking eligibility. PostgreSQL locks only
the engagement row, avoiding nullable outer-join locking. OneToOne uniqueness
provides the final duplicate safeguard. Participants are derived from the
engagement, never supplied by the browser.

## Scores and display

All five integer scores (1 through 5) are required. Written feedback is optional,
trimmed, limited to 1,000 characters and escaped when rendered. Each review is the
arithmetic mean of its five factors. Creator averages give every eligible review
equal weight, using integer aggregates and Decimal arithmetic, rounded to one
decimal with ROUND_HALF_UP. No Gemini calls or matching-score changes are involved.

Completed projects count eligible engagements independently of submitted reviews.
Both metrics are derived, not editable counters. Invalidated engagement evidence
excludes the associated review from public aggregates without deleting it.
Profiles show newest reviews first, six per page. List querysets annotate aggregates
to avoid one reputation query per creator card.

Public review labels use explicitly published opportunity brand names and titles.
Private briefs use "Verified Brand" and "Private CREO project". Private engagement
messages, account contacts and brief snapshots are not copied into review cards.
The form warns brands that their voluntary feedback is public and must not contain
confidential information.

## Immutability and security boundary

There are no product edit/delete endpoints or privileged submission overrides.
Model save/delete guards reject ordinary changes, and Django Admin disables add,
change and delete, while permitting inspection. These protections do not prevent
someone with unrestricted database/server/deployment access from changing records
using SQL, bulk ORM operations or replacement code. This is an application-level
provenance system, not a tamper-proof ledger.

## Workflow and rollout

After the normal creator acceptance, start, delivery (and any revisions), the brand
marks the engagement completed at /engagements/<id>/. "Rate This Creator" opens
/engagements/<id>/review/. The brand submits all five scores using "Submit verified
review". The submitted review remains viewable there; public reputation appears at
/creators/<id>/#verified-reputation and existing creator evaluation interfaces.

Migration engagements.0004_creatorreview_and_more adds the review table, score
constraints, unique engagement relationship and an event lookup index. Apply it
through the normal approved deployment process before serving the new code.
Existing data and migration history are preserved. No reputation seed command is
provided, and no production data is required for verification.

## Verification

The full isolated SQLite test suite ran 271 tests: 268 passed, three PostgreSQL-only
tests skipped. System checks, migration drift checks and git diff checks passed.
The PostgreSQL-only review concurrency test must be run on an isolated PostgreSQL
database before claiming database-specific race verification; SQLite does not
prove PostgreSQL row-lock behavior.

Browser verification used a separate disposable local database and exercised the
real invitation, acceptance, start, submission, revision, resubmission, completion
and review workflow. It checked 1-5 selection, keyboard controls, invalid input,
duplicate requests, 4.6 arithmetic, private labels, unrated profiles, discovery,
320-1440 pixel layouts and console errors. No production reviews were created.
