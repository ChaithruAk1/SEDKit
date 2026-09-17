# ADR guide (sed-draft-adr)

## Which decisions
Draft an ADR for a choice that is hard to reverse and that the requirements force but the recorded ADRs leave open:
- integration style (synchronous API, events, files) and where business rules run;
- data ownership and master data (which system is the source of truth);
- identity, access and security boundaries;
- hosting, deployment and release approach;
- build, buy or configure.
Skip trivia (library choices, naming) and anything a recorded ADR already decides.

## Shape (MADR)
- **Title:** the decision, stated positively ("Keep supplier master data in the ERP").
- **Context:** the problem and constraints from the pages, in neutral words.
- **Drivers:** what the choice must satisfy (quality attributes, constraints, deadlines).
- **Options:** two to four real alternatives, including the obvious simple one. Pros and cons are about the drivers.
- **Decision:** the chosen option and why it wins against the drivers.
- **Consequences:** what becomes easier, what becomes harder, and follow-up work.

## Honesty
- When the pages do not give enough to choose, still list the options and pick the one that keeps options open, and
  say in the rationale what information would change the choice. Lower `confidence`.
- Never invent systems, vendors, costs, volumes or people.
