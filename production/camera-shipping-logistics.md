# Camera-Shipping Logistics

> **Status:** accepted 2026-10-16, with open parameters. A first standard written from what the repo already states. The timing offsets, spare counts, carrier, insurance and cost of damage or loss are not decided; [Open parameters](#open-parameters) lists them.

The camera-shipping model sends a camera and lens to the client, who films themselves while Isaac facilitates remotely. The [blueprint](../methodology/methodology-blueprint.md) calls this Phase 3 and lists its logistics as missing. This document is the logistics standard: the shipment lifecycle, the kit, the return, insurance, and what happens when something breaks or goes missing.

Two things come back at the end, and this document covers one of them. The footage and data return to the client; that return is set out in the [return package](return-package.md). The physical kit returns to RP; that return is here.

## Owner

**Kit owner.** One role is accountable for the kit's inventory, every shipment, the insurance record, and every return. It does not rotate per shipment. Isaac holds the role until the practice has someone else to hand it to. A name goes in the inventory header when the role changes hands.

The client names a **receiving person** before anything ships: the one who signs for the box, checks the contents, and packs the return. The receiving person is often the participant, but need not be.

## The shipment lifecycle

T is the day of the client's first filming session. Steps are counted back from T (T minus N) or forward from it (T plus N). The offsets are placeholders, since no transit time is on record in the repo; they are listed under [Open parameters](#open-parameters). Replace them with real transit times after the first full cycle. Calendar dates never go in this document; they go in the shipment record.

Phase 3 runs one to three filming sessions over one to two weeks, and the blueprint puts Phase 4 at one to two weeks after filming. Those are the only durations the repo states. The return window below sits inside them.

| Step | When | What happens | Owner |
|---|---|---|---|
| 1. Gate | T minus 21 days | Phase 2 is done. Receiving person named, shipping address confirmed, country confirmed. If the destination is outside the home country, stop and read [International](#international). Insurance is confirmed as covering this shipment (see [Insurance](#insurance)). | Kit owner |
| 2. Reserve | T minus 18 days | The kit is reserved in the inventory against this client. Nothing else ships from it until it is back. | Kit owner |
| 3. Bench test | T minus 14 days | Every item on the [kit checklist](#kit-checklist) is powered, recorded to, played back and reset. Cards are formatted in the camera. Firmware is noted. Serial numbers are logged. | Kit owner |
| 4. Pack and photograph | T minus 12 days | Packed to the [packing rules](#packing). Photographs of the kit laid out and of the sealed box are taken for the damage record. | Kit owner |
| 5. Ship | T minus 10 days | Handed to the carrier with tracking and the signature requirement chosen in [Carrier](#carrier). The client gets the tracking reference and the arrival window. | Kit owner |
| 6. Arrival check | T minus 7 days | The receiving person unboxes on camera or on a call, ticks the checklist, and reports any damage or missing item the same day. Late reports are harder to attribute, which is why the check has a deadline. | Receiving person |
| 7. Setup call | T minus 5 days | A short facilitated call: power on, record a test clip, play it back, confirm the framing and sound. Fix anything now. The call is the last point where a replacement can still arrive before T. | Isaac with receiving person |
| 8. Filming | T to T plus 14 days | The filming sessions. The kit stays with the client. | Client |
| 9. Return pack | Within 2 days after the last session | The receiving person packs the kit in the same box, with the checklist, using the prepaid return label that came in the outbound box. Tracking goes to Isaac. | Receiving person |
| 10. Return transit | Counted from the carrier's quote | Tracked. If the box is not scanned as moving within a few days of drop-off, the kit owner opens a [loss case](#damage-and-loss). | Kit owner |
| 11. Check-in | The day it arrives | Unboxed against the outbound photographs and the checklist. Any difference is a [damage case](#damage-and-loss) the same day. | Kit owner |
| 12. Offload and close | Within 2 days of check-in | Cards are offloaded and verified, the client's copy is prepared under the [return package](return-package.md), and the kit is reset and returned to inventory. Return rate is recorded for the [evaluation framework](../methodology/evaluation-framework.md), which tracks equipment shipped and returned against a target of 100%. | Kit owner |

If a step slips, everything after it slips by the same amount, and the setup call stays before T. If T moves, the whole table moves with it. The step order does not change.

## Kit checklist

Tick each line on the bench test, again on the client's arrival check, and again on check-in. The inventory holds the serial numbers and the actual spare counts; this list holds the categories. A spare is an item that stops a session if it fails and that the client cannot buy that day.

**Primary kit**

- [ ] Camera body, with serial recorded
- [ ] Lens, with serial recorded, front and rear caps on
- [ ] Lens hood, if one is used
- [ ] Power: batteries, charger, mains adapter for the client's country, charging cable
- [ ] Recording media: cards and a card case
- [ ] Audio: microphone, windscreen, cable or receiver
- [ ] Support: a stand, a clamp or a small tripod, whichever the kit uses
- [ ] Cleaning cloth
- [ ] Padded case or the wrapping cloth and padding the [example shoot plan](examples/2026-03-31-shoot-plan.md) shows
- [ ] Outbound box, return label, tape, the one-page setup sheet, and a printed copy of this checklist

**Backup gear.** Backup is decided per category, not per item.

- [ ] Spare batteries
- [ ] Spare cards
- [ ] Spare microphone, or a second audio path
- [ ] Spare cables for power, audio and any data connection
- [ ] Spare charger, or confirmation that the charger can be bought locally in the client's country
- [ ] Spare cap, hood and small hardware that is cheap and loses easily

The number of spares per category is open (see [Open parameters](#open-parameters)). No spare body ships, because a second body doubles the insured value. A failed body is covered by sending a replacement, which cannot arrive in less than the carrier's transit time.

## Packing

- Camera and lens travel separate from each other, each wrapped, with nothing loose inside the box.
- Batteries go in the box in the way the carrier's rules for lithium cells require. Those rules differ by carrier and by service, and the kit owner reads the current ones before each shipment instead of relying on this document.
- Cards go in their case, which is not loose in the box.
- The box is the same box in both directions where possible, so packing is a repeat, not a new job.
- Outbound and return packing are photographed. The photographs are the evidence in a damage case.
- Nothing in or on the box names the client's organization or what the camera is for. The label carries a name and an address.

## Return

"Everything is returned" has two readings in the repo, and this document keeps them apart. The footage and data go back to the client. The physical kit goes back to RP. A kit is returned when all of these are true:

1. Every item on the checklist arrived, with the serial numbers matching the inventory.
2. Nothing is damaged beyond ordinary wear, judged against the outbound photographs.
3. The cards that held the client's footage have been offloaded, the offload verified, and the client's copy prepared. Offload comes before cards are wiped for the next shipment. Cards are never wiped before the client has the footage.
4. The kit is reset: batteries charged, cards formatted in the camera, settings returned, firmware noted, and the inventory marked available.
5. The return rate is recorded.

Cards in transit hold the client's unreleased footage, so they are the most sensitive thing in the box. They travel with the kit, in their case, and the offload in step 12 happens on arrival.

## Carrier

No carrier is named anywhere in the repo, and the choice stays open. Choose from these questions, and record the answer in the shipment record:

- Does the carrier ship to the client's address and country, and with what delivery windows?
- Can it require a signature, and can the client schedule the delivery?
- Does its own declared-value protection exist, and is it enough or only a supplement to separate insurance?
- Does it carry lithium batteries on the chosen service?
- Does it support a prepaid return label inside the outbound box?
- What does it say about packaging, and which claims does it refuse when packaging was inadequate?

## International

The blueprint lists international shipping as missing. This document does not solve it. Before any shipment leaves the home country, the kit owner answers these, and the answers go in the shipment record:

- Does customs treat the kit as a temporary export and re-import, and what paperwork does that need?
- What value is declared, and does it match the insurance record?
- Does the destination restrict cameras, radio-equipped microphones or batteries?
- Who pays duties or taxes if the carrier asks for them, and is the return leg affected?
- Is the transit time long enough that the Phase 3 schedule needs to move?

Until these have answers on paper, no kit ships outside the home country.

## Insurance

This section states what must be decided, and every decision is open. It states no amounts and recommends no insurer.

**Decisions to make**

1. What is the insured object: the kit, the kit plus the client's footage, or the kit plus liability to the client for loss or damage?
2. Is cover per shipment, per period, or a standing policy on the inventory?
3. Does cover run in both directions, and while the kit is with the client?
4. Who carries the risk while the kit is in the client's hands: RP, the client, or both?
5. Is the carrier's own protection used as the cover, a supplement, or not at all?
6. Does any of it change for international shipments?
7. What evidence does a claim need, and is that evidence already produced by the lifecycle above (serials, outbound photographs, the arrival check, tracking)?

**Questions to ask an insurer**

- Does the policy cover equipment that is lent to a person outside the business and used by them unsupervised? Many policies cover equipment on a business's own jobs and not equipment in a borrower's hands.
- Is damage covered when it happens in use, or only theft and transit loss?
- Is the policy territorial, and does cover end at a border?
- What counts as proof of loss, and what is the deadline to report?
- Which exclusions apply to shipping: inadequate packaging, unattended delivery, carrier-excluded items?
- Is there an excess or deductible, and does it apply per item or per incident?
- Does it replace at new, at current value, or at an agreed value?
- Is the footage on the cards covered, and if not, what is the position on a data-loss claim?
- Does the client need to be named, or to accept any condition?

Record the answers, the insurer, the policy reference and its renewal point in the inventory header, not here. A public document should not carry a policy reference.

## Damage and loss

Every case opens a record the day it is found. The record has: the case number, which of these cases it is, the date found, who found it, the kit items affected with serials, the photographs, the carrier tracking history, the filming sessions affected, and what was decided.

| Case | First move | Questions that settle it |
|---|---|---|
| Damaged on arrival | The receiving person reports through the arrival check with photographs, the same day. | Was the outer box damaged? The carrier's claim window may depend on it. |
| Fault found at the setup call | Replace or repair the item before T. | Is a spare in the box, or does a replacement ship, and does T move? |
| Fails during filming | The client stops and reports. The session moves, not the kit to a shop. | Is a backup category covering it, and does the session need rescheduling? |
| Damaged in use | Treat as damage, not as a breach. Record, photograph, return as usual. | Who bears the repair cost? Open. |
| Lost or damaged on the return | Open a carrier claim and an insurer claim, using the outbound and return photographs. | Which of the two pays first? Open. |
| Not returned | The kit owner contacts the receiving person after the return window and finds out what happened. | At what point, and with what remedy, is the kit declared lost? Open. |
| Cards or footage lost | The most serious case. Tell the client first and at once. | Is a copy elsewhere, and what does the client decide? Open. |

The first move in every case is to find out what happened, which follows the practice's anti-extractive posture. Who bears the cost is not decided here; each "Open" above is listed under [Open parameters](#open-parameters).

## Open parameters

These are not decided. None is a statement of current practice.

- **T and every offset.** The offsets (21, 18, 14, 12, 10, 7, 5 days before T; 2 days after the last session) are placeholders until real transit times exist.
- **Spares per category.** The categories are listed; the counts are not set.
- **Carrier.** Not chosen; the questions are in [Carrier](#carrier).
- **Insurance.** Every decision in [Insurance](#insurance) is open, including who carries the risk while the kit is with the client.
- **Damage, loss and non-return.** Who bears repair or replacement cost, when a kit counts as lost, and any deposit or agreement are open. The repo states no legal terms and this document adds none.
- **Whether a client has completed the cycle.** The blueprint says none had as of April 2026. This document does not record a later answer.
