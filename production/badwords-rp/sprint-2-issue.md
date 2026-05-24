# Sprint 2: BadWords RP config layer (Polish / Witness / Selective)

## Goal

Define what BadWords should mark vs. cut, per RP service tier. End the sprint with three YAML configs and a README that maps RP modes to BadWords settings.

## Background

BadWords default cuts ums, false starts, retakes. The Say Why methodology preserves them as polyvagal/somatic data. The config layer is the mode switch.

Three modes (from `SPRINTS.md`):

| Mode | Behavior |
|---|---|
| Polish | Cut filler. BadWords default. |
| Witness | Mark but do not cut. Heat-map for emotional pivots. |
| Selective | Mark all, editor decides per-instance. |

## Tasks

- [ ] Read `~/Downloads/BadWords-main/src/config.py` (255K) to find what settings actually exist
- [ ] Read `~/Downloads/BadWords-main/src/algorithms.py` (39K) to find the filler-detection logic
- [ ] Produce a table mapping BadWords settings → 3 RP modes
- [ ] Write `polish.yaml`, `witness.yaml`, `selective.yaml` in `production/badwords-rp/configs/`
- [ ] Define the filler-word list per mode (witness marks almost nothing red, polish marks um/uh/you-know/like/I-mean)
- [ ] Define silence thresholds per mode (witness preserves long pauses, polish tightens them)
- [ ] Add `README.md` explaining how to load a config into BadWords (file path, naming convention, install steps)

## Not in scope this sprint

- Installing BadWords (sprint 3)
- Running it on real footage (sprint 3)
- Wiring to `resolve_workflow.py` (sprint 4)
- PWA front-end (sprint 5)

## Done when

A future session can install BadWords and load one of these YAML configs to get RP-aware behavior, without needing to redo the methodology mapping. Specifically: someone unfamiliar with the work can pick `witness.yaml`, follow the README, and end up with a BadWords session that marks fillers without cutting them.

## References

- Sprint 1 inventory: [production/badwords-rp/SPRINTS.md](../production/badwords-rp/SPRINTS.md)
- BadWords upstream: https://github.com/veritus-git/BadWords
- RP service architecture memory: [project_rp_service_architecture.md](https://github.com/risaac09/rubinstein-productions-toolkit) (April 2026: Mirror/Map/Territory → Founder Story / Program Engagement / Org Embedding)
- Transcription rule precedent: `~/rp-shared/worker/index.js` "Keep ums, false starts, and self-corrections"

## Voice

Methodology copy in any config or README passes stop-slop and isaac-voice gates. No em-dashes. No rule-of-three. No false agency.
