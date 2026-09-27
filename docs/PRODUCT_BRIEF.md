# FitWaze

Personalized Walking and Cycling for Better Health

## Product purpose

PERSON + PLACE = PERSONALIZED PHYSICAL ACTIVITY.

FitWaze is a mobile wellness and physical-activity support application. It
answers two questions: what activity should I do, and where should I do it?
Walking and cycling are the initial activities. The intended audience includes
insufficiently active people and people seeking to prevent or manage
cardiometabolic conditions. The application does not diagnose or treat conditions.

## Initial demo acceptance flow

1. Enter and save a profile.
2. Each morning, check in: how you feel, an optional blood glucose reading
   (mmol/L or mg/dL), and any foot problem or warning symptoms. Low or very
   high glucose, feeling unwell or warning symptoms produce a "Not today"
   answer with what to do instead; a foot problem moves the session to
   cycling; high glucose or tiredness shortens it.
3. Receive today's activity recommendation with a duration and explanation.
4. Accept the suggested activity or choose walking/cycling for the session.
   A new recommendation remains subject to the saved activity abilities.
5. Routes start from the saved home (or current location), and are fetched
   automatically. Any start point can be saved as home.
6. Request several candidate round trips approximately matching the duration.
7. Compare explanations, duration, distance and uncertainty; unsuitable known
   terrain can withhold a route, so fewer candidates may be returned.
8. Select a route: the map goes full screen with a control sheet over it.
   Start the journey and follow the pulsing position dot and the travelled
   trail; the app says when you are back at the start. Opening the route or
   directions home in Apple Maps or Google Maps is optional.
9. Finish or abandon the activity and review progress. Alternatively, choose
   a simulated journey, follow its animated marker and finish into demo progress.

The same mobile interface serves `/`, `/demo` and `/mobile`. On larger screens
it stays phone-width. Starting a journey keeps its map and controls together.

## Current engine integration and its limits

The Activity Engine uses activity preferences and abilities, weekly activity
minutes, goals, diabetes status and mobility limitations. Age, sex, height,
weight and frequency are collected, but not every collected field currently
changes the activity duration. Age additionally influences route scoring.
Prior activities and feedback do not yet adapt the recommendation rules.
The current rules are demo product logic, not a clinically validated plan.

The Route Engine receives the activity and target duration from the persisted
recommendation. Walking and cycling use different weights; profile information
adjusts terrain emphasis and known feasibility limits. Invalid or one-way
routes are rejected, and the client sends the candidate revision to detect a
changed route at selection. Activity choice must not bypass reported inability.

The mock provider is synthetic and does not follow streets. ORS provides street
routing, but requires configuration and a live smoke test. With ORS, each
route's topography (steepest sustained gradient and total climb, from
elevation) and surroundings (steps, busy roads, paths/footways, cycleways and,
for walking, greenery) are measured from ORS elevation and extra_info layers
and drive scoring and the feasibility gate. Sidewalk coverage, crossings and
safety remain unmeasured placeholders and are labelled as such. Sidewalks, protected cycle facilities, traffic speed,
crash data, parks and other GIS enrichment remain integration work. Not every
factor in the proposed product brief has a measured data source today.

GPS following pans the map with actual movement and draws a travelled trail.
It requires location permission and a secure context. The current web demo
does not provide reliable background tracking, turn-by-turn instructions,
route snapping or automatic rerouting.

Real progress currently aggregates completed sessions using planned route
distance and estimated duration, not uploaded GPS measurements. Simulation
moves along the selected geometry in approximately 20 seconds without GPS.
Its completions stay in memory for the current tab, appear separately in demo
progress and never create or complete real activity sessions.

## Further development

- Enrich route attributes with verified GIS sources and visible coverage.
- Review and validate wellness recommendation rules with domain experts.
- Add feedback and history-based adjustment with explainable behavior.
- Distinguish measured activity duration/distance from planned values.
- Extend guidance and evaluate native background tracking if required.
- Test the full journey on actual phones and with live routing data.
