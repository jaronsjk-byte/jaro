"use client";

import { FormEvent, useState } from "react";

const suggestions = [
  "How can I reset my study streak?",
  "Where can I manage notifications?",
  "How do I plan a calmer study week?",
];

type Citation = { id?: string; title?: string };

export default function Home() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [asked, setAsked] = useState(false);

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = question.trim();
    if (!clean || loading) return;
    setLoading(true); setAnswer(""); setCitations([]); setNotice(""); setAsked(true);
    try {
      let contextId = "anonymous";
      try {
        contextId = window.localStorage.getItem("chillmind-context") || window.crypto?.randomUUID?.() || `cm-${Date.now().toString(36)}`;
        window.localStorage.setItem("chillmind-context", contextId);
      } catch { /* Storage can be unavailable in private browsing. */ }
      const response = await fetch("/api/answers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: clean, contextId }),
      });
      if (!response.ok) {
        if (response.status === 404) setNotice("AI answers are not enabled for this deployment yet. Your question has not been sent anywhere else.");
        else if (response.status === 429) setNotice("You’ve reached the current question limit. Please try again a little later.");
        else {
          const body = await response.json().catch(() => ({}));
          setNotice(body.message || "We couldn’t get an answer right now. Please try again.");
        }
        return;
      }
      if (!response.body) throw new Error("Missing response stream");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let fullText = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const chunk of events) {
          const line = chunk.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const data = JSON.parse(line.slice(6));
          if (data.type === "token") { fullText += data.text || ""; setAnswer(fullText); }
          if (data.type === "citations") setCitations(data.items || []);
        }
      }
      if (!fullText) setNotice("No answer came back. Please try asking in a different way.");
    } catch {
      setNotice("Connection interrupted. Check your connection and try again.");
    } finally { setLoading(false); }
  }

  return (
    <main className="site-shell">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <header className="topbar">
        <a className="brand" href="#" aria-label="ChillMind home"><span className="brand-mark"><span /></span><span>chillmind<span className="brand-dot">.</span></span></a>
        <nav className="top-nav" aria-label="Main navigation"><a href="#how-it-works">How it works</a><a href="#principles">Our approach</a></nav>
        <a className="nav-cta" href="#ask">Ask a question <span aria-hidden="true">↗</span></a>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow"><span className="status-dot" /> A little more clarity, one question at a time</div>
          <h1>Your mind has<br />enough <em>tabs</em> open.</h1>
          <p className="hero-description">A calmer way to find answers about your ChillMind experience. No noise, no judgement — just useful guidance when you need it.</p>
          <div className="hero-actions"><a className="button button-primary" href="#ask">Find an answer <span aria-hidden="true">↗</span></a><a className="text-link" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a></div>
          <div className="hero-proof"><div className="proof-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3 19 6v5c0 4.5-3 7.7-7 10-4-2.3-7-5.5-7-10V6l7-3Z"/><path d="m9 12 2 2 4-4"/></svg></div><div><strong>Grounded in our help guides</strong><span>Built to be helpful, not to guess.</span></div></div>
        </div>
        <div className="hero-art" aria-label="Abstract illustration of a calm, organised mind" role="img">
          <div className="art-orbit orbit-a" /><div className="art-orbit orbit-b" />
          <div className="art-sun"><span className="sun-core" /><span className="sun-ray ray-one" /><span className="sun-ray ray-two" /><span className="sun-ray ray-three" /></div>
          <div className="floating-note note-top"><span className="note-icon lavender">✳</span><span><b>One thing at a time</b><small>Make room to think</small></span></div>
          <div className="floating-note note-bottom"><span className="note-icon peach">↗</span><span><b>Small steps count</b><small>Progress over pressure</small></span></div>
          <div className="art-caption"><span>01 / A CLEARER HEADSPACE</span><span className="caption-line" /></div>
        </div>
      </section>

      <section className="ask-section" id="ask">
        <div className="section-kicker">THE ANSWER DESK <span>—</span> ALWAYS START WITH A QUESTION</div>
        <div className="ask-heading-row"><div><h2>What’s on your mind?</h2><p>Ask about features, settings, or finding your way around ChillMind.</p></div><div className="privacy-note"><span className="lock-icon">⌑</span> Avoid sharing personal details</div></div>
        <form className="question-box" onSubmit={askQuestion}>
          <label className="sr-only" htmlFor="question">Your question</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Try “How do I organise my week?”" maxLength={2000} rows={3} />
          <div className="question-footer"><span className="input-hint">Be as specific or as simple as you like.</span><button className="button button-primary ask-button" type="submit" disabled={!question.trim() || loading}>{loading ? <><span className="button-spinner" /> Thinking…</> : <>Get guidance <span aria-hidden="true">↗</span></>}</button></div>
        </form>
        <div className="suggestions"><span className="suggestion-label">NEED A STARTING POINT?</span>{suggestions.map((item) => <button key={item} type="button" className="prompt-chip" onClick={() => { setQuestion(item); document.getElementById("question")?.focus(); }}>{item}<span aria-hidden="true">↗</span></button>)}</div>
        {asked && (answer || notice || loading) && <div className="answer-panel" aria-live="polite">
          <div className="answer-panel-head"><span className="answer-symbol">✳</span><div><strong>Your guidance</strong><span>{loading ? "Putting the pieces together…" : "A response from ChillMind"}</span></div><span className="answer-status">{loading ? "WORKING" : notice ? "NOTICE" : "READY"}</span></div>
          {answer && <p className="answer-text">{answer}</p>}{notice && <p className="notice-text">{notice}</p>}
          {citations.length > 0 && <div className="citation-list"><span>RELATED HELP GUIDES</span>{citations.map((item, index) => <span key={item.id || index} className="citation-pill">{item.title || "ChillMind help guide"}</span>)}</div>}
        </div>}
      </section>

      <section className="how-section" id="how-it-works">
        <div className="section-intro"><div className="section-kicker">SIMPLE BY DESIGN</div><h2>Less searching.<br /><em>More understanding.</em></h2></div>
        <div className="steps-grid">
          <article className="step-card"><span className="step-number">01</span><div className="step-icon icon-question">?</div><h3>Ask naturally</h3><p>No special commands or perfect wording. Just tell us what you’re trying to figure out.</p><span className="step-foot">START WHERE YOU ARE <span>↗</span></span></article>
          <article className="step-card step-featured"><span className="step-number">02</span><div className="step-icon icon-spark">✳</div><h3>Get grounded guidance</h3><p>Answers are based on the available ChillMind help guides — not made-up product details.</p><span className="step-foot">BUILT AROUND CONTEXT <span>↗</span></span></article>
          <article className="step-card"><span className="step-number">03</span><div className="step-icon icon-check">✓</div><h3>Take your next step</h3><p>Find the setting, understand the feature, and get back to what matters to you.</p><span className="step-foot">ONE STEP AT A TIME <span>↗</span></span></article>
        </div>
      </section>

      <section className="principles-section" id="principles">
        <div className="principle-orb" aria-hidden="true"><span>cm</span><i /><i /><i /></div>
        <div className="principle-copy"><div className="section-kicker">A DIFFERENT KIND OF HELPER</div><h2>Helpful should feel<br /><em>human, not overwhelming.</em></h2><p>ChillMind is designed around small, practical steps. This answer desk focuses on product guidance, and it hands off when the available information isn’t enough.</p><div className="principle-tags"><span>Clear by default</span><span>Honest about limits</span><span>Made for real life</span></div></div>
      </section>
      <footer className="footer"><a className="brand" href="#" aria-label="ChillMind home"><span className="brand-mark"><span /></span><span>chillmind<span className="brand-dot">.</span></span></a><span className="footer-note">A little more room to breathe.</span><a className="back-top" href="#">Back to top ↑</a></footer>
    </main>
  );
}
