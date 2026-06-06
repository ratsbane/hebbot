# Claude Code transcripts

Archived Claude Code session transcripts (`.jsonl` format, one JSON record per line) from the development of `hebbot`. These are the record of how the substrate framing emerged from the work — including dead ends, reversals, and the moments where the thesis got reshaped.

## What's here

- `macbook-session-2026-05-25-to-2026-06-05.jsonl` — the reset conversation: the strategic reframe from `panda-mcp`, the OMEN setup from scratch, the rename to `hebbot`, the foundations doc, the architectural decisions about substrate vs. orchestrator. ~6 MB; spans roughly 11 days of intermittent work.

(Sessions from the prior `panda-mcp` project may be archived in that repo separately; they are not duplicated here.)

## Format

The `.jsonl` files are Claude Code's native session format: one JSON object per line, each representing a message (user input, assistant response, tool call, tool result, system reminder, etc.). They can be replayed, mined for findings, or just read.

For programmatic access:

```bash
# count messages
wc -l *.jsonl

# extract just the user prompts
jq -r 'select(.type == "user" and (.content | type) == "string") | .content' < session.jsonl
```

## Why save these

1. **Provenance** for design decisions. The foundations doc captures the *what*; the transcripts hold the *why* and *how* it was argued through.
2. **Training data** for future fine-tuning or analysis of what skills the substrate's higher layer actually needed in practice.
3. **Honest historical record.** Doug pushed back on early framings; we changed direction multiple times. The transcripts document that work, not just the winning argument.

## Privacy note

These transcripts include file paths, IP addresses, and project details from a private development environment. They live in this private repo intentionally — do not publish without redaction.
