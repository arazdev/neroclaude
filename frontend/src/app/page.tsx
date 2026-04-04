"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
const API_SECRET = process.env.NEXT_PUBLIC_API_SECRET || "";

interface Position {
  id: string;
  token_id: string;
  market_question: string;
  side: string;
  action: string;
  entry_price: number;
  size_usdc: number;
  shares: number;
  confidence: number;
  reasoning: string;
  opened_at: string;
  status: string;
  closed_at: string | null;
  exit_price: number | null;
  pnl: number | null;
}

interface StrategyStats {
  open: number;
  exposure: number;
}

interface Summary {
  open_count: number;
  closed_count: number;
  total_exposure: number;
  realized_pnl: number;
  max_position_usdc: number;
  max_order_usdc: number;
  dry_run: boolean;
  bot_mode: string;
  strategies: {
    claude: StrategyStats;
    arb: StrategyStats;
    cross: StrategyStats;
    mm: StrategyStats;
  };
  time: string;
}

async function apiFetch<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (API_SECRET) {
    headers["Authorization"] = `Bearer ${API_SECRET}`;
  }
  const res = await fetch(`${API_URL}${path}`, { headers, cache: "no-store" });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div
      style={{
        background: "#141420",
        borderRadius: 12,
        padding: "20px 24px",
        border: "1px solid #222",
        minWidth: 160,
      }}
    >
      <div style={{ fontSize: 12, color: "#888", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, color: color || "#fff" }}>
        {value}
      </div>
    </div>
  );
}

function PositionRow({ p }: { p: Position }) {
  const isOpen = p.status === "OPEN";
  const sideColor = p.side === "BUY" ? "#4ade80" : "#f87171";
  const confPct = (p.confidence * 100).toFixed(0);

  const strategyMap: Record<string, { label: string; color: string }> = {
    ARB_YES: { label: "ARB", color: "#facc15" },
    ARB_NO: { label: "ARB", color: "#facc15" },
    CROSS_ARB: { label: "CROSS", color: "#a78bfa" },
    MM_BID_YES: { label: "MM", color: "#38bdf8" },
    MM_BID_NO: { label: "MM", color: "#38bdf8" },
  };
  const strategy = strategyMap[p.action] || { label: "AI", color: "#f472b6" };

  return (
    <div
      style={{
        background: "#141420",
        borderRadius: 10,
        padding: "16px 20px",
        border: `1px solid ${isOpen ? "#333" : "#1a1a2a"}`,
        marginBottom: 10,
        opacity: isOpen ? 1 : 0.65,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              background: strategy.color,
              color: "#000",
              padding: "2px 6px",
              borderRadius: 4,
              fontSize: 10,
              fontWeight: 700,
            }}
          >
            {strategy.label}
          </span>
          <span
            style={{
              background: sideColor,
              color: "#000",
              padding: "2px 8px",
              borderRadius: 4,
              fontSize: 11,
              fontWeight: 700,
            }}
          >
            {p.side}
          </span>
          <span style={{ fontWeight: 600, fontSize: 14 }}>
            ${p.size_usdc.toFixed(2)}
          </span>
          <span style={{ color: "#888", fontSize: 13 }}>
            @ {p.entry_price.toFixed(4)}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              fontSize: 12,
              color: Number(confPct) >= 70 ? "#4ade80" : "#facc15",
            }}
          >
            {confPct}% conf
          </span>
          <span
            style={{
              fontSize: 11,
              padding: "2px 8px",
              borderRadius: 4,
              background: isOpen ? "#1e3a2f" : "#1a1a2a",
              color: isOpen ? "#4ade80" : "#888",
            }}
          >
            {p.status}
          </span>
        </div>
      </div>
      <div style={{ fontSize: 14, marginBottom: 6 }}>{p.market_question}</div>
      <div
        style={{
          fontSize: 12,
          color: "#666",
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <span>token: {p.token_id.slice(0, 16)}…</span>
        <span>opened: {p.opened_at.slice(0, 19)}Z</span>
        {p.pnl !== null && (
          <span style={{ color: p.pnl >= 0 ? "#4ade80" : "#f87171" }}>
            P&L: ${p.pnl.toFixed(2)}
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: 12,
          color: "#555",
          marginTop: 6,
          fontStyle: "italic",
        }}
      >
        {p.reasoning}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [openPos, setOpenPos] = useState<Position[]>([]);
  const [closedPos, setClosedPos] = useState<Position[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<string>("");

  const refresh = async () => {
    try {
      setError(null);
      const [s, positions] = await Promise.all([
        apiFetch<Summary>("/api/summary"),
        apiFetch<{ open: Position[]; closed: Position[] }>("/api/positions"),
      ]);
      setSummary(s);
      setOpenPos(positions.open);
      setClosedPos(positions.closed);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e: any) {
      setError(e.message || "Failed to fetch");
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15_000); // auto-refresh every 15s
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 20px" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 32,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 28, letterSpacing: -1 }}>
            NEROCLAUDE
          </h1>
          <div style={{ fontSize: 13, color: "#666", marginTop: 4 }}>
            Polymarket Trading Dashboard
            {summary?.dry_run && (
              <span
                style={{
                  marginLeft: 10,
                  background: "#facc15",
                  color: "#000",
                  padding: "1px 6px",
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 700,
                }}
              >
                DRY RUN
              </span>
            )}
            {summary?.bot_mode && (
              <span
                style={{
                  marginLeft: 6,
                  background: "#333",
                  color: "#ccc",
                  padding: "1px 6px",
                  borderRadius: 4,
                  fontSize: 11,
                }}
              >
                {summary.bot_mode.toUpperCase()}
              </span>
            )}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <button
            onClick={refresh}
            style={{
              background: "#222",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "8px 16px",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Refresh
          </button>
          {lastRefresh && (
            <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>
              {lastRefresh}
            </div>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div
          style={{
            background: "#2a1515",
            border: "1px solid #f87171",
            borderRadius: 8,
            padding: "12px 16px",
            marginBottom: 20,
            color: "#f87171",
            fontSize: 14,
          }}
        >
          Connection error: {error}
        </div>
      )}

      {/* Stats */}
      {summary && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: 12,
            marginBottom: 20,
          }}
        >
          <StatCard label="Open Positions" value={String(summary.open_count)} />
          <StatCard
            label="Total Exposure"
            value={`$${summary.total_exposure.toFixed(2)}`}
            color="#60a5fa"
          />
          <StatCard
            label="Max Budget"
            value={`$${summary.max_position_usdc.toFixed(0)}`}
          />
          <StatCard
            label="Realized P&L"
            value={`$${summary.realized_pnl.toFixed(2)}`}
            color={summary.realized_pnl >= 0 ? "#4ade80" : "#f87171"}
          />
          <StatCard
            label="Closed Trades"
            value={String(summary.closed_count)}
          />
        </div>
      )}

      {/* Strategy breakdown */}
      {summary?.strategies && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 10,
            marginBottom: 32,
          }}
        >
          {[
            { key: "claude", label: "AI (Claude)", color: "#f472b6" },
            { key: "arb", label: "Arbitrage", color: "#facc15" },
            { key: "cross", label: "Cross-Platform", color: "#a78bfa" },
            { key: "mm", label: "Market Making", color: "#38bdf8" },
          ].map(({ key, label, color }) => {
            const s = summary.strategies[key as keyof typeof summary.strategies];
            return (
              <div
                key={key}
                style={{
                  background: "#141420",
                  borderRadius: 10,
                  padding: "12px 16px",
                  borderLeft: `3px solid ${color}`,
                }}
              >
                <div style={{ fontSize: 11, color: "#888" }}>{label}</div>
                <div style={{ fontSize: 16, fontWeight: 700, color }}>
                  {s.open} open
                </div>
                <div style={{ fontSize: 12, color: "#555" }}>
                  ${s.exposure.toFixed(2)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Open positions */}
      <h2 style={{ fontSize: 16, marginBottom: 12, color: "#4ade80" }}>
        Open Positions ({openPos.length})
      </h2>
      {openPos.length === 0 ? (
        <div
          style={{
            color: "#555",
            padding: 20,
            textAlign: "center",
            border: "1px dashed #222",
            borderRadius: 10,
            marginBottom: 32,
          }}
        >
          No open positions
        </div>
      ) : (
        <div style={{ marginBottom: 32 }}>
          {openPos.map((p) => (
            <PositionRow key={p.id} p={p} />
          ))}
        </div>
      )}

      {/* Closed positions */}
      {closedPos.length > 0 && (
        <>
          <h2 style={{ fontSize: 16, marginBottom: 12, color: "#888" }}>
            Closed Trades ({closedPos.length})
          </h2>
          <div>
            {closedPos
              .slice()
              .reverse()
              .map((p) => (
                <PositionRow key={p.id} p={p} />
              ))}
          </div>
        </>
      )}
    </div>
  );
}
