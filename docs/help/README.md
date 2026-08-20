# ChillMind help articles

These markdown files are the **grounding corpus** for the AI answers feature. The
route answers support questions using only what is written here.

- Editing an article changes what the assistant will say. Treat these as product copy.
- Every file needs frontmatter with a unique `id`, a `title`, and `tags`.
- `id` values appear in answers as citations, e.g. `[breathing-streaks]`, so keep them stable.
- Run `npm run build:kb` in `web/` after editing; it regenerates `web/lib/kb-data.ts`,
  which is committed so the corpus is reviewable in a diff.
- If a topic is not covered here, the assistant deliberately declines and offers a
  human handoff instead of guessing. Adding coverage is how you improve deflection.
