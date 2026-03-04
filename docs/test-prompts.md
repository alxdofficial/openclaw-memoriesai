# LUCI / DETM Test Prompts

Copy-paste one at a time into OpenClaw using the run command shown for each task.

**Tasks 1–5** — Marketing tasks grounded in real sources or fixed datasets.
All data lives in Google Sheets/Docs so the agent must browse to read it — it cannot make numbers up.
**Tasks 6–11** — DETM browser automation tests (default agent).

---

## Task 12 — Bakery TikTok Trend Intelligence
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt below>"`
**Use case:** Spot trending sounds, formats, and content templates for a bakery brand
**Timeout:** 20 minutes
**Grounding:** Live TikTok data via visual browser

```
DETM ONLY

Use DETM to track this task. Create ~/luci-test/ if it doesn't exist.

## Brand Brief: Crumb & Co.
- Small-batch artisan bakery based in London
- Speciality: sourdough, croissants, seasonal pastries, custom celebration cakes
- Tone: warm, tactile, craft-focused — the smell of a bakery in video form
- Target audience: 22–35 food lovers, home bakers, brunch crowd
- Platforms: TikTok (primary), Instagram Reels (secondary)
- Content pillars: baking process (lamination, scoring, proofing), product reveals,
  behind-the-scenes kitchen, customer reactions, seasonal specials
- Handle goal: @crumbandco
- Hashtags: #SourdoughTok #BakeryTok #CrumbAndCo #ArtisanBread #CroissantTok

---

## Step 1 — Open TikTok

Use live_ui to open a browser and navigate to https://www.tiktok.com

Pass this instruction to live_ui:
"Open Firefox, navigate to https://www.tiktok.com, and wait for the page to load.
If a login wall or sign-in prompt appears, call escalate with the message 'TikTok is asking for login'.
If a CAPTCHA appears, call escalate with 'TikTok is showing a CAPTCHA'.
Otherwise call done once the TikTok homepage or For You feed is visible."

If live_ui returns escalated=True:
  Call task_update with the escalation reason and ask the user to resolve it in the
  browser (display :99), then poll task_update(query="status") every 20 seconds
  until the user confirms. Then continue.

Take a desktop_look to confirm what you can see.

---

## Step 2 — Search for bakery-relevant content

For each of the following search terms, use live_ui to navigate to the results:

Search terms:
1. "sourdough"
2. "croissant recipe"
3. "bakery aesthetic"
4. "bread scoring"
5. "pastry asmr"

For each search term:

a) Use live_ui with instruction:
   "Click the search bar at the top of TikTok, type '[TERM]', press Enter,
   and wait for search results to load. If results show a 'Videos' tab, click it.
   Call done once you can see video thumbnails for the search term."

b) After live_ui returns, take a desktop_look to see the results page.
   Read the visible thumbnails: note creator handles, approximate view counts
   shown on thumbnails, and any hashtags or sound names visible.

c) If a video is actively playing or has a clearly visible sound name in the corner,
   call mavi_understand with a 10-second duration and this prompt:
   "What is the name of the sound or music playing in the TikTok video on screen?
   What is the visual format of the video — talking head, process video, timelapse,
   ASMR closeup, or something else? What text overlays or captions are visible?"

d) Record your findings for this search term before moving to the next.

---

## Step 3 — Browse the For You Feed

Use live_ui to navigate back to the TikTok homepage:
"Navigate to https://www.tiktok.com and scroll down slowly through the For You
feed — pause for 2 seconds between each scroll. Do 8 scrolls total.
Call done when you have completed 8 scrolls."

After live_ui returns, take a desktop_look to see the current state of the feed.

For any video that is playing and shows a sound name or interesting format, call
mavi_understand (10–12 seconds) with a specific prompt. Good prompts to use:

- "What is the name of the sound or song playing in this TikTok? Is it a trending
  audio, original sound, or music? What does the creator's visual style look like?"
- "Describe the video format in detail: is it a process video, a talking head, a
  product reveal, a timelapse, ASMR, or something else? What text overlays appear?"
- "What hashtags are shown in the caption of the video currently on screen?
  What niche does this creator appear to be in?"

Use mavi_understand for 3–5 videos where motion or audio gives you information
you could not get from a static screenshot. Use desktop_look for everything else.

---

## Step 4 — Identify top trends for Crumb & Co.

Based on all observations from Steps 2 and 3, identify the top 5 trends most
relevant to an artisan bakery. For each trend write:

1. Trend name (exact sound name, hashtag, or format descriptor as seen on screen)
2. Evidence (where you saw it, how many times, approximate view counts if visible)
3. Why it works for Crumb & Co. (one sentence — specific to the bakery brief)
4. Content idea (one concrete video concept: what would be filmed, what sound,
   what caption hook)
5. Urgency (Hot right now / Evergreen / Seasonal)

---

## Step 5 — Output

Save the full report as ~/luci-test/crumb-trends-[DATE].md where DATE is
today's date (YYYY-MM-DD). Include:
- A header with the brand name and date
- The top 5 trends in the format above
- A "Sounds to license or duet" section listing any specific audio tracks noted
- A "Formats to test first" section with 2–3 quick-win video formats observed

Then call task_update with a plain-text summary of the top 5 trends so the user
can read them directly in the dashboard without opening the file.
```

---

## Task 1 — TikTok Trend Intelligence
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt below>"`
**Use case:** Spot trends the brand can ride
**Timeout:** 15 minutes
**Grounding:** Live data from TikTok.com (requires login — cookies persist after first run)

```
DETM ONLY

You are working on behalf of Surge Energy — a UK natural energy drink targeting
18–25 students and young professionals. Key details:
- Product: 250ml natural energy cans (green tea extract, lion's mane mushroom)
- Tone: no-BS, clean, authentic
- Promo code: SURGE15
- Platforms: TikTok (primary), Instagram Reels (secondary)

Use DETM to track this task. Create ~/luci-test/ if it doesn't exist.

## Step 1 — Open TikTok

Open a browser and navigate to https://www.tiktok.com

Take a screenshot and inspect what you see.

IMPORTANT — if you see a login page or sign-in prompt:
  Call task_update with message: "TikTok is asking for login. Please log in using
  the browser on display :99, then reply to this task when done."
  Then call task_update(query="status") every 30 seconds until you see a user
  message confirming login is complete. Once confirmed, continue.

IMPORTANT — if you see a CAPTCHA or bot-detection challenge:
  Call task_update with message: "TikTok is showing a CAPTCHA. Please solve it
  in the browser on display :99, then reply to this task when done."
  Then poll task_update(query="status") every 15 seconds until confirmed, then continue.

## Step 2 — Search for relevant content

Once on TikTok, use the search bar to search for each of the following terms
one at a time. For each search, switch to the "Trending" or "Top" sort if available.

Search terms:
1. "energy drink"
2. "study with me"
3. "morning routine drink"

For each search result page, take a screenshot and record:
- The top 3 visible videos: creator handle, approximate view count (shown on thumbnail),
  and any visible sound name or hashtag shown on the post
- Any hashtags shown in the trending sidebar or suggestions panel

## Step 3 — Browse the For You feed

Navigate back to https://www.tiktok.com and scroll the For You feed slowly
(one scroll per second, up to 10 scrolls). After each scroll take a screenshot.
Note any sounds, formats, or hashtags that appear repeatedly across multiple videos.

When a video is actively playing and you want to understand the sound, visual
style, or caption content in detail, call mavi_understand with a duration of
10–15 seconds and a specific prompt. Examples:
- "What is the name of the sound or song playing in this TikTok video?"
- "What hashtags are shown in the caption of the video on screen?"
- "Describe the visual format and content style of this video."

For static UI elements (search results, sidebar hashtags, view counts),
use desktop_look — mavi_understand is only worth calling when motion or
audio is the key information you need.

## Step 4 — Compile findings

Based on what you observed in Steps 2 and 3, identify the top 5 trends most
relevant to Surge Energy. For each trend write:

1. Trend name (exact sound name, hashtag, or format descriptor as seen on screen)
2. Evidence (where you saw it — search result, FYP feed, how many times)
3. View/post counts if visible
4. Surge Energy content idea (one sentence — specific, not generic)
5. Best influencer type for this trend (e.g. student lifestyle, gym/fitness,
   foodie, productivity — based on what you observed in the content)

## Step 5 — Output

Save the full trend report as ~/luci-test/trend-intel-[DATE].md where DATE is
today's date (YYYY-MM-DD).

Then call task_update with a message summarising the top 5 trends in plain text
so the user can read them directly in the dashboard without opening the file.
```

---

## Task 2 — Campaign A/B Analysis
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt>"`
**Use case:** Compare two real campaign runs to decide Q2 strategy
**Timeout:** 10 minutes
**Grounding:** Fixed dataset in Google Sheets (fabricated but treated as ground truth)
**Prereq:** Upload `docs/test-data/campaign-a-macro.csv` and `docs/test-data/campaign-b-micro.csv` to a Google Sheet with two tabs, then replace `[SHEET_URL_CAMPAIGN_COMPARISON]` below.

```
DETM ONLY

Use DETM to track this task.

Create ~/luci-test/ if it doesn't exist.

Open a browser and go to this Google Sheet:
[SHEET_URL_CAMPAIGN_COMPARISON]

The sheet has two tabs:
- "Campaign A - Macro" — 5 macro influencers (400K–1.2M followers), January 2025
- "Campaign B - Micro" — 20 micro influencers (12K–42K followers), February 2025

Both campaigns had a £5,000 budget. The goal was to grow brand awareness and
drive first-time purchases for Surge Energy (a UK natural energy drink).

Read all rows in both tabs. Calculate for each campaign:
- Total views
- Total engagements (likes + comments + shares)
- Average engagement rate (total engagements / total views)
- Total discount codes used
- Cost per engagement (spend / total engagements)
- Cost per code redemption (spend / discount codes used)

Then write a comparison report saved as ~/luci-test/ab-comparison.md that
includes:
1. A side-by-side summary table with the above metrics
2. Which campaign performed better for brand awareness vs. conversions, and why
3. A clear recommendation for where to put the Q2 budget
```

---

## Task 3 — Weekly Creator Performance Check
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt>"`
**Use case:** Catch underperformers and missed deadlines before end of week
**Timeout:** 8 minutes
**Grounding:** Fixed dataset in Google Sheets
**Prereq:** Upload `docs/test-data/weekly-performance-feb10.csv` to a Google Sheet, then replace `[SHEET_URL_WEEKLY_PERFORMANCE]` below.

```
DETM ONLY

Use DETM to track this task.

Create ~/luci-test/ if it doesn't exist.

Open a browser and go to this Google Sheet:
[SHEET_URL_WEEKLY_PERFORMANCE]

This is the Surge Energy campaign tracker for the week of Feb 10–16 2025.
It shows each active creator's contracted post deadline, target views,
actual views, target engagement rate, actual engagement rate, and
discount codes used.

Read all rows. Identify:
1. Any creator who missed their posting deadline (published = 0)
2. Any creator whose actual_views are more than 20% below their views_target
3. Any creator whose actual_engagement_rate is below their eng_rate_target

Write a weekly performance report saved as ~/luci-test/weekly-report-feb10.md.
Format it as you would an internal Slack update to the campaign manager:
- Overall summary (one paragraph)
- Green list: creators on track
- Red list: creators needing action, with specific numbers and suggested next step
  (e.g. chase for post, request reshoot, review content brief)
```

---

## Task 4 — Post-Campaign ROI Report
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt>"`
**Use case:** Executive summary after both campaign phases complete
**Timeout:** 10 minutes
**Grounding:** Fixed dataset in Google Sheets
**Prereq:** Upload `docs/test-data/roi-summary.csv` to a Google Sheet, then replace `[SHEET_URL_ROI_SUMMARY]` below.

```
DETM ONLY

Use DETM to track this task.

Create ~/luci-test/ if it doesn't exist.

Open a browser and go to this Google Sheet:
[SHEET_URL_ROI_SUMMARY]

This sheet contains the final aggregated metrics for Surge Energy's two
influencer campaign phases (Campaign A: macro, Campaign B: micro).
Each row is a metric, each column is a campaign.

Read all rows. Then write an executive-ready ROI report saved as
~/luci-test/roi-report.md that includes:

1. Campaign overview table (spend, creators, views, engagements, ROAS, CPA)
2. Key insight: which campaign delivered better ROI and why
3. The ROAS formula used: revenue_attributed / total_spend
4. Cost efficiency comparison: cost per engagement and cost per acquisition
   side by side
5. Recommendation for Q2: budget split, creator tier mix, and one tactical
   change based on the data

The report should be suitable for a 5-minute investor or CMO briefing.
```

---

## Task 5 — 30-Day Content Calendar
**Agent:** `influencer-mgmt`
**Run:** `openclaw agent --agent influencer-mgmt --message "<prompt>"`
**Use case:** Build March 2025 content calendar for Surge Energy
**Timeout:** 15 minutes
**Grounding:** Brand brief (in this prompt) + live TikTok Creative Center trends

```
DETM ONLY

Use DETM to track this task.

Create ~/luci-test/ if it doesn't exist.

## Brand Brief: Surge Energy
- Natural energy drink, 250ml cans, UK brand
- Target: 18–25 students and young professionals
- Key ingredients: green tea extract (150mg caffeine), lion's mane mushroom
- Tone: no-BS, clean, authentic — not extreme sports, not corporate wellness
- Platforms: TikTok (primary), Instagram Reels (secondary)
- Content pillars: morning routine, study sessions, pre/post gym, taste content
- Promo code: SURGE15 | Hashtags: #SurgeEnergy #NaturalEnergy #CleanEnergy
- Avoid: health claims, competitor mentions, overly produced content

## Your task:
First, open a browser and go to:
https://ads.tiktok.com/business/creativecenter/trends/hub/pc/en

Browse trending sounds and formats. Note at least 3 trends currently active
in the wellness, fitness, or food/drink space that could work for Surge Energy.

Then build a 30-day content calendar for March 2025 (March 1–31) across
TikTok and Instagram. For each post include:
- Date
- Platform
- Format (TikTok video, Reel, carousel, etc.)
- Hook/concept (one sentence)
- Caption draft (2–3 sentences)
- Hashtags (4–6)
- Relevance note (why this will resonate — trending sound, cultural moment,
  seasonal tie-in, etc.)

Flag 4 "reactive slots" (leave the concept blank) with a note on what type
of real-time moment would justify using them.

Save as ~/luci-test/content-calendar.md.
At the top, list the 3 TikTok trends you found and the URLs where you saw them.
```

---

## Task 6 — Influencer Discovery Report
**Agent:** `main` (default)
**Run:** `openclaw --message "<prompt>"`
**Use case:** 1.1 Influencer Intelligence Agent
**Timeout:** 12 minutes

```
Use DETM to track this task. Use the visual browser when it makes sense
(navigating sites, filling forms, reading pages). Fall back to web_search
or other tools when faster and the result is equivalent.

Create a directory ~/luci-test/ if it doesn't exist.

Open a web browser and navigate to YouTube. Search for fitness creators
who regularly discuss supplements and protein. Find 8 channels that appear
to have a meaningful following and consistent upload history in this niche.

For each creator record:
- Channel name
- Channel URL (copy from the address bar)
- Approximate subscriber count (visible on channel page)
- Primary content focus (1 sentence)

Save the results as ~/luci-test/influencer-shortlist.md in a clean markdown
table. At the top, include a one-paragraph summary of common themes you
noticed across these creators' content.
```

---

## Task 7 — Competitive Landscape Report
**Agent:** `main` (default)
**Run:** `openclaw --message "<prompt>"`
**Use case:** 1.2 Competitive Video Intelligence
**Timeout:** 15 minutes

```
Use DETM to track this task. Use the visual browser when it makes sense
(navigating sites, filling forms, reading pages). Fall back to web_search
or other tools when faster and the result is equivalent.

Create a directory ~/luci-test/ if it doesn't exist.

Use a browser to research 5 AI-powered video tools that compete in the
video intelligence or video analytics space (e.g. tools that do things like
video search, auto-tagging, highlight generation, or content repurposing).
Search Google and visit each product's website directly.

For each competitor record:
- Product name and company
- Website URL (from the address bar)
- Core feature set (3-5 bullets)
- Target customer (enterprise, SMB, creator, etc.)
- Pricing tier if publicly listed
- Any notable differentiator

Save as ~/luci-test/competitive-landscape.md. End the document with a
"Gap Analysis" section: based on what you found, what capabilities appear
underserved or missing across these tools?
```

---

## Task 8 — Vertical Video Conversion (FFmpeg)
**Agent:** `main` (default)
**Run:** `openclaw --message "<prompt>"`
**Use case:** 3.1 Format Conversion at Scale
**Timeout:** 8 minutes

```
Create a directory ~/luci-test/ if it doesn't exist.

Download a short publicly available sample video (under 30 seconds, any
format) using wget or curl. Use a well-known public sample video source.

Then use ffmpeg to:
1. Crop and resize it to 1080x1920 (9:16 vertical/portrait format),
   center-cropping the original frame horizontally
2. Limit output to the first 20 seconds if longer
3. Save the result as ~/luci-test/vertical-output.mp4

After conversion, run ffprobe on the output and confirm the resolution
is 1080x1920. Save the ffprobe output as ~/luci-test/ffprobe-result.txt.
```

---

## Task 9 — Sales Meeting Prep Brief
**Agent:** `main` (default)
**Run:** `openclaw --message "<prompt>"`
**Use case:** 9.1 Meeting Prep Agent
**Timeout:** 15 minutes

```
Use DETM to track this task. Use the visual browser when it makes sense
(navigating sites, filling forms, reading pages). Fall back to web_search
or other tools when faster and the result is equivalent.

Create a directory ~/luci-test/ if it doesn't exist.

You have a demo call with HubSpot in 30 minutes. Use a browser to research
them as a prospect.

1. Visit hubspot.com — note their current product focus and any prominently
   featured use cases or customer stories visible on the page
2. Search Google for "HubSpot news 2025 2026" — note any recent product
   announcements, partnerships, or strategic moves from the visible results
3. Find HubSpot's YouTube channel and note what types of content they post
   and any recent themes visible on the channel page

Compile everything into ~/luci-test/hubspot-brief.md with these sections:
- Company Overview (3-4 sentences)
- Recent Initiatives (bullet points from news research)
- Video Content Strategy (what their YouTube presence looks like)
- Potential Pain Points (where a video intelligence tool like LUCI
  could add value for a company like HubSpot)
- Recommended Talking Points (3-4 specific angles for the demo)
```

---

## Task 10 — AI Reporter List (100 contacts)
**Agent:** `main` (default)
**Run:** `openclaw --message "<prompt>"`
**Use case:** Press / media outreach research
**Timeout:** take as long as you need

```
Use DETM to track this task. Use the visual browser when it makes sense.
Fall back to web_search or other tools when faster and equivalent.

Use a browser to find 100 journalists and reporters who cover AI at major
tech publications: Business Insider, Forbes, TechCrunch, The Verge, Wired,
MIT Technology Review, Bloomberg Technology, Reuters, VentureBeat, CNET,
Ars Technica. Aim for ~9 per publication.

Start by visiting each publication's staff/authors page and find names from
there — do not rely on names you already know.

For each reporter collect:
- Full name
- Publication
- Beat / focus area (AI, ML, tech policy, etc.)
- LinkedIn profile URL (search LinkedIn in the browser if needed)

When done, save the full list as ~/luci-test/ai-reporters.csv with columns:
name, publication, beat, linkedin_url

Send the CSV file in the chat when complete.
```

---

## Task 11 — LinkedIn Posts from Research
**Agent:** `linkedin-networking`
**Run:** `openclaw agent --agent linkedin-networking --message "<prompt>"`
**Use case:** 5.2 Social Outreach Agent + 3.3 Content Remix
**Timeout:** 12 minutes

```
DETM ONLY

Use DETM to track this task. Use the visual browser when it makes sense
(navigating sites, filling forms, reading pages). Fall back to web_search
or other tools when faster and the result is equivalent.

Create a directory ~/luci-test/ if it doesn't exist.

Open a browser and search YouTube for a recent (2024 or 2025) talk, panel,
or webinar about "AI in sales" or "AI for sales teams".

Pick the most relevant result and watch the first 3-5 minutes of it.
You can use the spacebar to pause if needed.

Based on what you see and hear in those opening minutes, write 3 LinkedIn
post drafts targeting different angles:
- Post A: thought leadership (an insight or trend from the video)
- Post B: practical tip (something actionable a sales team could do)
- Post C: engagement question (pose a question to drive comments)

Each post: 150-200 words, professional but conversational tone, 3-5 hashtags.

Save all three to ~/luci-test/linkedin-posts.md with clear headers.
At the top, include the video title and URL from the address bar.
```
