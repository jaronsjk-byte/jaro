"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import styles from "./lab.module.css";

type LabStatus = "Planned" | "In progress" | "Completed";
type LabEntry = {
  id: string;
  title: string;
  subject: string;
  summary: string;
  status: LabStatus;
  createdAt: string;
};

const STORAGE_KEY = "jaro-lab-entries-v1";
const initialLabs: LabEntry[] = [
  { id: "starter-python", title: "Python fundamentals", subject: "Python", summary: "Variables, conditions, loops and small problem-solving exercises.", status: "Planned", createdAt: "Starter" },
  { id: "web-basics", title: "Responsive web page", subject: "Web development", summary: "Build a semantic page and test it across mobile and desktop widths.", status: "In progress", createdAt: "Starter" },
  { id: "data-practice", title: "Data handling practice", subject: "Data structures", summary: "Work through arrays, dictionaries, searching and sorting exercises.", status: "Planned", createdAt: "Starter" },
];

const statusOptions: LabStatus[] = ["Planned", "In progress", "Completed"];

export default function LabPage() {
  const [entries, setEntries] = useState<LabEntry[]>(initialLabs);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"All" | LabStatus>("All");
  const [showForm, setShowForm] = useState(false);
  const [notice, setNotice] = useState("");
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("Web development");
  const [summary, setSummary] = useState("");
  const [status, setStatus] = useState<LabStatus>("Planned");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const parsed: unknown = JSON.parse(saved);
        if (Array.isArray(parsed)) setEntries(parsed as LabEntry[]);
      }
    } catch {
      setNotice("Browser storage is unavailable. Your current session still works, but changes may not persist after refresh.");
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    } catch {
      setNotice("Could not save to this browser. Export your lab list to keep a copy.");
    }
  }, [entries]);

  const filteredEntries = useMemo(() => {
    const term = query.trim().toLowerCase();
    return entries.filter((entry) => {
      const matchesStatus = filter === "All" || entry.status === filter;
      const matchesQuery = !term || [entry.title, entry.subject, entry.summary].some((value) => value.toLowerCase().includes(term));
      return matchesStatus && matchesQuery;
    });
  }, [entries, filter, query]);

  const completedCount = entries.filter((entry) => entry.status === "Completed").length;
  const activeCount = entries.filter((entry) => entry.status === "In progress").length;

  function addLab(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanTitle = title.trim();
    const cleanSummary = summary.trim();
    if (!cleanTitle) return;
    const next: LabEntry = {
      id: window.crypto?.randomUUID?.() || `lab-${Date.now().toString(36)}`,
      title: cleanTitle,
      subject: subject.trim() || "General",
      summary: cleanSummary || "No notes added yet.",
      status,
      createdAt: new Date().toISOString(),
    };
    setEntries((current) => [next, ...current]);
    setTitle("");
    setSummary("");
    setStatus("Planned");
    setShowForm(false);
    setNotice("Lab entry added.");
  }

  function updateStatus(id: string, nextStatus: LabStatus) {
    setEntries((current) => current.map((entry) => entry.id === id ? { ...entry, status: nextStatus } : entry));
    setNotice("Lab status updated.");
  }

  function removeEntry(id: string) {
    setEntries((current) => current.filter((entry) => entry.id !== id));
    setNotice("Lab entry removed.");
  }

  function exportLabs() {
    const header = ["Title", "Subject", "Summary", "Status", "Created"];
    const rows = entries.map((entry) => [entry.title, entry.subject, entry.summary, entry.status, entry.createdAt]);
    const csv = [header, ...rows].map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "jaro-lab-workspace.csv";
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice("Lab list exported as CSV.");
  }

  return (
    <main className={styles.shell}>
      <div className={styles.glow} aria-hidden="true" />
      <header className={styles.topbar}>
        <a href="/" className={styles.brand}><span className={styles.brandMark}>j</span><span>jaro<span className={styles.brandDot}>.</span></span></a>
        <nav aria-label="Lab navigation" className={styles.nav}><a href="/">Home</a><span aria-current="page">Lab workspace</span></nav>
        <button className={styles.secondaryButton} type="button" onClick={exportLabs}>Export CSV <span aria-hidden="true">↗</span></button>
      </header>

      <section className={styles.hero}>
        <div className={styles.eyebrow}><span className={styles.pulse} /> YOUR LEARNING WORKSPACE</div>
        <h1>Make room to<br /><em>experiment.</em></h1>
        <p className={styles.intro}>A simple home for lab exercises, practical notes and progress. Add experiments, track the next step, and keep a portable record of your work.</p>
        <div className={styles.heroActions}>
          <button className={styles.primaryButton} type="button" onClick={() => { setShowForm((open) => !open); setNotice(""); }}>{showForm ? "Close form" : "＋ Add a lab"}</button>
          <span className={styles.localNote}><span aria-hidden="true">◈</span> Saved in this browser</span>
        </div>
      </section>

      <section className={styles.stats} aria-label="Lab summary">
        <article className={styles.statCard}><span className={styles.statLabel}>TOTAL LABS</span><strong>{entries.length.toString().padStart(2, "0")}</strong><span className={styles.statFoot}>Your workspace</span></article>
        <article className={styles.statCard}><span className={styles.statLabel}>IN PROGRESS</span><strong>{activeCount.toString().padStart(2, "0")}</strong><span className={styles.statFoot}>Currently active</span></article>
        <article className={styles.statCard}><span className={styles.statLabel}>COMPLETED</span><strong>{completedCount.toString().padStart(2, "0")}</strong><span className={styles.statFoot}>Marked finished</span></article>
      </section>

      {showForm && <section className={styles.formCard} aria-labelledby="new-lab-title">
        <div className={styles.formHeading}><div><span className={styles.sectionLabel}>NEW ENTRY</span><h2 id="new-lab-title">Add a lab</h2></div><span className={styles.formGlyph}>＋</span></div>
        <form onSubmit={addLab} className={styles.form}>
          <label>Lab title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. JavaScript DOM events" maxLength={100} required /></label>
          <label>Subject<select value={subject} onChange={(event) => setSubject(event.target.value)}><option>Web development</option><option>Python</option><option>Java</option><option>Data structures</option><option>Database systems</option><option>Networking</option><option>Cyber security</option><option>General</option></select></label>
          <label className={styles.fullField}>Notes<textarea value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="What will you practise or what did you learn?" maxLength={1000} rows={3} /></label>
          <label>Status<select value={status} onChange={(event) => setStatus(event.target.value as LabStatus)}>{statusOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
          <div className={styles.formActions}><button className={styles.secondaryButton} type="button" onClick={() => setShowForm(false)}>Cancel</button><button className={styles.primaryButton} type="submit">Save lab ↗</button></div>
        </form>
      </section>}

      <section className={styles.listSection}>
        <div className={styles.listHeading}><div><span className={styles.sectionLabel}>YOUR COLLECTION</span><h2>Lab notebook</h2></div><span className={styles.countPill}>{filteredEntries.length} {filteredEntries.length === 1 ? "entry" : "entries"}</span></div>
        <div className={styles.controls}>
          <label className={styles.searchBox}><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search labs or subjects…" aria-label="Search labs or subjects" /></label>
          <label className={styles.filterBox}><span className={styles.srOnly}>Filter by status</span><select value={filter} onChange={(event) => setFilter(event.target.value as "All" | LabStatus)}><option>All</option>{statusOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
        </div>
        <div className={styles.labGrid}>
          {filteredEntries.map((entry, index) => <article className={styles.labCard} key={entry.id}>
            <div className={styles.cardTop}><span className={styles.cardIndex}>{String(index + 1).padStart(2, "0")}</span><span className={`${styles.status} ${entry.status === "Completed" ? styles.completed : entry.status === "In progress" ? styles.inProgress : styles.planned}`}>{entry.status}</span></div>
            <span className={styles.subject}>{entry.subject}</span>
            <h3>{entry.title}</h3>
            <p>{entry.summary}</p>
            <div className={styles.cardFooter}><label className={styles.statusControl}><span className={styles.srOnly}>Update status for {entry.title}</span><select value={entry.status} onChange={(event) => updateStatus(entry.id, event.target.value as LabStatus)}>{statusOptions.map((option) => <option key={option}>{option}</option>)}</select></label><button type="button" className={styles.removeButton} onClick={() => removeEntry(entry.id)} aria-label={`Remove ${entry.title}`}>Remove</button></div>
          </article>)}
          {filteredEntries.length === 0 && <div className={styles.emptyState}><span>⌕</span><h3>No matching labs</h3><p>Try another search or clear the status filter.</p><button type="button" className={styles.secondaryButton} onClick={() => { setQuery(""); setFilter("All"); }}>Clear filters</button></div>}
        </div>
      </section>

      {notice && <p className={styles.notice} role="status">{notice}<button type="button" onClick={() => setNotice("")} aria-label="Dismiss message">×</button></p>}
      <footer className={styles.footer}><a href="/" className={styles.brand}><span className={styles.brandMark}>j</span><span>jaro<span className={styles.brandDot}>.</span></span></a><span>Build steadily. Keep learning.</span><a href="#">Back to top ↑</a></footer>
    </main>
  );
}
