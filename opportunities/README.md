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

## Simple workspaces

The creator workspace shows six currently open projects per page using the same
published/open/deadline selector as Explore Opportunities. Each card submits to the
existing application endpoint with CSRF and an optional message. The fixed workspace
return destination accepts no arbitrary URL. Contact permission remains off by default;
creators can explicitly grant it from their private application later. Applied state
and My Applications are always scoped to the logged-in creator.

The brand workspace uses the existing creator directory queryset and keyword search.
Post a Project and My Projects are the primary actions. Project titles open applicants;
management/edit links remain available. Studio, Gemini Brief Builder, comparison and
saved briefs live in an accessible native details section. Creator portfolio/profile
and verification links remain in the equivalent secondary section. Both workspaces
show their own unread engagement notifications and link to the existing inbox.

## Local demo data

Run `python manage.py seed_demo_brands` explicitly with `DJANGO_DEBUG=True` and a
local/test SQLite database. It creates five labeled fictional brands and ten realistic
projects, using the normal JobPosting model. Pending migrations produce an actionable
error before any data is inserted. PostgreSQL and non-debug environments are refused;
no seed runs automatically in production.

The older `seed_opportunities --development` command remains available and uses the
same shared catalog and identities for its original five-project seed. The new command
can add a second project to those existing, identifiable demo brands without resetting
or editing their first projects. Colliding real accounts are skipped. Reruns preserve
edits, deadlines, statuses and records. All demo passwords are unusable and email
addresses use example.invalid. No applicants or scores are fabricated.

## Validation and deployment

Tests cover the new-brand/two-creator journey through application, considering,
contact approval, invitation, delivery, revision and completion using real models
and views. Regression tests cover original brief-based engagements and discovery.
Apply the new additive migrations through the normal approved deployment process;
no production data or deployment configuration is changed by development checks.

## Brand audit clarifications

Project cards show application and delivery dates. Counts on the workspace, My
Projects and management pages include all application records, including withdrawn
applications. Applicant-page totals reflect the current filters; matching still
uses only active submitted creators.

Applicant review reuses the public portfolio card for up to three previews. Concrete
format/style/tool overlap retains its relevance label; otherwise recent public work
is clearly labeled as samples. Portfolio visibility follows the existing active
Creator account rule. Applications remain reviewable after a creator becomes
unavailable, without linking to or previewing the hidden profile/portfolio.

The brand audit requires no new migration. Work type, free-text output instructions,
duration and additional requirements remain contextual inputs, not new scoring
factors. Existing Smart Matching uses its supported taxonomies, portfolio format,
aspect, style, experience, pricing, availability and delivery deadline evidence.
