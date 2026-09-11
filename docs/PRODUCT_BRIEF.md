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
2. Receive an activity recommendation with a duration and explanation.
3. Accept the suggested activity or choose walking/cycling for the session.
   A new recommendation remains subject to the saved activity abilities.
4. Use current location, tap the map, or enter latitude and longitude.
5. Request several candidate round trips approximately matching the duration.
6. Compare explanations, duration, distance and uncertainty; unsuitable known
   terrain can withhold a route, so fewer candidates may be returned.
7. Select a route, start the journey, and follow the live GPS marker on the map.
8. Finish or abandon the activity and review progress. Alternatively, choose
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
routing, but requires configuration and a live smoke test. Surface attributes
are currently unknown for ORS: placeholder scores must not be presented as
measured accessibility. Sidewalks, protected cycle facilities, traffic speed,
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
