"""The audit trail: who did what, when, and how they were identified (docs/audit.md).

* `store`: one append-only SQLite file per profile, `DATA_DIR\\audit\\audit.db`, kept apart from `sed.db`; entries
  are chained by hash so an edit made outside SED shows; nothing ever updates, deletes or prunes an entry.
* `record`: what every action calls. `record(...)` writes one entry; `start(...)` writes the attempt before an action
  and the returned `Attempt` writes its outcome after, so a change sent out is on record even if SED stops half-way.
  Both refuse the action when the entry cannot be written.
"""
