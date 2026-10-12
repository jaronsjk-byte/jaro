export default function Home() {
  return (
    <main style={{ fontFamily: "system-ui", padding: 40, maxWidth: 640 }}>
      <h1>ChillMind AI Answers</h1>
      <p>
        Internal service. The answer endpoint is <code>POST /api/answers</code> and
        returns <code>text/event-stream</code>. It responds 404 while the
        <code> AI_ANSWERS_ENABLED</code> flag is off.
      </p>
    </main>
  );
}
