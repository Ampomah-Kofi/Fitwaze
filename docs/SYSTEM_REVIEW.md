# FitWaze logic, system design and flow review

## Confirmed failures and corrections

| Finding | Evidence | Correction |
| --- | --- | --- |
| Live API and client were different versions | Live OpenAPI lacked `candidate_revision` and the session activity request schema while HTML used them | Restarted the verified local API with current code; added `backend/start-demo.ps1` with reload and the existing SQLite database |
| Route requests fail after access-token expiry | Client sent the same in-memory JWT indefinitely | Protected requests renew via the refresh cookie, share one in-flight refresh and retry; expired refresh returns to sign-in without discarding the same user's in-tab route |
| Route button can reopen the wrong panel or scroll position | The recommendation button only changed the active screen | It now opens the start-point panel and resets scroll, with visible instructions |
| Hidden-map viewport operations | Route results switched to the list before focusing map bounds | Map viewport work now runs only while visible, and bounds refresh on returning to Map |
| Route failures were fleeting | Errors were only presented in a disappearing toast | Route requests also leave a persistent inline error and an available retry button |
| Displayed profile could differ from the engine input | Sign-in never fetched the saved profile | Sign-in loads the saved profile before showing Today; changing accounts clears in-tab route/progress state |
| Account deletion failed after creating a recommendation | Live cleanup returned 500; regression reproduced a non-null foreign-key error | Delete owned sessions/recommendations before deleting the user; verify other users remain unaffected |

## Intended flow and ownership

1. Authenticate. The server derives the user identity from the access token.
2. Load/save that user's profile. No profile ID is supplied by the client.
3. Create a recommendation from the saved profile and optional session activity
   choice. Reported inability must block that choice.
4. Choose a starting point. Route offers depend on the recommendation and this
   location. Changing either invalidates old offers and pending responses.
5. Generate, validate, filter and score candidate round trips. A provider failure
   or lack of usable routes must produce an actionable response.
6. Select an offered candidate using its revision. The server recomputes its
   data, checks ownership and current feasibility, and rejects changed routes.
7. Start the real journey or a local simulation. Real GPS is followed, not
   artificially advanced along the planned route. Simulation is explicitly labelled.
8. Complete/abandon the session. Terminal status cannot be replayed to add totals.
9. View progress scoped to the authenticated user. Simulated completions are
   separate, local to the tab, and not posted as exercise.

## Remaining design limitations

- The running demo uses **mock routes**. They are synthetic, not street-following
  navigation. ORS configuration and live service verification are still required
  for real routes. GIS infrastructure and safety attributes are not yet measured.
- Activity rules are deterministic demo rules, not a clinically validated plan.
  Some collected fields and previous-session feedback do not yet influence them.
- Progress uses planned distance and estimated duration for completed sessions,
  rather than measured activity. The UI/product should distinguish these before
  reporting them as measured health outcomes.
- Route cache and rate-limit state live in one process. A deployment with several
  workers needs a shared design; candidate revisions detect changed offers but
  do not make the cache shared.
- The client guards duplicate/overlapping sessions in one tab. The backend does
  not yet enforce one active session across tabs/devices or provide selection
  idempotency. A database-level lifecycle design is needed for that guarantee.
- Same-tab reauthentication retains a route; page reload does not restore an
  active session. Persisted active-session discovery/resume needs explicit API
  support. Simulation progress is intentionally lost on reload.
- GPS tracking is foreground web tracking. Turn-by-turn directions, rerouting,
  route snapping and native background tracking remain unimplemented.
- Browser automation was unavailable for this review. Client checks use a DOM
  stub, and live API checks do not establish real-device usability.

## Validation performed

Live localhost API checks created a temporary account and exercised profile,
walking recommendation, cycling recommendation, three options for each, route
selection, completion and progress (two completed sessions). Test accounts were
removed successfully after the deletion fix. The live OpenAPI was checked for
the new request/response contract. Regression coverage includes hidden-map
operations, route-panel navigation, persistent errors, concurrent token refresh,
in-tab session preservation and owned-history deletion.
