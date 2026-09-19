---
name: chunked-large-file-append
description: Split large file appends into multiple small edits to avoid max_tokens truncation rejection
source: auto-skill
extracted_at: '2026-09-16T07:10:39.759Z'
---

# Chunked Large File Append

## When this applies

You need to append or insert a substantial block of text (>~80 lines) into an existing file via the `edit` tool. A single edit call with very large `new_string` will be truncated by the model's max_tokens limit, and the tool will reject the write to prevent corrupting the file.

## Procedure

1. **Estimate size first.** If the new content is roughly 80+ lines or ~4 KB+, do NOT attempt it in a single edit call.
2. **Split into 3–5 chunks** of ~30–60 lines each. Natural break points: section headers, table boundaries, paragraph ends.
3. **First chunk:** use `edit` to replace the last line(s) of the existing file with those same lines + the first chunk of new content. This anchors the insertion point.
4. **Subsequent chunks:** use `edit` to replace the last line(s) of what you just wrote with those lines + the next chunk. Each edit is small enough to stay within token limits.
5. **Verify** by reading the tail of the file after the final chunk.

## Why this works

- Each individual `edit` call stays well under the max_tokens threshold for a single response.
- The `old_string` anchor (last few lines of the previous chunk) is short and unique, so matching is reliable.
- No partial/corrupt writes: if one chunk fails, the file still ends at the last successful chunk's boundary.

## Anti-pattern

A single `edit` call with a 150-line `new_string`. The model generates the tool call, hits max_tokens mid-string, the tool detects truncation and rejects the entire write. You lose the work and must retry anyway — but now you know to split.

## Rule of thumb

- < 40 lines new content: single edit is fine.
- 40–80 lines: borderline; try single edit, have a fallback plan to split.
- > 80 lines: always split into chunks from the start.

## Resuming after context compaction

If the session was compacted and you are resuming mid-append:

1. **Re-read the file tail** (`read_file` with `offset` near the end) to get the exact current last lines. Do NOT rely on the anchor text stored in a summary — it may be stale if another edit landed, or the line numbers may have shifted.
2. Use the freshly-read last 1–3 lines as your `old_string` anchor for the next chunk.
3. Continue the normal chunk procedure from there.

This costs one extra `read_file` call but prevents a failed edit due to a mismatched anchor.

## Validated at scale (2026-09-16)

7 sequential edits appending ~450 lines total to a 674-line file, each chunk 30–80 lines. Zero rejections. The file grew from 674 → 1124 lines. Natural section headers (`##`, `###`) made clean break points throughout.
