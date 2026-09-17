# Release notes style (sed-draft-release-notes)

- **Audience:** business users and their managers. Say what they can now do or what works better, never how it was
  built ("Partners can register deals from the portal", not "Implemented the deal registration API").
- **Structure:** a two-to-four-sentence summary, then sections in this order when they have entries: "New features",
  "Improvements", "Fixes". Add "Known issues" only for limitations users will meet.
- **Grouping:** related issues share one entry (list all their keys). Internal work users never see (refactoring,
  build pipeline) is left out, or summarised in one "Behind the scenes" entry when it matters to them.
- **Numbers:** only as `{{f:<fact_key>}}` tokens from the packet facts, listed in `facts_cited`. No dates, versions or
  counts typed by hand; version names come from the `fix_versions` fact.
- **Tone:** plain, factual, no marketing words, no blame, no person names.
