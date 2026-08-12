-- similarity_score was NOT NULL, but "no face could be detected in the image" is a real,
-- distinct outcome from "a face was found and scored low" -- storing 0.0 for the former would
-- read as "confidently not a match" when the true state is "couldn't even check." Same
-- reasoning as 004_documents_sha256_nullable.sql: NULL means a real thing here, not a
-- data-quality gap.
ALTER TABLE face_matches ALTER COLUMN similarity_score DROP NOT NULL;
