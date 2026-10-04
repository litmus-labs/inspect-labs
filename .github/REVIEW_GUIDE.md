# Review guide

Every reviewer, human or automated, checks a pull request against these rules.
They protect what Inspect Labs promises its users: that lab logs can be trusted and
that safeguards act before an action reaches a Lab.

## Must hold

1. **Unknown stays unknown.** A missing or unreadable result is recorded as unknown
   (null), never as a pass, a fail or zero.
2. **Rescoring and replay run nothing.** `rescore`, `replay-rules`, `monitor` and
   `attach` read saved files only. They never call a Lab or dispatch an action.
3. **Refused actions never reach the Lab.** Every tool call goes through the shared
   `Gateway`. Undeclared operations and out-of-range arguments are refused.
4. **Lab logs stay stable.** Changes to `LabLog` fields, digests or the hash chain
   keep old files verifiable, and the hashes stay the same across processes.
5. **Same rules, same decisions.** Evaluation (`connect_lab`) and serving
   (`inspect-labs serve`) decide the same way for the same action.
6. **No secrets or private material.** No credentials, private evaluation answers,
   partner data or `.research/` content in public files. Native logs are not
   automatically safe to publish.
7. **Claims match evidence.** Docs and docstrings don't claim real-instrument,
   physical-safety or scientific validity that tests with simulators and mocks
   can't show.

## Should hold

- Plain, descriptive names. No new coined terms.
- Native Inspect AI and Inspect Robots objects. No new runner or task hierarchy.
- Tests at user, persistence and untrusted-input boundaries.
- Small, focused changes. Public API changes keep deprecated aliases for a release.
