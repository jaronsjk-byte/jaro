# Amaizen-Inspired Jaro Recovery Plan

## Goal
Recover and improve the user-facing capabilities of the hosted Amaizen reference inside the existing Jaro/ChillMind codebase. Amaizen's original source code is unavailable, so this is a feature reconstruction and quality-improvement effort, not a source-code restoration or a line-for-line clone.

## Non-negotiable constraints
- Preserve the existing Streamlit app in `proo.py`, its current user flows, and the Next.js app under `web/`.
- Work on a feature branch; do not replace `main` wholesale.
- Audit each module before editing it. Keep existing working behavior and add regression tests for changes.
- Do not present generated, random, or placeholder values as real user analytics.
- Keep API keys in environment variables. Never commit secrets or real user wellness data.
- Clearly label wellness content as informational, not diagnosis or emergency care.

## Existing module inventory to verify against the current code
- Account / login and profile
- Dashboard and wellbeing overview
- Mood and self-check-in / survey
- AI mentor or chat
- Breathing exercises and session history
- Focus drills and mini-games (memory, word jumble/word chain, tic-tac-toe, number focus)
- Daily goals and task completion
- Schedule and calendar
- Notifications and notification preferences
- Analytics, streaks, achievements and progress history
- Theme and accessibility preferences
- Help / FAQ and privacy information
- Next.js help/answer desk and its API

This list is based on code-level indicators and must be checked against actual navigation, handlers, persistence and tests before claiming any module is complete.

## Required audit for every module
1. **Discoverability:** Is it visible in navigation and can users reach it on desktop and mobile?
2. **Functionality:** Do every primary button, form, filter, save, reset, export and navigation action actually work?
3. **Persistence:** Does data survive reruns, refreshes and sign-in/sign-out appropriately? Is ownership scoped to the correct user?
4. **Truthfulness:** Are charts and streaks calculated from real persisted events rather than random/demo/session-only values?
5. **Failure states:** Are empty, loading, offline, invalid-input, API-limit and server-error states understandable?
6. **Accessibility:** Keyboard navigation, focus indicators, contrast, readable type, reduced motion and labels.
7. **Security/privacy:** Authentication, authorization, input validation, secrets, logging, data deletion and safe AI behavior.
8. **Quality gates:** Automated tests, typecheck, production build and deployment configuration.

## Implementation order
### P0 — Protect the current project
- Capture the current baseline and run existing tests/build checks.
- Confirm the correct deployment root and required environment variables.
- Identify and fix security or startup failures without exposing secrets.

### P1 — Module recovery
- Map each visible navigation item to its implementation and mark it: working, partial, broken, or missing.
- Repair broken modules before introducing new features.
- Implement missing core actions using the existing project architecture.

### P2 — Data integrity
- Use durable storage for user-owned tasks, mood check-ins, schedules, breathing sessions and preferences.
- Add schema/version handling and safe error paths before migrating existing data.
- Derive analytics from recorded events; display honest empty states when history is unavailable.

### P3 — Product-quality UI
- Establish shared spacing, type, colors, surfaces, buttons, forms, charts and feedback patterns.
- Make layouts responsive and navigation usable on small screens.
- Improve hierarchy and reduce excessive visual effects; support reduced motion and strong contrast.
- Keep the Streamlit and Next.js experiences visually related without pretending they are one integrated app unless the architecture supports that.

### P4 — Verification and release
- Add or update tests for each repaired module.
- Run Python tests, JavaScript tests, TypeScript checks and production builds where available.
- Check deployment environment variables and deployment logs.
- Review the diff for regressions and secrets, then open a reviewable PR. Do not merge or deploy until checks pass.

## Completion criteria
- Every existing module has a documented status and a working route/action or a clear reason it is unavailable.
- No existing module is removed merely to simplify the redesign.
- Core user data persists correctly and analytics are based on actual records.
- No dead primary buttons or unexplained placeholder states remain.
- Desktop/mobile layouts, accessibility basics, tests and production build are checked.
- Release notes state what was tested and what still needs live-deployment verification.

## Known limitation
The hosted reference URL does not expose its original source code and could not be reliably inspected during the initial pass. Therefore, the implementation should use the current Jaro code as the source of truth and the visible Amaizen experience as a reference when accessible. Do not claim exact parity without a screen-by-screen comparison.
