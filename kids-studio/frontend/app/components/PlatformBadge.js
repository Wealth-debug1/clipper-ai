"use client";

const STATUS_COLORS = {
  posted: "bg-green-600 text-white",
  pending: "bg-zinc-400 text-white",
  failed: "bg-red-600 text-white",
  skipped_no_credentials: "bg-amber-500 text-white",
  skipped_quota: "bg-amber-500 text-white",
};

const PLATFORM_LABELS = {
  youtube: "YT",
  tiktok: "TT",
  instagram: "IG",
  facebook: "FB",
};

export default function PlatformBadge({ platform, status }) {
  const color = STATUS_COLORS[status] || "bg-zinc-400 text-white";
  return (
    <span
      title={`${platform}: ${status}`}
      className={`inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}
    >
      {PLATFORM_LABELS[platform] || platform}
    </span>
  );
}
