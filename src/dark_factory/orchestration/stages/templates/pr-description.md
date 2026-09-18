<!--
dark-factory change request description template (T-094).

Edit this file to reshape the description the factory puts on every pull
request it opens. Placeholders are double braces around a lowercase name
(inline or on their own line):

  {{change_id}}      factory change id, e.g. chg-001
  {{title}}          change title from the tracker
  {{description}}    task text from the tracker (fallback sentence when empty)
  {{stage}}          pipeline stage that opened the request, e.g. construction
  {{role}}           role owning the stage, e.g. develop
  {{attempt}}        attempt number of the stage operation
  {{repository}}     product repository slug
  {{source_branch}}  task branch the request merges from
  {{target_branch}}  base branch of the request
  {{commit_sha}}     head commit the request is opened for
  {{risk_class}}     risk class of the change, e.g. R1
  {{external_ref}}   tracker reference, e.g. PLANE-42 (n/a when absent)
  {{run_id}}         factory run id

Unknown placeholders fail the runtime at startup (fail-closed). Do not put the
provider adapter's change-id marker into the template: the adapter appends that
marker to the body itself, and an extra marker would break change-request
lookup.
-->

## Summary

**{{title}}**

{{description}}

## Details

| Field | Value |
|---|---|
| Change | `{{change_id}}` |
| Repository | `{{repository}}` |
| Stage | `{{stage}}` (role: `{{role}}`, attempt {{attempt}}) |
| Risk class | `{{risk_class}}` |
| Tracker | {{external_ref}} |
| Run | `{{run_id}}` |

## Proposed change

- Branch: `{{source_branch}}` → `{{target_branch}}`
- Head commit: `{{commit_sha}}`
- Produced by the dark factory agent stage; machine gates run in CI on the head commit.

## Review & merge

- Merge is a human decision on `{{target_branch}}`; approvals are bound to the head commit.
- The task text above is the tracker description the factory acted on — raise questions against it.
