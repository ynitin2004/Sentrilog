-- documents.sha256 was NOT NULL, but with presigned-PUT uploads (client -> S3 directly) the
-- intake API creates the document row *before* the file exists in S3, to hand out a presigned
-- URL tied to a specific document id. It genuinely cannot know the hash at that point. The
-- hash gets backfilled once something actually reads the object (Phase 4 extraction, or a
-- lightweight upload-confirmation step) -- until then, NULL means "not yet uploaded/verified,"
-- which is a real, meaningful state, not a data-quality gap.
ALTER TABLE documents ALTER COLUMN sha256 DROP NOT NULL;
