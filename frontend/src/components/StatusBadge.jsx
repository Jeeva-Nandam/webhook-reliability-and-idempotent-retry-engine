const COLORS = {
  PENDING: "#9ca3af",
  PROCESSING: "#3b82f6",
  SUCCESS: "#22c55e",
  FAILED: "#f59e0b",
  DEAD: "#ef4444",
};

export default function StatusBadge({ status }) {
  const color = COLORS[status] || "#6b7280";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: "999px",
        fontSize: "0.75rem",
        fontWeight: 600,
        color: "#fff",
        backgroundColor: color,
      }}
    >
      {status}
    </span>
  );
}
