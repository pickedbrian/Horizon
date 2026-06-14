---
layout: default
title: Scoring System
---

# Scoring System

After fetching content from all sources, Horizon uses an AI model to score each item on a 0-10 scale. The default scoring prompt is tuned for a focused AI/ML daily digest, not a general technology digest.

## Pipeline

1. **Batch processing** — Items are scored in batches of 10 with a progress bar. Failed items receive a score of 0.
2. **Content preparation** — For each item, the content is truncated (800 chars if comments are present, 1000 otherwise) and engagement metrics are assembled from metadata (HN score, Reddit upvote ratio, etc.).
3. **AI analysis** — The prepared content is sent to the configured AI model (temperature 0.3) with a system prompt defining the scoring criteria.
4. **Response parsing** — The AI response is parsed as JSON (with fallbacks for code-block-wrapped JSON). Each item gets: `ai_score` (float), `ai_reason` (string), `ai_summary` (string), and `ai_tags` (list).
5. **Retry** — Failed AI calls are retried up to 3 times with exponential backoff (2-10 seconds).

## Scoring Scale

| Score | Tier | Description |
|-------|------|-------------|
| 9-10 | Groundbreaking AI | Major AI breakthroughs, model releases, capability jumps, or industry-changing AI announcements |
| 7-8 | High Value AI | Important AI research, AI engineering deep-dives, AI tools, datasets, benchmarks, or infrastructure |
| 5-6 | Related but Not Essential | Incremental AI updates, useful AI tutorials, or AI-adjacent infrastructure |
| 3-4 | Low Priority | Weak AI relevance, routine AI updates, or general technical content with only a loose AI mention |
| 0-2 | Noise | Spam, off-topic, trivial updates, or general technology content with no direct AI/ML relevance |

## Scoring Factors

The AI evaluates each item based on:

- **AI relevance first** — non-AI items should rarely score above 4
- **Technical depth and novelty in AI/ML** — original ideas, new techniques, research contributions
- **Potential impact** — how broadly this affects AI builders, researchers, products, or users
- **Quality of writing/presentation** — clarity, structure, thoroughness
- **Community discussion** — insightful comments, diverse viewpoints, substantive debates
- **Engagement signals** — high upvotes/favorites paired with substantive discussion (not just raw numbers)

Engagement metadata is source-specific: HN provides score and comment count, Reddit provides upvote ratio and comment count.

## Filtering

After scoring, items are filtered by `filtering.ai_score_threshold` (default: `5.0`) and sorted by score descending. Optional balanced digest quotas are then applied before enrichment.

```json
{
  "filtering": {
    "ai_score_threshold": 5.0,
    "time_window_hours": 24,
    "max_items": 20,
    "category_groups": {
      "ai": {
        "limit": 5,
        "categories": ["ai-news", "ai-tools", "machine-learning"]
      }
    }
  }
}
```

`category_groups` limits each configured category group independently.
`max_items` caps the merged result. Both fields are optional; without them,
scoring and filtering behave as before.

Items scoring 9.0 or above are featured in the "Today's Highlights" section of the summary.

## Enrichment

Items that pass the score threshold and any balanced digest limits go through a second AI pass for enrichment (`src/ai/enricher.py`):

1. **Concept extraction** — AI identifies 1-3 technical concepts in the item that may need explanation.
2. **Web search** — Each concept is searched via the configured provider (`duckduckgo` by default, or `serper`) to gather grounding context.
3. **Structured analysis** — The item content and search results are sent to AI, which produces:
   - `whats_new` — what specifically happened or changed
   - `why_it_matters` — significance and impact
   - `key_details` — notable technical details or caveats
   - `background` — background knowledge for readers without deep domain expertise

These fields are combined into a `detailed_summary` stored in the item's metadata and used in the final daily summary.
