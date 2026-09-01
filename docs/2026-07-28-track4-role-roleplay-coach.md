# PRD — Track 4: ROLE (Interactive AI Roleplay & Skills Coach)

**Project code:** `ROLE`
**Date:** 2026-07-28
**Status:** draft — **backlog** (Track 1 M2X is the active build)
**Owners:** TBD · **Shashank** (advisory)

← [[../context|Course Context]] · [[../meetings/2026-07-28-course-scope-alignment|scope call]] · active track: [[2026-07-28-track1-m2x-meeting-to-execution|M2X PRD]]

---

## 1. Product brief

**User.** Sales, customer success, support, HR, recruiters, managers, compliance — anyone who
must practise a hard conversation before having it for real.

**Problem.** Skills practice needs a partner and a coach. Both are scarce, inconsistent, and
expensive. People rehearse on real customers instead.

**Inputs.** Live voice/text sessions, company playbooks, product knowledge, scenario settings,
role competencies.

**Outputs.** A realistic AI counterpart conversation + an **evidence-linked scorecard**
against transparent rubrics + an individual learning plan.

**Workflow.**
`scenario + persona policy → live session → company RAG → adaptive controller
→ evaluator agents → evidence-linked feedback → learning plan → trainer review`

**Data boundaries.** Voluntary participation with explicit consent. Session recordings are
the learner's data; scores are **not** shared with managers without the learner's approval.
Retention limit agreed before pilot.

**Prohibited actions (hard line).** No personality inference. No emotion, mental-state, or
biometric claims. No hidden or pseudo-scientific scoring. Every score must cite a transcript
segment or a measurable event, or it is not reported. No use in hiring, appraisal, or
disciplinary decisions in v1.

## 2. Scope decisions

**In scope** — one role, three scenarios (recommend: sales discovery, complaint handling,
manager feedback). Text-first with optional voice. Transparent observable rubrics only.

**Out of scope (v1)** — avatars/video personas, emotion or sentiment scoring, LMS
integration, manager-facing dashboards, any performance-management use.

**Infra note (constraint check).** Text-first mode is fully local-friendly. Voice adds hosted
STT/TTS spend; **avatar/video is explicitly deferred** — heaviest component, least learning
value per rupee.

**Ethics note.** This is the highest-risk track. Shashank's source doc already flags it:
scoring must stay transparent, evidence-based, consented, and reviewable. Treat that as a
build requirement, not advice.

## 3. Architecture (target)

```
scenario config + persona policy -> conversation session (text or voice)
                     |                          |
              company RAG (playbooks)    scenario controller (difficulty, state)
                                                |
                        observer -> evaluator agents -> evidence-linked scorecard
                                                |
                                   learning plan -> trainer review/override
```

**Stack:** Python · FastAPI · optional hosted STT/TTS · Qdrant/Chroma · Pydantic +
Instructor (scorecard schemas) · RAGAS · LangGraph (multi-agent + state) · Langfuse · Docker.

## 4. Phased plan (trimmed)

| Phase | Build | Exit gate | Est. |
|---|---|---|---|
| **0 Conversational literacy** | Text-first roleplay with one fixed persona; optional STT/TTS so the same scenario runs by voice | One consistent scenario completes end-to-end; session logged with model + cost metadata | 2–3 d |
| **1 Persona playground** | Compare personas, difficulty levels, emotional states, response lengths, conversation policies | Persona, scenario goal, and difficulty hold across ≥20 turns in 5 test sessions | 4–5 d |
| **1B Scenario prompts + scoring** | Versioned scenario library + eval harness with observable rubrics (question coverage, listening ratio, factual accuracy, policy adherence, objection handling, clarity) | All scorecards schema-valid; **every score evidence-linked**; acceptable agreement with human reference on 10 labelled conversations | 1 wk |
| **2 RAG-grounded scenarios** | Ground both the persona and the evaluator in playbooks, product docs, policy | Faithfulness ≥0.80; unsupported learner claims correctly flagged in ≥90% of test cases | 1 wk |
| **3 Tool-calling coach** | Tools for scenario generation, product lookup, session scoring, report generation | ≥0.90 tool-call accuracy; **blocks unapproved disclosure** of individual performance data | 4–5 d |
| **4 Adaptive multi-agent** | Persona agent + scenario controller + observer + evaluator + coach. Difficulty adapts to performance; controller enforces scenario rules | ≥15% improvement in rubric coverage or learner progression vs fixed baseline, with reproducible scenario rules | 1 wk |
| **5 Presentation analysis** *(optional)* | Analyse an uploaded presentation for content coverage, slide use, pacing — **content and structure only** | Produces useful content feedback; **zero** personality/emotion/biometric output | 3–4 d |
| **6 Capstone** | Deployed platform: multi-role scenarios, learner history, trainer controls with score override + notes, consent, privacy, tracing | Passes universal gate (see M2X §6) **plus** a fairness audit across accents/languages/speaking styles | 1 wk |

**Total: ~6–7 weeks part-time** (Phase 5 optional; drop first when trimming).

## 5. Pilot

Three scenarios, one role, small **voluntary** group. Measure: repeat-practice rate,
self-reported confidence, rubric progression across sessions, trainer agreement with scores.

## 6. Open questions

1. Which role + three scenarios for v1?
2. Who owns rubric design — and who is the reviewing trainer?
3. Consent + data-retention policy for practice sessions: who signs off?
4. Text-first only for v1, or is voice in from the start?
5. Confirm: scores never feed appraisal. Agreed as a permanent boundary or v1-only?
