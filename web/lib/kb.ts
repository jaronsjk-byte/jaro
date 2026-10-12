import { ARTICLES } from "./kb-data";

/**
 * Retrieval over ChillMind's own help articles.
 *
 * Deliberately dependency-free: the corpus is ~14 short documents, so a
 * TF-IDF-ish keyword score beats the operational cost of an embedding service.
 * The corpus is generated from docs/help/*.md by scripts/build-kb.mjs, so the
 * grounding source stays reviewable in pull requests.
 */

export interface Article {
  id: string;
  title: string;
  tags: string[];
  body: string;
}

const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on",
  "how", "do", "i", "my", "can", "what", "where", "does", "with", "you",
  "me", "be", "this", "that", "at", "as", "are", "if", "get", "there",
]);

export function tokenize(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter(
    (t) => t.length > 2 && !STOPWORDS.has(t),
  );
}

function docFrequencies(articles: Article[]): Map<string, number> {
  const df = new Map<string, number>();
  for (const a of articles) {
    for (const term of new Set(tokenize(`${a.title} ${a.tags.join(" ")} ${a.body}`))) {
      df.set(term, (df.get(term) ?? 0) + 1);
    }
  }
  return df;
}

export interface RetrievedArticle extends Article {
  score: number;
}

/**
 * Returns the top-k articles above a relevance floor. An empty result is a
 * meaningful signal: the route must then decline and offer a human handoff
 * rather than letting the model improvise.
 */
export function retrieve(
  question: string,
  k = 4,
  corpus: Article[] = ARTICLES,
): RetrievedArticle[] {
  const qTerms = tokenize(question);
  if (qTerms.length === 0 || corpus.length === 0) return [];

  const df = docFrequencies(corpus);
  const N = corpus.length;

  const scored = corpus.map((a) => {
    const haystack = tokenize(`${a.title} ${a.title} ${a.tags.join(" ")} ${a.tags.join(" ")} ${a.body}`);
    const counts = new Map<string, number>();
    for (const t of haystack) counts.set(t, (counts.get(t) ?? 0) + 1);

    let score = 0;
    for (const term of qTerms) {
      const tf = counts.get(term);
      if (!tf) continue;
      const idf = Math.log(1 + N / (1 + (df.get(term) ?? 0)));
      score += (tf / haystack.length) * idf * 100;
    }
    return { ...a, score };
  });

  return scored
    .filter((a) => a.score > 0.15)
    .sort((a, b) => b.score - a.score)
    .slice(0, k);
}

/** Renders retrieved articles into the grounding block sent as instructions. */
export function renderContext(articles: RetrievedArticle[]): string {
  return articles
    .map((a) => `### [${a.id}] ${a.title}\n${a.body.trim()}`)
    .join("\n\n");
}
