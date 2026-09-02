---
name: clarify-ticket
description: Use before implementing an Odoo board task — picks the right ticket, reads it fully, discovers answers from the ticket and repo, then asks the user only the blocking questions whose answers would change the implementation. Ends with a written interpretation and readiness verdict, not code.
---

# Clarify a ticket before building it

The point is to remove ambiguity that would cause rework — **not** to ask questions for their own
sake, and **not** to start coding an underspecified ticket. Odoo is the source of truth for the
ticket's current requirements and status. When you can find the answer in the ticket, the repo, or
established project conventions, find it yourself and record it as an assumption — do not ask.

## 1. Pick a ticket

1. `my_tasks()` — tickets assigned to you, soonest deadline first.
2. Choose an open one — ToDo / In Progress. Skip Done, cancelled, or blocked tickets unless the
   user names one explicitly.
3. Tie-break in this order: highest priority → earliest deadline → oldest. State which ticket you
   picked and why in one line.

## 2. Read the whole ticket

`task(<id>)` and read all of it before forming questions:

- Title, description, **acceptance criterion** (the description *is* the criterion on this board)
- Priority, labels/tags, deadline
- Comments / chatter and any notes
- Reference links — the week-task file and the PRD (PDFs). Read them.
- Attachments, related tasks, existing implementation references

Then inspect the repo where it matters: the modules, APIs, schemas, config, and gate tests the
ticket touches. Match existing conventions rather than inventing new ones.

## 3. Understand it

Answer for yourself, from what you just read:

- The real business requirement and who it is for
- Success behavior and failure behavior
- Inputs and outputs
- Which existing services, tables, UI, or gate commands are involved
- What assumptions an implementation would have to make

## 4. Find the ambiguities that matter

Scan for: missing acceptance criteria, undefined edge cases, unclear business rules, missing
validation, unclear permissions, unclear error behavior, unclear API contracts, missing DB
requirements, unclear UI/UX, conflicting requirements, cross-ticket dependencies, security/privacy
concerns, performance/scale needs, and any point where several reasonable implementations diverge.

Keep only the ambiguities that (a) you could not resolve from the ticket, repo, or conventions and
(b) would materially change what you build. Everything else is a non-blocking assumption you will
record, not a question.

## 5. Ask — only the blocking questions

Ask with `AskUserQuestion` (or a numbered list if the tool is unavailable). For each question:
briefly say why it matters, give concrete options, and recommend the most sensible one.

> **1. What happens on a duplicate intake for an existing evidence id?**
> Affects the API response and the DB constraint.
> **A.** Reject (409) · **B.** Replace · **C.** Keep both versions
> **Recommendation:** A, unless versioning is explicitly required.

`note(<id>, "...")` the open questions on the ticket so the ambiguity is visible on the board, not
just in this chat.

## 6. Wait, then restate

Do not implement until the blocking questions are answered. After answers:

- Restate the final interpretation of the requirements.
- List assumptions that remain.
- Name the implementation approach.
- Ask for confirmation only if a significant ambiguity still stands.

## 7. Readiness output

When requirements are clear enough, print exactly this block:

```text
Ticket:
<ticket name>

Understanding:
<what needs to be built>

Decisions:
- ...

Remaining assumptions:
- ...

Implementation plan:
1. ...

Files/components likely affected:
- ...

Ready to implement: YES
```

If the ticket was already sufficiently specified, say so plainly and go straight to the readiness
block — no invented questions. Never invent business requirements; if a required fact is genuinely
undecidable and could cause rework, ask before coding.

## Guardrails

- Blocking question (could cause rework) vs non-blocking assumption (record and proceed) — keep them
  distinct.
- `start(<id>)` belongs to the [[board]] build flow, not here. This skill ends at a plan; it does
  not write code or claim numbers.
- Never read `evals/heldout/` if you are the Builder this week — it is sealed.
