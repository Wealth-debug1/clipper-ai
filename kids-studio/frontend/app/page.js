"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_BASE } from "./lib/config";
import Nav from "./components/Nav";
import PlatformBadge from "./components/PlatformBadge";

const PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"];

const PUBLISH_STATUS_LABELS = {
  fully_posted: "Fully posted",
  partially_posted: "Partially posted",
  posting_failed: "Posting failed",
  unposted: "Unposted",
};

export default function LibraryPage() {
  const [queue, setQueue] = useState([]);
  const [queueLoading, setQueueLoading] = useState(true);

  const [bookTitle, setBookTitle] = useState("");
  const [targetAge, setTargetAge] = useState("4-8");
  const [creating, setCreating] = useState(false);
  const [jobStatus, setJobStatus] = useState("");
  const [jobProgress, setJobProgress] = useState(0);
  const [jobMessage, setJobMessage] = useState("");

  async function loadQueue() {
    try {
      const res = await fetch(API_BASE + "/queue");
      const data = await res.json();
      setQueue((data.queue || []).slice().reverse());
    } catch (err) {
      console.error("Failed to load queue:", err);
    } finally {
      setQueueLoading(false);
    }
  }

  useEffect(() => {
    loadQueue();
  }, []);

  function checkJob(jobId) {
    let failCount = 0;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(API_BASE + "/job/" + jobId);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        failCount = 0;
        setJobStatus(data.status || "");
        setJobProgress(data.progress || 0);
        setJobMessage(data.message || "");
        if (data.status === "completed") {
          clearInterval(interval);
          setCreating(false);
          setJobMessage(data.message || "Video ready");
          await loadQueue();
        }
        if (data.status === "failed") {
          clearInterval(interval);
          setCreating(false);
          setJobMessage("Failed: " + (data.error || "Unknown error"));
        }
      } catch (err) {
        failCount++;
        if (failCount >= 3) {
          clearInterval(interval);
          setCreating(false);
          setJobMessage("Lost connection to kids-studio — refresh and check /job/" + jobId);
        } else {
          setJobMessage("Reconnecting... (" + failCount + "/3)");
        }
      }
    }, 2000);
  }

  async function createVideo(e) {
    e.preventDefault();
    if (!bookTitle.trim()) return;
    setCreating(true);
    setJobProgress(0);
    setJobStatus("starting");
    setJobMessage("Starting generation...");
    try {
      const params = new URLSearchParams({ book_title: bookTitle, target_age: targetAge });
      const res = await fetch(API_BASE + "/create-video?" + params.toString(), { method: "POST" });
      const data = await res.json();
      if (!data.job_id) throw new Error(data.detail || "Failed to start job");
      checkJob(data.job_id);
    } catch (err) {
      setCreating(false);
      setJobMessage("Error: " + err.message);
    }
  }

  return (
    <main>
      <Nav />
      <div className="p-6 max-w-5xl mx-auto flex flex-col gap-8">
        <section className="border border-black/10 dark:border-white/10 rounded-lg p-4">
          <h2 className="font-semibold mb-3">Create Video</h2>
          <form onSubmit={createVideo} className="flex flex-wrap gap-3 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs opacity-70">Book / story title</label>
              <input
                type="text"
                value={bookTitle}
                onChange={(e) => setBookTitle(e.target.value)}
                placeholder="The Very Hungry Caterpillar"
                className="border border-black/20 dark:border-white/20 rounded px-3 py-1.5 bg-transparent min-w-64"
                disabled={creating}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs opacity-70">Target age</label>
              <input
                type="text"
                value={targetAge}
                onChange={(e) => setTargetAge(e.target.value)}
                className="border border-black/20 dark:border-white/20 rounded px-3 py-1.5 bg-transparent w-24"
                disabled={creating}
              />
            </div>
            <button
              type="submit"
              disabled={creating || !bookTitle.trim()}
              className="bg-blue-600 disabled:bg-zinc-400 text-white rounded px-4 py-1.5 text-sm font-medium"
            >
              {creating ? "Generating..." : "Create Video"}
            </button>
          </form>

          {creating && (
            <div className="mt-4">
              <div className="flex justify-between text-xs mb-1">
                <span>{jobStatus}</span>
                <span>{jobProgress}%</span>
              </div>
              <div className="bg-zinc-200 dark:bg-zinc-800 rounded-full h-3">
                <div
                  className="bg-green-500 h-3 rounded-full transition-all"
                  style={{ width: jobProgress + "%" }}
                />
              </div>
              {jobMessage && <p className="text-xs opacity-70 mt-1">{jobMessage}</p>}
            </div>
          )}
          {!creating && jobMessage && <p className="text-xs opacity-70 mt-3">{jobMessage}</p>}
        </section>

        <section>
          <h2 className="font-semibold mb-3">Video Library</h2>
          {queueLoading && <p className="text-sm opacity-70">Loading...</p>}
          {!queueLoading && queue.length === 0 && (
            <p className="text-sm opacity-70">No videos yet — create one above.</p>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {queue.map((item) => (
              <div key={item.id} className="border border-black/10 dark:border-white/10 rounded-lg overflow-hidden flex flex-col">
                <video src={item.video_url} preload="metadata" className="w-full aspect-[9/16] bg-black object-cover" muted />
                <div className="p-3 flex flex-col gap-2">
                  <Link href={"/video/" + item.id} className="font-medium text-sm hover:underline">
                    {item.title}
                  </Link>
                  <div className="flex items-center gap-1 flex-wrap">
                    {PLATFORMS.map((platform) => (
                      <PlatformBadge key={platform} platform={platform} status={item.platforms?.[platform]?.status || "pending"} />
                    ))}
                  </div>
                  <span className="text-xs opacity-70">
                    {PUBLISH_STATUS_LABELS[item.publish_status] || item.publish_status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
