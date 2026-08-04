"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { API_BASE } from "../../lib/config";
import Nav from "../../components/Nav";
import PlatformBadge from "../../components/PlatformBadge";

const PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"];

export default function VideoDetailPage() {
  const { id } = useParams();
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(null);

  async function loadItem() {
    try {
      const res = await fetch(API_BASE + "/queue/" + id);
      const data = await res.json();
      if (data.error) {
        setItem(null);
      } else {
        setItem(data);
      }
    } catch (err) {
      console.error("Failed to load video:", err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (id) loadItem();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function postOrRetry(platform) {
    setPosting(platform);
    try {
      await fetch(API_BASE + "/post/" + id + "/" + platform, { method: "POST" });
      await loadItem();
    } catch (err) {
      alert("Post failed: " + err.message);
    } finally {
      setPosting(null);
    }
  }

  if (loading) {
    return (
      <main>
        <Nav />
        <p className="p-6 text-sm opacity-70">Loading...</p>
      </main>
    );
  }

  if (!item) {
    return (
      <main>
        <Nav />
        <p className="p-6 text-sm opacity-70">Video not found.</p>
      </main>
    );
  }

  return (
    <main>
      <Nav />
      <div className="p-6 max-w-3xl mx-auto flex flex-col gap-6">
        <div>
          <h1 className="text-xl font-semibold">{item.title}</h1>
          <p className="text-sm opacity-70">{item.tagline}</p>
        </div>

        <video src={item.video_url} controls className="w-full max-w-sm aspect-[9/16] bg-black rounded-lg mx-auto" />

        <div className="text-sm flex flex-col gap-1">
          <p><span className="opacity-70">Moral:</span> {item.moral}</p>
          <p><span className="opacity-70">Target age:</span> {item.target_age}</p>
          <p><span className="opacity-70">Created:</span> {item.created_at}</p>
        </div>

        <div>
          <h2 className="font-semibold mb-3">Publish status</h2>
          <div className="flex flex-col gap-3">
            {PLATFORMS.map((platform) => {
              const p = item.platforms?.[platform] || {};
              return (
                <div key={platform} className="border border-black/10 dark:border-white/10 rounded-lg p-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <PlatformBadge platform={platform} status={p.status} />
                    <div className="text-xs">
                      <div>{p.status}</div>
                      {p.posted_at && <div className="opacity-70">{p.posted_at}</div>}
                      {p.error && <div className="text-red-500 max-w-xs truncate" title={p.error}>{p.error}</div>}
                      {p.url && (
                        <a href={p.url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">
                          View post
                        </a>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => postOrRetry(platform)}
                    disabled={posting === platform}
                    className="text-xs bg-blue-600 disabled:bg-zinc-400 text-white rounded px-3 py-1.5"
                  >
                    {posting === platform ? "Posting..." : p.status === "posted" ? "Re-post" : "Post now"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </main>
  );
}
