---
name: youtube-agent-builder
description: "Build a skill or agent by having Claude watch YouTube tutorials — both the transcript (what was said) and the actual video frames/audio (what was shown but never explained). Use when asked to: watch a video and summarize/analyze it, learn a skill from YouTube tutorials, or build a skill/agent from a set of tutorial URLs. Requires the youtube and yt-analysis MCP servers (see Setup)."
metadata:
  {
    "openclaw":
      {
        "emoji": "📺",
        "requires": { "mcp": ["youtube", "yt-analysis"] },
        "install":
          [
            {
              "id": "script",
              "kind": "script",
              "path": "scripts/setup_youtube_mcp.sh",
              "label": "Clone, build, and register the YouTube MCP servers",
            },
          ],
      },
  }
---

# YouTube Agent Builder

Turn YouTube tutorials into a tested, reusable skill file. Works because two
tools cover two different kinds of information: a transcript MCP for what was
**said**, and a Gemini-backed MCP for what was **shown** — the part of a
tutorial where someone says "and then you just do this" and the transcript
records nothing useful.

## Setup

Run once per machine: `./scripts/setup_youtube_mcp.sh`. It clones and builds
both servers and registers them with Claude Code (`claude mcp add -s user`).
You'll be asked for two keys:

- **YouTube Data API v3 key** — optional. Without it you still get 5 of 15
  transcript-layer tools (transcripts, SponsorBlock, most-replayed, URL
  parsing). With it you also get search, metadata, channel/playlist info,
  comments, and engagement stats. Daily quota is 10,000 units; a search costs
  100, pulling a channel's recent videos costs 1 — pull channels, don't search,
  when you can.
- **Gemini API key** (free, from [Google AI Studio](https://aistudio.google.com/apikey))
  — required for the visual-analysis layer.

**Known limitation:** private and age-restricted videos won't work for the
Gemini layer — it has to be able to fetch the URL itself.

Sanity check before building anything: paste a tutorial URL and ask *"Watch
this and tell me the three techniques the creator demonstrates on screen that
they never explain out loud."* If the answer is specific and grounded, both
layers are working.

## Step 1 — Pick 10 videos, not the top 10

The best tutorial on any skill is usually made by someone who does the work
and has a fraction of the audience — view count measures thumbnails, not
technique. Pick creators who actually do the work, videos that show a full
process end to end, and **at least two that disagree with each other** so the
agent learns where practitioners genuinely differ.

## Step 2 — Extract and cross-reference

Run this, filling in the role and the 10 URLs:

```
I am building a [ROLE] agent. Here are 10 of the best YouTube tutorials on this skill: [PASTE 10 URLS]

Watch each one using both the transcript and the visual analysis. For each video, extract: the actual methods demonstrated, the specific settings, numbers, and parameters used, the order they do things in, what they explicitly warn against, and anything they do on screen without explaining it.

Then compare all 10 and give me:
- The techniques that appear in most or all of them, these are the fundamentals
- The techniques only one or two use, marked as opinions rather than rules
- Every point where they directly contradict each other, with both positions
- What none of them cover, which tells me where the gaps are

Cite the video and the timestamp for every technique. Do not generalize into advice that no video actually gave.

Then write it as a skill file: when to use this skill, the process step by step, the specific parameters and settings, the rules with the reasoning behind them, the mistakes to avoid, and how to tell when the output is good.
```

Two rules make this trustworthy, not just plausible:

1. **Every technique needs a timestamp.** If it can't point to where a claim
   came from, you can't tell whether it watched the video or predicted what a
   video like that would probably say.
2. **Separate consensus from opinion.** A technique nine creators use is a
   rule; a technique one creator uses is a preference. Don't let the write-up
   flatten that distinction.

If you're saving this as a real skill for this repo, write it to
`skills/<role>/SKILL.md` following the frontmatter format the other skills
here use (`name`, `description`) — see `skills/skill-creator/SKILL.md`.

## Step 3 — Test it, then feed the gaps back

A skill built purely from watching is unproven until it produces something.

```
Run the [ROLE] skill on this real task: [TASK]

Then evaluate the result against the source videos. Which techniques from the skill did you actually apply, and which did you skip? Where did the skill give you no guidance and you had to improvise? Was there any point where two of the source techniques conflicted and you had to choose?

Update the skill file with what was missing. Do not delete anything the videos taught, only add what the gap revealed.
```

Two or three rounds of this and the skill stops being a summary of ten videos
and becomes a working process. When it misses something specific, don't
re-run all ten — find one video that covers that gap and feed just that one
in.

Repeat the whole cycle per role (editing, outreach, ads, content, ...) — each
one built from the ten people who are actually good at that specific thing.

## Pitfalls

- **Popular ≠ useful.** Chasing view count gets you thumbnails, not technique.
- **No timestamp, no trust.** A summary without citations can't be checked
  against the source.
- **Skipping the test round.** An untested skill file just sounds authoritative.
- **Smoothing over disagreement.** When practitioners contradict each other,
  that's a real decision point in the craft — don't average it into one bland
  recommendation.
- **Copying instead of learning.** Extracting techniques is fine; republishing
  someone's actual script or specific creative work is not. You're building a
  process, not lifting a product.
