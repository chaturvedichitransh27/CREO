# CREO Opportunities — marketplace integration

Published open jobs appear automatically in `/opportunities/`; draft detail pages
remain inaccessible publicly. New brands need no seeded account or separate profile.
The public brand overview contains only published posting data and the explicit
public brand name. All private routes are owner/participant scoped and uncached.

## Applications and notifications

Creators apply through a CSRF-protected form; the database enforces one application
per creator/posting. The posting row is locked while checking deadline and status
and creating the application, so closing/publishing writes serialize with submissions
on PostgreSQL. Contact and consideration edits lock the application and verify its
version. In-app notification uniqueness is scoped to application, recipient and kind.
Notifications carry no contact details; they link to the current private application.
No external email provider is used. Notifications appear in the existing Engagements
inbox and can be marked read only by their recipient.

## Consent

Professional email/phone entries are separate from login/account data, are never
prefilled from account emails, and are discarded when their share box is unchecked.
Creator approval applies only to that application and takes effect when considered
or shortlisted. The brand supplies and explicitly approves its own contact email.
Both parties can revoke permission; views read current consent on every request.
Revocation cannot recall information someone previously viewed. Contacts are never
copied into ranking caches, engagement snapshots, public pages or notifications.
Considering is not itself creator consent; brands can request permission in-app.

## Recommendations

`top_applicants` uses `discovery.matching.score_creator` and the existing weights,
normalization and portfolio/profile evidence. Its adapter has a namespaced job/version
cache identity, no invented licensing/audience requirements, and no Gemini insights.
Optional format/aspect preferences are skipped when absent (existing Brief requirements
remain unchanged). Results are live and limited to the best five active submitted
applicants. Unknown pricing, availability and evidence are explained, never fabricated.
Gemini configuration, quotas, prompts and fallback behavior are unchanged.

## Existing collaboration integration

Engagement now accepts exactly one source: an existing Brief or a JobPosting.
The migration preserves every existing brief relationship and adds a nullable posting
relationship, a source constraint and unique posting/creator constraint. Job invitations
use the same Engagement, EngagementEvent and transition services as brief invitations.
Project snapshots contain no contacts and do not change when postings are edited.
Existing brief-based approved moodboards remain available only for their original source.
Repeated invitation clicks return the existing engagement. Applicants may be invited
after applications close, provided the delivery deadline has not passed.

## Local demo data

Explicitly run `python manage.py seed_opportunities --development` only with
`DJANGO_DEBUG=True` and a deliberately selected local SQLite development database.
The command refuses PostgreSQL and non-debug settings; it never runs on startup.
It creates five labeled fictional brands and projects with unusable passwords and
`example.invalid` addresses. Existing usernames/emails and records are never changed.
Reruns do not duplicate or refresh records. It creates no fake applicants or rankings.

## Validation and deployment

Tests cover the new-brand/two-creator journey through application, considering,
contact approval, invitation, delivery, revision and completion using real models
and views. Regression tests cover original brief-based engagements and discovery.
Apply the new additive migrations through the normal approved deployment process;
no production data or deployment configuration is changed by development checks.
