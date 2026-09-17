# Report section style (sed-draft-report)

## General
- Write for the audience named in context.md: support leads want specifics and actions, business stakeholders want
  impact and asks, IT leadership wants money, risk and decisions, a vendor review wants evidence for a negotiation.
- Lead with the point, then the evidence. One idea per bullet or sentence.
- Every number is a `{{f:<fact_key>}}` token. Pick the fact whose label says exactly what you mean: a delta fact for
  "up/down against the average", a count for "how many", a percentage for rates. When no fact says it, use words
  ("most", "several", "higher than last month") instead of a number.
- Say "AI-assisted" when you cite an AI-derived fact (marked in facts.md).
- Neutral, factual tone. No blame, no names of people, no ticket numbers, no guesses beyond the findings.
- Plain Markdown only: short paragraphs or `-` bullets, `**bold**` for at most one phrase per bullet. No headings,
  tables, links or images.

## Section types
- **Headline** (summary): one or two sentences, the single most important point with one or two facts.
- **Executive / relationship summary** (summary): four or five sentences that combine the other sections; do not
  introduce facts the sections did not use.
- **Highlights / lowlights**: two or three bullets each, each with its fact.
- **Recurring issues / top issues / risks**: one bullet per finding you cite (its effect and what is being done), and
  say plainly when there are none.
- **Actions / asks / decisions needed / negotiation points**: three to five bullets starting with a verb, each with
  the role that owns it and, when there is one, the date or deadline fact.
- **Performance / spend / license sections**: the trend first (better, worse, stable against the comparison fact),
  then the one or two drivers from the tables.

## Before you ingest
- Count the words (a token counts as one): stay within the section's limit.
- Check that every token key exists in facts.md, character for character.
- Check `cited_finding_ids` against findings.md.
