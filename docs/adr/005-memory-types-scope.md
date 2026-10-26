# ADR 005: scope of the eleven memory types

Context: the architecture lists eleven memory types with a table each and a pruning rule each. Building every table literally would add code that nothing reads.

Choice: (1) relational memory records people and handles from message authors, joining only on an identical full name (two or more words), and the delete cascade prunes people with no remaining messages; (2) meta-memory stores numbers only (calls, failures, successes) for 30 days; (3) the 7-day raw-buffer purge is not built because items are chunked and marked processed in one transaction, so no raw buffer exists to purge; (4) there is no `projects` table, compartments partition every row.

Why: each choice keeps the verified-delete guarantee (a deleted conversation leaves no names behind) and adds no unread storage.

How to reverse: add the table and a writer in `memory/`, and extend `Deleter` if it holds names or content.
