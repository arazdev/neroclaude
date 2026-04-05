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

interface KalshiOrder {
  order_id: string;
  ticker: string;
  side: string;
  action: string;
  status: string;
  created_time: string;
}

interface KalshiOrderHistory {
  resting: KalshiOrder[];
  executed: KalshiOrder[];
  canceled: KalshiOrder[];
}

interface KalshiFill {
  ticker: string;
  side: string;
  action: string;
  created_time: string | null;
}

interface KalshiPosition {
  ticker: string;
  side: string;
  action: string;
  fills: number;
}

interface KalshiPositions {
  positions: KalshiPosition[];
  positions_count: number;
  fills: KalshiFill[];
  fills_count: number;
  cash: number;
  note: string;
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

interface Settings {
  dry_run: boolean;
  bot_mode: string;
  claude_model: string;
  poly_enabled: boolean;
  kalshi_enabled: boolean;
  max_order_usdc: number;
  max_position_usdc: number;
  poll_interval: number;
}

interface Wallet {
  polymarket: {
    balance: number;
    positions_value: number;
    total: number;
  };
  kalshi: {
    balance: number;
    available: number;
    positions_value?: number;
  };
  combined: {
    total_balance: number;
    total_portfolio: number;
  };
  realized_pnl: number;
  open_positions: number;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (API_SECRET) {
    headers["Authorization"] = `Bearer ${API_SECRET}`;
  }
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { ...headers, ...options?.headers },
    cache: "no-store",
  });
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
        <span>token: {p.token_id.slice(0, 16)}...</span>
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

function SettingsPanel({
  settings,
  onSave,
  onRestart,
  onClose,
  saving,
  restarting,
}: {
  settings: Settings;
  onSave: (s: Settings) => void;
  onRestart: () => void;
  onClose: () => void;
  saving: boolean;
  restarting: boolean;
}) {
  const [local, setLocal] = useState<Settings>(settings);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.8)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "#1a1a2e",
          borderRadius: 16,
          padding: 28,
          width: 400,
          maxWidth: "90vw",
          border: "1px solid #333",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{ margin: "0 0 20px", fontSize: 20 }}>Settings</h2>

        {/* DRY RUN toggle */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ flex: 1, fontSize: 14 }}>DRY RUN (paper trading)</span>
            <button
              onClick={() => setLocal({ ...local, dry_run: !local.dry_run })}
              style={{
                background: local.dry_run ? "#facc15" : "#333",
                color: local.dry_run ? "#000" : "#888",
                border: "none",
                borderRadius: 6,
                padding: "6px 16px",
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              {local.dry_run ? "ON" : "OFF"}
            </button>
          </label>
        </div>

        {/* PLATFORMS */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Trading Platforms
          </label>
          <div style={{ display: "flex", gap: 12 }}>
            <button
              onClick={() => setLocal({ ...local, poly_enabled: !local.poly_enabled })}
              style={{
                flex: 1,
                background: local.poly_enabled ? "#8b5cf6" : "#1a1a2e",
                color: local.poly_enabled ? "#fff" : "#666",
                border: local.poly_enabled ? "2px solid #8b5cf6" : "2px solid #333",
                borderRadius: 8,
                padding: "10px 12px",
                fontWeight: 600,
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              {local.poly_enabled ? "✓" : "○"} Polymarket
            </button>
            <button
              onClick={() => setLocal({ ...local, kalshi_enabled: !local.kalshi_enabled })}
              style={{
                flex: 1,
                background: local.kalshi_enabled ? "#ec4899" : "#1a1a2e",
                color: local.kalshi_enabled ? "#fff" : "#666",
                border: local.kalshi_enabled ? "2px solid #ec4899" : "2px solid #333",
                borderRadius: 8,
                padding: "10px 12px",
                fontWeight: 600,
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              {local.kalshi_enabled ? "✓" : "○"} Kalshi
            </button>
          </div>
        </div>

        {/* BOT MODE */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Bot Mode
          </label>
          <select
            value={local.bot_mode}
            onChange={(e) => setLocal({ ...local, bot_mode: e.target.value })}
            style={{
              width: "100%",
              background: "#0d0d15",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 14,
            }}
          >
            <option value="claude">Claude AI Only</option>
            <option value="arb">Arbitrage Only</option>
            <option value="cross">Cross-Platform Only</option>
            <option value="mm">Market Making Only</option>
            <option value="all">All Strategies</option>
          </select>
        </div>

        {/* CLAUDE MODEL */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Claude Model
            <span style={{ color: "#888", fontSize: 12, marginLeft: 8 }}>
              (Haiku = cheapest, Sonnet 4 = best)
            </span>
          </label>
          <select
            value={local.claude_model}
            onChange={(e) => setLocal({ ...local, claude_model: e.target.value })}
            style={{
              width: "100%",
              background: "#0d0d15",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 14,
            }}
          >
            <option value="claude-3-haiku-20240307">Claude 3 Haiku (cheapest ~$0.25/M)</option>
            <option value="claude-3-5-haiku-20241022">Claude 3.5 Haiku (~$1/M)</option>
            <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet (~$3/M)</option>
            <option value="claude-sonnet-4-20250514">Claude Sonnet 4 (best ~$3/M)</option>
          </select>
        </div>

        {/* MAX ORDER */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Max Order Size (USDC)
          </label>
          <input
            type="number"
            value={local.max_order_usdc}
            onChange={(e) =>
              setLocal({ ...local, max_order_usdc: Number(e.target.value) })
            }
            style={{
              width: "100%",
              background: "#0d0d15",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 14,
            }}
          />
        </div>

        {/* MAX POSITION */}
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Max Position Size (USDC)
          </label>
          <input
            type="number"
            value={local.max_position_usdc}
            onChange={(e) =>
              setLocal({ ...local, max_position_usdc: Number(e.target.value) })
            }
            style={{
              width: "100%",
              background: "#0d0d15",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 14,
            }}
          />
        </div>

        {/* POLL INTERVAL */}
        <div style={{ marginBottom: 24 }}>
          <label style={{ fontSize: 14, display: "block", marginBottom: 8 }}>
            Poll Interval (seconds)
          </label>
          <input
            type="number"
            value={local.poll_interval}
            onChange={(e) =>
              setLocal({ ...local, poll_interval: Number(e.target.value) })
            }
            style={{
              width: "100%",
              background: "#0d0d15",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: 14,
            }}
          />
        </div>

        {/* Buttons */}
        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={() => onSave(local)}
            disabled={saving}
            style={{
              flex: 1,
              background: "#4ade80",
              color: "#000",
              border: "none",
              borderRadius: 8,
              padding: "12px",
              fontWeight: 700,
              cursor: saving ? "wait" : "pointer",
              opacity: saving ? 0.7 : 1,
            }}
          >
            {saving ? "Saving..." : "Save Settings"}
          </button>
          <button
            onClick={onRestart}
            disabled={restarting}
            style={{
              flex: 1,
              background: "#f87171",
              color: "#000",
              border: "none",
              borderRadius: 8,
              padding: "12px",
              fontWeight: 700,
              cursor: restarting ? "wait" : "pointer",
              opacity: restarting ? 0.7 : 1,
            }}
          >
            {restarting ? "Restarting..." : "Restart Bot"}
          </button>
        </div>

        <button
          onClick={onClose}
          style={{
            width: "100%",
            marginTop: 12,
            background: "transparent",
            color: "#888",
            border: "1px solid #333",
            borderRadius: 8,
            padding: "10px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
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

  // Settings state
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);

  // Wallet state
  const [wallet, setWallet] = useState<Wallet | null>(null);

  // Logs state
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState(false);

  // Kalshi orders state
  const [kalshiOrders, setKalshiOrders] = useState<KalshiOrderHistory | null>(null);
  const [kalshiPositions, setKalshiPositions] = useState<KalshiPositions | null>(null);
  const [showKalshiOrders, setShowKalshiOrders] = useState(false);

  const fetchKalshiOrders = async () => {
    try {
      const [orders, positions] = await Promise.all([
        apiFetch<KalshiOrderHistory>("/api/kalshi/orders/history"),
        apiFetch<KalshiPositions>("/api/kalshi/positions"),
      ]);
      setKalshiOrders(orders);
      setKalshiPositions(positions);
    } catch {
      setKalshiOrders({ resting: [], executed: [], canceled: [] });
      setKalshiPositions({ positions: [], positions_count: 0, fills: [], fills_count: 0, cash: 0, note: "" });
    }
  };

  const fetchLogs = async () => {
    try {
      const data = await apiFetch<{ logs: string[]; total: number }>("/api/logs?lines=30");
      setLogs(data.logs);
    } catch {
      setLogs(["Failed to fetch logs"]);
    }
  };

  const refresh = async () => {
    try {
      setError(null);
      const [s, positions, w] = await Promise.all([
        apiFetch<Summary>("/api/summary"),
        apiFetch<{ open: Position[]; closed: Position[] }>("/api/positions"),
        apiFetch<Wallet>("/api/wallet").catch(() => null),
      ]);
      setSummary(s);
      setOpenPos(positions.open);
      setClosedPos(positions.closed);
      if (w) setWallet(w);
      await fetchKalshiOrders();
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e: unknown) {
      const err = e as Error;
      setError(err.message || "Failed to fetch");
    }
  };

  const loadSettings = async () => {
    try {
      const s = await apiFetch<Settings>("/api/settings");
      setSettings(s);
      setShowSettings(true);
    } catch (e: unknown) {
      const err = e as Error;
      setError("Failed to load settings: " + err.message);
    }
  };

  const saveSettings = async (s: Settings) => {
    setSaving(true);
    try {
      await apiFetch("/api/settings", {
        method: "POST",
        body: JSON.stringify(s),
      });
      setSettings(s);
      setShowSettings(false);
      refresh();
    } catch (e: unknown) {
      const err = e as Error;
      setError("Failed to save: " + err.message);
    } finally {
      setSaving(false);
    }
  };

  const restartBot = async () => {
    setRestarting(true);
    try {
      await apiFetch("/api/restart", { method: "POST" });
      setShowSettings(false);
      setTimeout(refresh, 3000);
    } catch (e: unknown) {
      const err = e as Error;
      setError("Failed to restart: " + err.message);
    } finally {
      setRestarting(false);
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 20px" }}>
      {/* Settings Modal */}
      {showSettings && settings && (
        <SettingsPanel
          settings={settings}
          onSave={saveSettings}
          onRestart={restartBot}
          onClose={() => setShowSettings(false)}
          saving={saving}
          restarting={restarting}
        />
      )}

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
            Polymarket & Kalshi Trading Dashboard
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
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => { fetchLogs(); setShowLogs(!showLogs); }}
            style={{
              background: showLogs ? "#8b5cf6" : "#333",
              color: "#fff",
              border: "1px solid #444",
              borderRadius: 8,
              padding: "8px 16px",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Logs
          </button>
          <button
            onClick={() => { fetchKalshiOrders(); setShowKalshiOrders(!showKalshiOrders); }}
            style={{
              background: showKalshiOrders ? "#ec4899" : "#333",
              color: "#fff",
              border: "1px solid #444",
              borderRadius: 8,
              padding: "8px 16px",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Kalshi Orders
          </button>
          <button
            onClick={loadSettings}
            style={{
              background: "#333",
              color: "#fff",
              border: "1px solid #444",
              borderRadius: 8,
              padding: "8px 16px",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Settings
          </button>
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
        </div>
      </div>

      {/* Last refresh time */}
      {lastRefresh && (
        <div style={{ fontSize: 11, color: "#555", marginBottom: 16, textAlign: "right" }}>
          Last updated: {lastRefresh}
        </div>
      )}

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
          {error}
        </div>
      )}

      {/* Logs Panel */}
      {showLogs && (
        <div
          style={{
            background: "#0a0a0f",
            border: "1px solid #333",
            borderRadius: 12,
            padding: "16px",
            marginBottom: 24,
            maxHeight: 300,
            overflow: "auto",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: "#8b5cf6" }}>
              Bot Activity Logs
            </span>
            <button
              onClick={fetchLogs}
              style={{
                background: "#222",
                color: "#888",
                border: "1px solid #333",
                borderRadius: 4,
                padding: "4px 8px",
                cursor: "pointer",
                fontSize: 11,
              }}
            >
              Refresh
            </button>
          </div>
          <div style={{ fontFamily: "monospace", fontSize: 11, lineHeight: 1.6 }}>
            {logs.length === 0 ? (
              <div style={{ color: "#555" }}>No logs yet...</div>
            ) : (
              logs.map((line, i) => (
                <div
                  key={i}
                  style={{
                    color: line.includes("ERROR") ? "#f87171" :
                           line.includes("WARNING") ? "#facc15" :
                           line.includes("Claude") || line.includes("Kalshi") ? "#8b5cf6" :
                           line.includes("BUY") || line.includes("trade") ? "#4ade80" :
                           "#888",
                    padding: "2px 0",
                    borderBottom: "1px solid #1a1a1a",
                  }}
                >
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Kalshi Orders Panel */}
      {showKalshiOrders && kalshiOrders && (
        <div
          style={{
            background: "#0a0a0f",
            border: "1px solid #ec4899",
            borderRadius: 12,
            padding: "16px",
            marginBottom: 24,
            maxHeight: 500,
            overflow: "auto",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: "#ec4899" }}>
              Kalshi Portfolio
              {kalshiPositions && (
                <span style={{ marginLeft: 12, fontSize: 12, color: "#4ade80" }}>
                  ${kalshiPositions.cash?.toFixed(2) || "0.00"} cash • {kalshiPositions.positions_count} positions • {kalshiPositions.fills_count} fills
                </span>
              )}
            </span>
            <button
              onClick={fetchKalshiOrders}
              style={{
                background: "#222",
                color: "#888",
                border: "1px solid #333",
                borderRadius: 4,
                padding: "4px 8px",
                cursor: "pointer",
                fontSize: 11,
              }}
            >
              Refresh
            </button>
          </div>

          {/* Active Positions from exchange */}
          {kalshiPositions && kalshiPositions.positions.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#4ade80", marginBottom: 8 }}>
                📊 Open Positions ({kalshiPositions.positions_count})
              </div>
              {kalshiPositions.positions.map((p, i) => (
                <div
                  key={i}
                  style={{
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: "#4ade80",
                    padding: "6px 0",
                    borderBottom: "1px solid #1a1a1a",
                  }}
                >
                  <span style={{ color: p.side === "yes" ? "#4ade80" : "#f87171" }}>
                    {p.action.toUpperCase()} {p.side.toUpperCase()}
                  </span>{" "}
                  <span style={{ color: "#ec4899" }}>{p.ticker}</span>
                  <span style={{ color: "#888", marginLeft: 8 }}>({p.fills} fills)</span>
                </div>
              ))}
            </div>
          )}

          {/* Trade History */}
          {kalshiPositions && kalshiPositions.fills.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#8b5cf6", marginBottom: 8 }}>
                📜 Trade History ({kalshiPositions.fills_count})
              </div>
              {kalshiPositions.fills.map((f, i) => (
                <div
                  key={i}
                  style={{
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: "#a78bfa",
                    padding: "6px 0",
                    borderBottom: "1px solid #1a1a1a",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>
                      {f.action.toUpperCase()} {f.side.toUpperCase()} <span style={{ color: "#ec4899" }}>{f.ticker}</span>
                    </span>
                    <span style={{ color: "#888" }}>
                      {f.created_time ? new Date(f.created_time).toLocaleString() : ""}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
          
          {/* Executed Orders */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#4ade80", marginBottom: 8 }}>
              ✓ Executed ({kalshiOrders.executed.length})
            </div>
            {kalshiOrders.executed.slice(0, 10).map((o, i) => (
              <div
                key={i}
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "#4ade80",
                  padding: "4px 0",
                  borderBottom: "1px solid #1a1a1a",
                }}
              >
                {o.action.toUpperCase()} {o.side.toUpperCase()} {o.ticker}
                <span style={{ color: "#555", marginLeft: 8 }}>
                  {new Date(o.created_time).toLocaleTimeString()}
                </span>
              </div>
            ))}
            {kalshiOrders.executed.length === 0 && (
              <div style={{ fontSize: 11, color: "#555" }}>No executed orders</div>
            )}
          </div>

          {/* Resting Orders */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#facc15", marginBottom: 8 }}>
              ⏳ Resting ({kalshiOrders.resting.length})
            </div>
            {kalshiOrders.resting.slice(0, 10).map((o, i) => (
              <div
                key={i}
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "#facc15",
                  padding: "4px 0",
                  borderBottom: "1px solid #1a1a1a",
                }}
              >
                {o.action.toUpperCase()} {o.side.toUpperCase()} {o.ticker}
              </div>
            ))}
            {kalshiOrders.resting.length === 0 && (
              <div style={{ fontSize: 11, color: "#555" }}>No resting orders</div>
            )}
          </div>

          {/* Canceled Orders */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#f87171", marginBottom: 8 }}>
              ✗ Canceled ({kalshiOrders.canceled.length})
            </div>
            {kalshiOrders.canceled.slice(0, 10).map((o, i) => (
              <div
                key={i}
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "#f87171",
                  padding: "4px 0",
                  borderBottom: "1px solid #1a1a1a",
                }}
              >
                {o.action.toUpperCase()} {o.side.toUpperCase()} {o.ticker}
                <span style={{ color: "#555", marginLeft: 8 }}>
                  {new Date(o.created_time).toLocaleTimeString()}
                </span>
              </div>
            ))}
            {kalshiOrders.canceled.length === 0 && (
              <div style={{ fontSize: 11, color: "#555" }}>No canceled orders</div>
            )}
          </div>
        </div>
      )}

      {/* Wallet Overview */}
      {wallet && (
        <div
          style={{
            background: "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)",
            borderRadius: 16,
            padding: "24px",
            marginBottom: 24,
            border: "1px solid #333",
          }}
        >
          <div style={{ display: "flex", gap: 48, flexWrap: "wrap" }}>
            {/* Polymarket */}
            <div>
              <div style={{ fontSize: 12, color: "#888", marginBottom: 12 }}>POLYMARKET</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#a78bfa" }}>
                ${wallet.polymarket.balance.toFixed(2)}
              </div>
              <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>Available USDC</div>
              <div style={{ fontSize: 16, color: "#60a5fa" }}>
                ${wallet.polymarket.positions_value.toFixed(2)} <span style={{ fontSize: 12, color: "#666" }}>in positions</span>
              </div>
            </div>
            {/* Kalshi */}
            <div>
              <div style={{ fontSize: 12, color: "#888", marginBottom: 12 }}>KALSHI</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#f472b6" }}>
                ${wallet.kalshi.balance.toFixed(2)}
              </div>
              <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>Cash Balance</div>
              <div style={{ fontSize: 16, color: "#60a5fa" }}>
                {kalshiPositions ? (
                  <>{kalshiPositions.positions_count} <span style={{ fontSize: 12, color: "#666" }}>open positions</span></>
                ) : (
                  <span style={{ fontSize: 12, color: "#666" }}>Loading...</span>
                )}
              </div>
            </div>
            {/* Combined */}
            <div>
              <div style={{ fontSize: 12, color: "#888", marginBottom: 12 }}>COMBINED</div>
              <div style={{ fontSize: 28, fontWeight: 700, color: "#4ade80" }}>
                ${wallet.combined.total_portfolio.toFixed(2)}
              </div>
              <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>Cash + Poly Positions</div>
              <div
                style={{
                  fontSize: 16,
                  color: wallet.realized_pnl >= 0 ? "#4ade80" : "#f87171",
                }}
              >
                {wallet.realized_pnl >= 0 ? "+" : ""}${wallet.realized_pnl.toFixed(2)} <span style={{ fontSize: 12, color: "#666" }}>realized P&L</span>
              </div>
              <div style={{ fontSize: 10, color: "#555", marginTop: 4 }}>
                *Kalshi positions value not included
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Stats */}
      {summary && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
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
