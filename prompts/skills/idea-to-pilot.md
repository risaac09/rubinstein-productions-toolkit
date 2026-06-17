---
name: idea-to-pilot
description: The procedural middle between an approved idea and a shipped pilot app. Use when a seed has matured into a "Tool" (per seed-bed) and Isaac wants to take it from concept to a working, single-user PWA with governance, data, and security baked in, not bolted on. Trigger for "build a pilot", "turn this into an app", "I have an idea for a tool", "spec this out", "make an MVP", or any handoff from seed-bed where the kind is Tool. Pairs with agentic-development (the how of building with agents) and rp-shared-foundation-spec (the stack pilots inherit).
---

# Idea to Pilot

The toolkit had two bookends and no middle. `seed-bed` triages ideas up to
classification. `agentic-development` iterates on code that already exists. This
skill is the span between them: approved idea to shipped pilot, with the
governance, data, and security decisions made on purpose at the right stage
rather than discovered after launch.

Default target is a **single-user PWA** on the proven stack (vanilla JS, one
HTML/CSS/JS, service worker, localStorage, GitHub Pages). That stack scales for
one person on one device with no backend. The moment a pilot needs more than
that, the Graduate gate routes it to `rp-shared-foundation-spec`.

## Handoffs

- **In:** `seed-bed` matures a seed into kind `Tool`. That is the entry.
- **The how:** `agentic-development` (blast-radius thinking, same-context testing, atomic commits) runs inside every build stage below.
- **Out:** a shipped pilot hands to `project-management-coordinator` for upkeep, or back to `seed-bed` as a new Signal if it spawns ideas.

## The six stages

Each stage has one input, one job, one artifact, and one gate. Do not pass a
gate by skipping its artifact.

### 1. Spec
- **Job:** name what the thing is and what it refuses to be. One page.
- **Artifact:** `SPEC.md` with: the one job, the user, the data model (fields + where they live), the consent and threat note (what data is collected, what never leaves the device, what could go wrong), and the explicit non-goals.
- **Gate:** the data model and the consent note exist before any code. This is where governance is cheapest.

### 2. Shape
- **Job:** design the surface. Views, states, the one core loop.
- **Artifact:** a view list and a state shape (the localStorage key and its schema). Borrow the house aesthetic: warm tones, intentional friction, no SaaS blue.
- **Gate:** the state schema is written down. It becomes the load-time validator in stage 4.

### 3. Build
- **Job:** make it run. Vanilla JS, one file per concern, no build step.
- **Artifact:** working `index.html` + `app.js` + `app.css` + `sw.js`, plus a `test.js` written in the same session as the feature (per agentic-development).
- **Gate:** `node test.js` passes. The core loop works in a browser.

### 4. Harden
- **Job:** make it safe to hand to a stranger. This is the stage the current apps skip.
- **Artifact:** load-time schema validation on the localStorage state (reject or migrate bad state, do not trust it), a visible privacy line ("no accounts, no servers, no analytics" only if true), and a data-export path so the user can leave with their data.
- **Gate:** corrupt state does not crash the app, and the privacy claim is verifiable, not just asserted.

### 5. Ship
- **Job:** deploy and version.
- **Artifact:** GitHub Pages live, service worker cache version bumped, `VERSION` set, a one-paragraph README with the live URL.
- **Gate:** the live URL loads offline after first visit.

### 6. Graduate (only if needed)
- **Job:** decide whether the pilot stays single-user or needs a backend.
- **Trigger:** multi-device sync, a paid tier, or shared/team data. If none apply, stop here. Single-user is a finished state, not a lesser one.
- **Artifact:** if it graduates, an integration against `rp-shared-foundation-spec` (auth, data validation, consent, paywall inherited, not rewritten).
- **Gate:** no pilot claims "scalable" or "secure" in public until it either stays single-user by design or sits on the deployed foundation.

## Governance, data, and security, by stage

The point of the pipeline is that these are not a separate workstream:

- **Spec** sets the data model and the threat note.
- **Shape** sets the state schema.
- **Harden** enforces the schema at load, exposes export, and makes the privacy claim true.
- **Graduate** is the only door to a backend, and it inherits security from the foundation rather than improvising it.

## Anti-patterns

- **Building before the spec.** The data model is cheapest to get right on page one.
- **Asserting privacy you have not built.** "No analytics" is a claim a user should be able to verify.
- **Calling a single-user pilot a failure.** Most pilots should stop at Ship. Graduating is the exception, not the goal.
- **Improvising auth or payments per app.** That belongs in the foundation, once, deployed.

## The honest current state

Three pilots (Alchemy, the Diagnostic, Say Why) reached Ship as single-user
apps. None ran the Harden stage (no load-time schema validation, no export, the
privacy claim is philosophy not mechanism). The foundation that the Graduate
gate depends on is specified but not deployed. This skill names the path; the
work is to walk it.
