// Phase 5: Full Kanban dashboard
// For now, renders a placeholder so the container boots successfully.

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-indigo-600 mb-2">🎯 JobRadar</h1>
        <p className="text-gray-500 text-lg">Frontend coming in Phase 5.</p>
        <p className="text-gray-400 mt-2">
          API available at{" "}
          <a
            href={`${import.meta.env.VITE_API_URL}/docs`}
            className="text-indigo-500 underline"
            target="_blank"
            rel="noreferrer"
          >
            {import.meta.env.VITE_API_URL}/docs
          </a>
        </p>
      </div>
    </div>
  );
}
