# The booth story

What to say at the booth, in plain words. `STORY_RUNBOOK.md` has the beats, endpoints and timings; this file is the narrative that sits on top of them.

## The one line

> Atelic sees the leak, finds the source, measures it the way an expert would (camera first, checked by the physics) and keeps going when a sensor fails. The engineer stays in charge throughout.

## The setting

An unmanned gas gathering station in open desert, Eastern Province, Saudi Arabia. It is 23:00. Nobody is on site. The station has infrared cameras, methane sensors around the equipment skid and a weather mast with a sonic anemometer. Atelic's agents watch all of it, around the clock.

Why it matters: satellites catch super-emitters of a thousand kg/h and more. The leak we are about to find is 0.28 kg/h, about 3,700 times smaller than the regional plume on the overview screen. Leaks this size add up across thousands of sites and only on-site sensing sees them.

## Opening: the satellite view (beat 00, about 30 s)

The overview screen shows a real Carbon Mapper plume: Tanager-1, 4 November 2025, oil and gas, inland in the Eastern Province, about 1,040 kg/h, seen on 1 of 15 passes. It is a real detection in the same region as our (anonymous) station, but not our station; keep the label visible.

Use it to set up the problem, not to show off the image. Make three points:

1. **Scale.** That plume is about 1,040 kg/h. Our leak is 0.28 kg/h, about 3,700 times smaller. Satellites find the super-emitters; they cannot see leaks this size.
2. **Timing.** It was seen on 1 of 15 passes. A satellite takes a snapshot every few days; leaks start and stop in between, and fourteen passes saw nothing. On-site sensors watch every second.
3. **Night.** Satellite methane imaging needs reflected sunlight. Our incident happens at 23:00, when no satellite can see it at all.

*Say:* "This is what space sees: a real plume in this region, over a tonne an hour, caught on one pass out of fifteen. It's essential for finding the giants. The leak we're about to find is nearly four thousand times smaller, and it starts at eleven at night, when satellites are blind. That's the gap on-site sensing and Atelic fill."

Then cut to the station. The bridge is "satellites tell you which region to worry about; Atelic tells you which valve, how much, and what to do about it."

Don'ts:
- Don't imply the plume comes from our station or that Atelic detected it.
- Don't knock satellites. They are the first layer of a tiered system, not the competition.
- Don't name the operator or the exact facility behind the plume.

## 60-second version

> "This is a gas station in the Saudi desert at eleven at night, with nobody there. A camera picks up a small plume at a valve, XV-2104. Atelic raises an incident straight away.
>
> The agent links the camera, the methane sensors and the wind, and narrows the source down to that valve. Then it measures the leak. It reads the rate straight from the plume in the camera: about a quarter of a kilo an hour. Then it checks that number against four independent physics models: plume, particle, Bayesian and a flow solver around the equipment. They all agree. The Bayesian model also says this valve is the only thing leaking.
>
> Then it digs into the history. The valve was 'fixed' six weeks ago, but the follow-up survey never happened and the leak came back ten days later. It drafts a repair plan with every claim cited. The engineer questions it, pushes back, approves it, records the repair, and the agent confirms the readings are back to normal before a human closes the incident.
>
> And when things go wrong, here the weather mast goes down, the agent doesn't stop. The sensor models need wind, so it says so and measures from the camera alone."

## Three-minute walk-through

### 1. Calm (beat 0–1)
The cameras are live and nothing is wrong. A real 5 g/h release is running that the camera cannot see, and Atelic does not raise false alarms.
*Say:* "Quiet is the normal state. No alert fatigue."

### 2. Alert (beat 2)
The release steps up and the detector latches within seconds. INC-001 opens on the same footage, which keeps playing.
*Say:* "Seconds from plume to incident, at night, with nobody on site."

### 3. Locate (beat 3)
Two candidates sit in the camera's view: valve XV-2104 and flange F-205. Neither is confirmed yet.
*Say:* "The camera tells us where to look. The sensors and the history will tell us which one."

### 4. Quantify: the heart of the story (beat 4)
Open the Quantify page.

- **Camera first.** The agent reads the rate from the plume itself (QOGI: plume mass times plume speed, both measured in the infrared frames). It waits for a steady 90-second window: **0.24 kg/h**.
- **The physics checks it.** The agent reads the case (steady wind, stable night air from the sonic, one likely source) and picks which models are worth running:
  - Gaussian plume: 0.28
  - MDLQ (puff + Bayesian): 0.28, with XV-2104 the only source leaking (probability 1.0, every other source ≤0.002)
  - bLs (particle model using the sonic's turbulence): 0.31
  - CFD + inversion (airflow solved around the separator and compressor): 0.30
- **All agree** within 0.67–1.5× of the camera.
- **The engineer can run any tool.** Click a row on the rate chart and it runs on the same frozen window.

*Say:* "One number is a claim. A camera measurement that four independent physics models agree with is something an operator can act on and a regulator can accept. And the agent chose these tools for this case. It explains why each one runs, waits or is not needed."

### 5. Investigate and plan (beats 4b–6)
The 47-day history shows faint episodes from mid-September, ten quiet days after a re-torque work order (WO-009), then a return from 6 October that keeps growing. The maintenance records show WO-009 marked "verified", but the follow-up camera survey (WO-008) never happened. The agent drafts a repair plan with every claim cited.
*Say:* "It does what a good engineer does: it checks whether the last fix actually worked."

### 6. The engineer in charge (beats 7–9)
The engineer asks why XV-2104 still leads despite the earlier fix and gets a cited answer. They push back, the agent revises to draft v2, and the engineer approves it and issues the report.
*Say:* "Agents never approve, close or touch equipment. A named engineer does, and the audit trail records it."

### 7. Answer key (beat 9b)
Only now do we show the metered rate: **277.7 g/h**. The camera read 243 (−12.5%).
*Say:* "We hid this until the review was done, so it couldn't anchor anyone."

### 8. Repair, verify, close (beats 10–12)
The engineer records the repair. The agent checks that the readings are back to baseline and recommends closing. The engineer closes it, and the incident is written to the asset's memory, so next time the agent knows this valve's history.

### 9. When a sensor fails (beat 13 / INC-002)
A bigger leak, about 0.7 kg/h rising to 0.96, and the weather mast stops reporting. Every sensor model needs wind, so the agent blocks them with a stated reason and relies on the camera: **≈1.06 kg/h** (+11% against the metered rate).
*Say:* "This is judgement. It doesn't crash and it doesn't guess. It knows which tool still works and tells you why."

## What the rate tools add, in one breath

| Without them | With them |
|---|---|
| A single number from one method | A camera number cross-checked by independent physics |
| A dashboard | An agent that reads the case and picks its tools |
| "Rate unavailable" when a sensor drops | A reasoned fallback to the camera |
| Trust the model | The engineer can run any tool and see for themselves |
| "Something is leaking" | "This valve is leaking, nothing else is" (MDLQ) |

## Hard questions and honest answers

**"Which tool is the most accurate?"**
Don't rank them. In this demo the sensor readings are synthetic and generated with a Gaussian model, so the Gaussian, puff and MDLQ tools match almost exactly by construction. In the field, bLs is usually the most accurate for one known source with sensors nearby (often within ±10–30% in controlled-release trials). Bayesian methods such as MDLQ are best at saying which source leaks. Camera quantification is typically ±30–50%; its strength is needing no anemometer. That is why the agent cross-checks rather than trusting any single tool.

**"Is this real data?"**
The camera footage is real: a METEC controlled release from the GasVid dataset with a published metered rate. The plant, its sensors, its history and the repair are synthetic, and the plant releases the same rate as the footage. The history's wind and background variability come from measured METEC data. The footage was shot by day; a thermal camera images it the same way at night.

**"How is the camera calibrated?"**
Each camera's gain comes from a metered reference release it recorded. For CAM-01 that is the first 40 s of the same release the incident measures. We say so up front.

**"Is MDLQ the published tool?"**
It is our own Python implementation after Daniels et al. (2026): Gaussian puffs plus a spike-and-slab Bayesian model. It is not the authors' R code.

**"Is that real CFD?"**
It is a fast diagnostic solver: a mass-consistent wind field around the equipment and 3-D advection-diffusion on a 23,000-cell grid. It is not full RANS or LES, and it runs in about a second per candidate.

**"Where does it run?"**
The agents run on hosted NVIDIA Nemotron and the demo runs in the cloud (AWS). Don't claim Dell, edge or on-site hardware.

## Don't say

- That one rate tool is "the most accurate".
- That anything runs on Dell, edge or on-site hardware.
- "Failed": say "waiting", "not needed" or "blocked because…".
- That the plant or its sensors are real.
- That the agent approves, repairs or closes anything.
