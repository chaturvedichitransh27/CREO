# Optional visual references

Both existing brief forms (`/briefs/new/`, `/briefs/<id>/edit/`) and opportunity
forms (`/opportunities/new/`, `/opportunities/<id>/edit/`) accept zero to three
reference images. Removing a saved image requires the same owner-authorized,
CSRF-protected POST as saving the parent. No files means keep existing images.

`ReferenceImage` has exactly one Brief or JobPosting parent. Active images occupy
unique positions 0–2 per parent. The save service locks that parent and rechecks
the final count after removals, before saving parent fields, taxonomies or files.
Database constraints additionally prevent a fourth active slot. Existing image
positions stay stable; additions fill free slots in upload order.

The existing `portfolio.validators.validate_image` enforces JPEG/PNG/WebP,
5 MB per image and a 25-megapixel decoded-size limit. References are re-encoded
as WebP, oriented correctly, bounded to 2400 pixels, and stripped of EXIF,
original names and trailing data. They are not sent to Gemini.

Storage reuses `trust.models.evidence_storage`, the same private backend used by
Creative Studio: private filesystem storage locally or existing database media
storage with `DATABASE_MEDIA=True`. The checked-in Render blueprint already sets
DATABASE_MEDIA=True; no configuration or credentials are changed. There is no
Cloudinary integration in this checkout. Do not use ephemeral filesystem storage
for production uploads.

Only the permission-checked `/briefs/references/<id>/image/` route serves bytes.
Private brief references are visible to the owner and recipients of an invitation
that explicitly captured them. Published opportunity references are public;
draft opportunities are private. Responses are non-cacheable, inline WebP with
nosniff headers. Public `/media/` routes never serve these private storage names.

Invitations capture a many-to-many selection of the current reference records in
the same transaction and parent lock as their existing text snapshot. Removing or
replacing a shared reference retires the original record without changing its
bytes or order. Existing invitation recipients retain that immutable reference;
new uploads are not retroactively shared. References are never copied from Studio
moodboards. Pre-feature invitations have no references added retroactively.

Unshared removed files are deleted after database commit. Cascading parent deletion
uses the same cleanup; a rolled-back transaction keeps its old files. Newly staged
files are cleaned up if saving fails. Cleanup uses the backend's delete method;
a post-commit deletion failure is logged by Django rather than undoing a successful
save. Snapshot-retained files are intentionally not deleted while still shared.

The native horizontal scroll-snap carousel works without JavaScript; the lightweight
script adds arrows, keyboard navigation and a counter. New selections have thumbnails
and removable previews. Existing-image removal checkboxes and multi-file uploads
remain usable without JavaScript. Browsers cannot retain new file inputs after a
validation response, so the form asks users to reselect new uploads; existing files
are preserved. Only one frame is shown per reference, including animated input.

## Migration and verification

Apply `briefs.0003_referenceimage` and `engagements.0003_engagement_reference_images`
through the normal approved deployment (`python manage.py migrate`). These are
additive changes; existing briefs/opportunities/invitations start with no references.
No production migration has been run by this task.

Tests live in `briefs/test_references.py` and `briefs/test_reference_concurrency.py`.
They generate temporary image files and exercise local and database storage without
external uploads. The concurrent upload test requires an explicitly isolated
PostgreSQL test database; SQLite cannot verify row-lock concurrency. The normal
full test suite covers existing matching, Studio and engagement behavior.
