# MVP — browser-auto

**Version:** 0.1
**Last updated:** 2026-03-06
**Status:** Draft for pilot validation

---

## 1. MVP Goal

Launch browser-auto to the market in the smallest form that can validate real demand.

This MVP is not "a complete documentation platform".
This MVP is:

**A tool that generates step-by-step documentation with screenshots for a primary authenticated SaaS flow.**

The goal is to validate:

- whether teams want to replace manual screenshot documentation
- whether the generated output is useful enough to publish or edit lightly
- whether users trust the agent enough to run it repeatedly

---

## 2. Initial ICP

Initial target customer profile:

- small SaaS teams
- indie hackers
- startup product teams without a dedicated technical writer

Ideal user inside the team:

- founder
- PM
- developer

These users need documentation, but do not want to spend hours capturing screens and writing step-by-step guides.

---

## 3. Initial Use Case

The MVP supports one narrow, high-value use case:

**Generate user documentation for a core web-app flow after login.**

Examples:

- login flow
- onboarding flow
- account settings flow
- dashboard navigation
- simple CRUD flow

This is the wedge.
The MVP should not try to support every browser workflow from day one.

---

## 4. MVP Value Proposition

Given a URL and a documentation goal, browser-auto should:

1. open the app
2. authenticate with cookies or username/password
3. navigate the main flow
4. capture screenshots for each step
5. generate a Markdown guide that is ready to review and publish

Core promise:

**From product flow to usable documentation in minutes.**

---

## 5. In Scope

These capabilities are required for the MVP:

### 5.1 Run execution

- accept `goal + url`
- run planner, executor, and critic end to end
- produce documentation from a single run

### 5.2 Authentication

- session cookies via file
- username/password login for common forms

This is mandatory because most target SaaS apps are authenticated.

### 5.3 Output

- Markdown document
- screenshot per step
- readable H1 title
- language consistency in generated content
- portable output on disk

### 5.4 Reliability baseline

- post-action verification
- retry per step
- fallback strategies
- checkpoint and resume
- visible failure reporting

### 5.5 Usability

- local CLI as the primary interface
- simple setup instructions
- basic API available for assisted/internal usage

---

## 6. Out of Scope for MVP

These items are explicitly not required to validate the first market version:

- multi-tenant SaaS platform
- team collaboration
- billing and subscription tiers
- polished web dashboard
- scheduled runs
- webhooks
- destination integrations like Notion or Confluence
- Docker packaging polish
- custom output templates
- MFA / TOTP
- multi-tab flows
- cross-origin iframe support
- "supports any website" positioning

If any of these block launch, the launch scope is too broad.

---

## 7. Product Positioning for Launch

browser-auto should be positioned narrowly.

Recommended positioning:

**Automatically generate step-by-step documentation with screenshots for core SaaS product flows.**

Avoid launch messaging like:

- "documentation for any web app"
- "fully autonomous browser agent for every workflow"
- "enterprise documentation platform"

The MVP should sell one sharp promise, not the full long-term vision.

---

## 8. Definition of "Ready for Pilot"

browser-auto is ready for pilot validation when all of the following are true:

### Product

- can document a real authenticated flow with 5 to 10 steps
- output is useful without heavy rewriting
- run completes in a reasonable time
- failure rate is low enough for repeated real usage

### Reliability

- the core test suite is green
- API and CLI setup are reproducible
- common failures are visible and understandable

### User experience

- installation/setup can be followed by a non-author user
- a user can provide goal, URL, and auth inputs without developer intervention
- generated files are easy to inspect and share

### Validation

- at least 3 design partners can run a real workflow
- at least 1 recurring workflow exists per partner
- users say the result saves meaningful time over manual documentation

---

## 9. Pilot Success Metrics

The MVP should be evaluated with simple market-facing metrics:

- time to first usable doc
- percent of runs that complete without manual recovery
- percent of output that can be published with light edits only
- number of repeated runs per pilot account
- qualitative willingness to pay or continue using

Suggested initial targets:

- first usable doc in under 5 minutes for a 5 to 10 step flow
- low enough failure rate to demo repeatedly
- at least 3 pilot users complete real runs
- at least 1 pilot user asks to keep using it after the trial

---

## 10. Current Repo vs MVP

The current repository already covers most of the technical MVP:

- CLI exists
- agentic loop exists
- auth basics exist
- Markdown + screenshots output exists
- retries and checkpointing exist
- API scaffold exists

The main remaining gap is not feature breadth.
It is product readiness:

- stability
- narrower positioning
- easier onboarding
- repeatable pilot usage

---

## 11. Immediate Next Steps

To move from technical MVP to market MVP:

1. stabilize API and test stack
2. define the exact launch ICP and use case
3. create a simple onboarding path for pilot users
4. validate the product on 3 to 5 real SaaS apps
5. collect feedback on output quality, reliability, and willingness to adopt

---

## 12. One-Sentence MVP

**browser-auto generates publishable step-by-step documentation with screenshots for a core authenticated SaaS flow using a simple goal and URL.**
