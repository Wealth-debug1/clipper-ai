"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "../lib/config";
import Nav from "../components/Nav";

const PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"];

export default function SettingsPage() {
  const [quota, setQuota] = useState(null);
  const [health, setHealth] = useState(null);

  async function loadStatus() {
    try {
      const [quotaRes, healthRes] = await Promise.all([
        fetch(API_BASE + "/quota-status"),
        fetch(API_BASE + "/health"),
      ]);
      setQuota(await quotaRes.json());
      setHealth(await healthRes.json());
    } catch (err) {
      console.error("Failed to load status:", err);
    }
  }

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <main>
      <Nav />
      <div className="p-6 max-w-3xl mx-auto flex flex-col gap-8">
        <section>
          <h2 className="font-semibold mb-3">Scheduler</h2>
          {health ? (
            <div className="text-sm grid grid-cols-2 gap-y-1 max-w-md">
              <span className="opacity-70">Queue total</span><span>{health.queue_total}</span>
              <span className="opacity-70">Unposted</span><span>{health.queue_unposted}</span>
              <span className="opacity-70">Fully posted</span><span>{health.queue_fully_posted}</span>
              <span className="opacity-70">Posts today</span><span>{health.posts_today} / {health.max_posts_per_day}</span>
              <span className="opacity-70">Peak hour now</span><span>{health.peak_hour_now ? "Yes" : "No"}</span>
              <span className="opacity-70">Peak hours</span><span>{(health.peak_hours || []).join(", ")}</span>
            </div>
          ) : (
            <p className="text-sm opacity-70">Loading...</p>
          )}
        </section>

        <section>
          <h2 className="font-semibold mb-3">Platform quota</h2>
          {!quota && <p className="text-sm opacity-70">Loading...</p>}
          {quota && (
            <div className="flex flex-col gap-3">
              {PLATFORMS.map((platform) => {
                const p = quota[platform] || {};
                return (
                  <div key={platform} className="border border-black/10 dark:border-white/10 rounded-lg p-3 flex items-center justify-between">
                    <div>
                      <div className="font-medium capitalize">{platform}</div>
                      <div className="text-xs opacity-70">
                        {p.posts_today ?? 0} / {p.posts_limit ?? "-"} posts today
                      </div>
                      {p.last_error && (
                        <div className="text-xs text-red-500 max-w-sm truncate" title={p.last_error.message}>
                          {p.last_error.message}
                        </div>
                      )}
                    </div>
                    <span
                      className={
                        "text-xs font-medium rounded-full px-2 py-0.5 " +
                        (p.configured ? "bg-green-600 text-white" : "bg-zinc-400 text-white")
                      }
                    >
                      {p.configured ? "Configured" : "Not configured"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
