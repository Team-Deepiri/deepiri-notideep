const fs = require("fs");

const WEBHOOK_URL = process.env.DISCORD_WEBHOOK_URL;
const EVENT_NAME = process.env.GITHUB_EVENT_NAME;
const EVENT_PATH = process.env.GITHUB_EVENT_PATH;
const REPO_FALLBACK = process.env.GITHUB_REPOSITORY || "unknown-repo";
const ACTOR_FALLBACK = process.env.GITHUB_ACTOR || "unknown-actor";
const SERVER_URL = process.env.GITHUB_SERVER_URL || "https://github.com";
const RUN_ID = process.env.GITHUB_RUN_ID;

if (!WEBHOOK_URL) {
  console.error("Missing DISCORD_WEBHOOK_URL secret.");
  process.exit(1);
}

if (!EVENT_PATH || !fs.existsSync(EVENT_PATH)) {
  console.error("Missing or invalid GITHUB_EVENT_PATH.");
  process.exit(1);
}

function readEventPayload() {
  const raw = fs.readFileSync(EVENT_PATH, "utf8");
  return JSON.parse(raw);
}

function truncate(text, max = 180) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function normalizeRef(ref) {
  if (!ref) return "unknown";
  if (ref.startsWith("refs/heads/")) return ref.replace("refs/heads/", "");
  if (ref.startsWith("refs/tags/")) return ref.replace("refs/tags/", "");
  return ref;
}

function colorForEvent(eventName, action) {
  if (eventName === "push") return 0x57f287;
  if (eventName === "pull_request" && action === "closed") return 0xed4245;
  if (eventName === "issues" && action === "closed") return 0xed4245;
  if (eventName === "release") return 0xfee75c;
  return 0x5865f2;
}

function buildPushEmbed(payload, common) {
  const branch = normalizeRef(payload.ref);
  const commits = Array.isArray(payload.commits) ? payload.commits : [];
  const commitLines = commits.slice(0, 5).map((commit) => {
    const shortSha = (commit.id || "").slice(0, 7);
    const message = truncate((commit.message || "").split("\n")[0], 90);
    const url = commit.url || common.link;
    return `• [\`${shortSha}\`](${url}) ${message}`;
  });

  const extraCommitCount = commits.length > 5 ? `\n• and ${commits.length - 5} more commit(s)` : "";
  const description =
    `${commits.length} commit(s) pushed to \`${branch}\`` +
    (commitLines.length ? `\n\n${commitLines.join("\n")}${extraCommitCount}` : "");

  return {
    title: "Push",
    description,
    fields: [
      { name: "Repository", value: common.repo, inline: true },
      { name: "Event", value: "push", inline: true },
      { name: "Actor", value: common.actor, inline: true }
    ],
    color: colorForEvent("push"),
    url: payload.compare || payload.head_commit?.url || common.link,
    timestamp: new Date().toISOString()
  };
}

function buildPullRequestEmbed(payload, common) {
  const pr = payload.pull_request || {};
  const isMerged = payload.action === "closed" && pr.merged;
  const actionLabel = isMerged ? "merged" : payload.action;

  return {
    title: `Pull Request ${actionLabel}`,
    description: `[#${pr.number || "?"}](${pr.html_url || common.link}) ${truncate(pr.title || "(no title)", 140)}`,
    fields: [
      { name: "Repository", value: common.repo, inline: true },
      { name: "Event", value: `pull_request.${actionLabel}`, inline: true },
      { name: "Actor", value: common.actor, inline: true }
    ],
    color: colorForEvent("pull_request", payload.action),
    url: pr.html_url || common.link,
    timestamp: new Date().toISOString()
  };
}

function buildIssuesEmbed(payload, common) {
  const issue = payload.issue || {};
  const labelName = payload.label?.name;
  const eventValue = labelName ? `issues.${payload.action} (${labelName})` : `issues.${payload.action}`;
  const labelLine = labelName ? `\nLabel: \`${labelName}\`` : "";

  return {
    title: `Issue ${payload.action}`,
    description: `[#${issue.number || "?"}](${issue.html_url || common.link}) ${truncate(issue.title || "(no title)", 140)}${labelLine}`,
    fields: [
      { name: "Repository", value: common.repo, inline: true },
      { name: "Event", value: eventValue, inline: true },
      { name: "Actor", value: common.actor, inline: true }
    ],
    color: colorForEvent("issues", payload.action),
    url: issue.html_url || common.link,
    timestamp: new Date().toISOString()
  };
}

function buildCreateEmbed(payload, common) {
  if (payload.ref_type !== "branch") {
    return null;
  }

  const branch = payload.ref || "unknown";

  return {
    title: "Branch created",
    description: `Branch \`${branch}\` was created.`,
    fields: [
      { name: "Repository", value: common.repo, inline: true },
      { name: "Event", value: "create.branch", inline: true },
      { name: "Actor", value: common.actor, inline: true }
    ],
    color: colorForEvent("create"),
    url: `${SERVER_URL}/${common.repo}/tree/${encodeURIComponent(branch)}`,
    timestamp: new Date().toISOString()
  };
}

function buildReleaseEmbed(payload, common) {
  const release = payload.release || {};
  const tag = release.tag_name || "unknown-tag";
  const title = release.name ? truncate(release.name, 120) : `Release ${tag}`;

  return {
    title: "Release published",
    description: `[${title}](${release.html_url || common.link})`,
    fields: [
      { name: "Repository", value: common.repo, inline: true },
      { name: "Event", value: "release.published", inline: true },
      { name: "Actor", value: common.actor, inline: true }
    ],
    color: colorForEvent("release"),
    url: release.html_url || common.link,
    timestamp: new Date().toISOString()
  };
}

function buildEmbed(eventName, payload, common) {
  switch (eventName) {
    case "push":
      return buildPushEmbed(payload, common);
    case "pull_request":
      return buildPullRequestEmbed(payload, common);
    case "issues":
      return buildIssuesEmbed(payload, common);
    case "create":
      return buildCreateEmbed(payload, common);
    case "release":
      return buildReleaseEmbed(payload, common);
    default:
      return null;
  }
}

async function sendToDiscord(webhookUrl, body) {
  const response = await fetch(webhookUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const responseText = await response.text();
    throw new Error(`Discord webhook failed: ${response.status} ${responseText}`);
  }
}

async function main() {
  const payload = readEventPayload();
  const repo = payload.repository?.full_name || REPO_FALLBACK;
  const actor = payload.sender?.login || ACTOR_FALLBACK;
  const link = payload.repository?.html_url || `${SERVER_URL}/${repo}`;

  const common = { repo, actor, link };
  const embed = buildEmbed(EVENT_NAME, payload, common);

  if (!embed) {
    console.log(`No Discord message sent for event: ${EVENT_NAME}`);
    return;
  }

  if (RUN_ID) {
    embed.footer = {
      text: `GitHub Actions run ${RUN_ID}`
    };
  }

  const body = {
    username: "GitHub Activity",
    allowed_mentions: { parse: [] },
    embeds: [embed]
  };

  await sendToDiscord(WEBHOOK_URL, body);
  console.log("Discord notification sent.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
