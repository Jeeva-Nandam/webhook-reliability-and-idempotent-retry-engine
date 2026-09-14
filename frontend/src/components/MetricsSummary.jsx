export default function MetricsSummary({ metrics }) {
  if (!metrics) return null;

  const cards = [
    { label: "Total Events", value: metrics.total_events },
    { label: "Success", value: metrics.success_count },
    { label: "Failed", value: metrics.failed_count },
    { label: "Dead", value: metrics.dead_count },
    { label: "Processing", value: metrics.processing_count },
    { label: "Pending", value: metrics.pending_count },
    { label: "Success Rate", value: `${metrics.success_rate.toFixed(1)}%` },
  ];

  return (
    <div className="metrics-grid">
      {cards.map((c) => (
        <div className="metric-card" key={c.label}>
          <div className="metric-value">{c.value}</div>
          <div className="metric-label">{c.label}</div>
        </div>
      ))}
    </div>
  );
}
