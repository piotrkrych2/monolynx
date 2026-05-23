# LLM Wiki — Andrej Karpathy

> **Źródło:** https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f  
> **Opublikowany:** 4 kwietnia 2026  
> **Gwiazdki GitHub:** 5 000+  
> **Forks:** 5 000+  
> **Oryginalny tytuł:** `llm-wiki.md`

---

A pattern for building personal knowledge bases using LLMs.

This is an idea file, it is designed to be copy pasted to your own LLM Agent
(e.g. OpenAI Codex, Claude Code, OpenCode / Pi, or etc.). Its goal is to
communicate the high level idea, but your agent will build out the specifics
in collaboration with you.

---

## The core idea

Most people's experience with LLMs and documents looks like RAG: you upload a
collection of files, the LLM retrieves relevant chunks at query time, and
generates an answer. This works, but the LLM is rediscovering knowledge from
scratch on every question. There's no accumulation. Ask a subtle question that
requires synthesizing five documents, and the LLM has to find and piece
together the relevant fragments every time. Nothing is built up. NotebookLM,
ChatGPT file uploads, and most RAG systems work this way.

The idea here is different. Instead of just retrieving from raw documents at
query time, the LLM **incrementally builds and maintains a persistent wiki** —
a structured, interlinked collection of markdown files that sits between you
and the raw sources. When you add a new source, the LLM doesn't just index it
for later retrieval. It reads it, extracts the key information, and integrates
it into the existing wiki — updating entity pages, revising topic summaries,
noting where new data contradicts old claims, strengthening or challenging the
evolving synthesis. The knowledge is compiled once and then *kept current*, not
re-derived on every query.

This is the key difference: **the wiki is a persistent, compounding artifact.**
The cross-references are already there. The contradictions have already been
flagged. The synthesis already reflects everything you've read. The wiki keeps
getting richer with every source you add and every question you ask.

You never (or rarely) write the wiki yourself — the LLM writes and maintains
all of it. You're in charge of sourcing, exploration, and asking the right
questions. The LLM does all the grunt work — the summarizing, cross-referencing,
filing, and bookkeeping that makes a knowledge base actually useful over time.
In practice, I have the LLM agent open on one side and Obsidian open on the
other. The LLM makes edits based on our conversation, and I browse the results
in real time — following links, checking the graph view, reading the updated
pages. **Obsidian is the IDE; the LLM is the programmer; the wiki is the
codebase.**

This can apply to a lot of different contexts. A few examples:

- **Personal**: tracking your own goals, health, psychology, self-improvement —
  filing journal entries, articles, podcast notes, and building up a structured
  picture of yourself over time.
- **Research**: going deep on a topic over weeks or months — reading papers,
  articles, reports, and incrementally building a comprehensive wiki with an
  evolving thesis.
- **Reading a book**: filing each chapter as you go, building out pages for
  characters, themes, plot threads, and how they connect. By the end you have a
  rich companion wiki. Think of fan wikis like Tolkien Gateway — thousands of
  interlinked pages covering characters, places, events, languages, built by a
  community of volunteers over years. You could build something like that
  personally as you read, with the LLM doing all the cross-referencing and
  maintenance.
- **Business/team**: an internal wiki maintained by LLMs, fed by Slack threads,
  meeting transcripts, project documents, customer calls. Possibly with humans
  in the loop reviewing updates. The wiki stays current because the LLM does the
  maintenance that no one on the team wants to do.
- **Competitive analysis, due diligence, trip planning, course notes,
  hobby deep-dives** — anything where you're accumulating knowledge over time
  and want it organized rather than scattered.

---

## Architecture

There are three layers:

**Raw sources** — your curated collection of source documents. Articles, papers,
images, data files. These are immutable — the LLM reads from them but never
modifies them. This is your source of truth.

**The wiki** — a directory of LLM-generated markdown files. Summaries, entity
pages, concept pages, comparisons, an overview, a synthesis. The LLM owns this
layer entirely. It creates pages, updates them when new sources arrive,
maintains cross-references, and keeps everything consistent. You read it; the
LLM writes it.

**The schema** — a document (e.g. `CLAUDE.md` for Claude Code or `AGENTS.md`
for Codex) that tells the LLM how the wiki is structured, what the conventions
are, and what workflows to follow when ingesting sources, answering questions,
or maintaining the wiki. This is the key configuration file — it's what makes
the LLM a disciplined wiki maintainer rather than a generic chatbot. You and
the LLM co-evolve this over time as you figure out what works for your domain.

```
your-project/
├── raw/                    ← Immutable source material (you curate)
│   ├── articles/
│   ├── papers/
│   └── notes/
├── wiki/                   ← LLM-generated knowledge pages (LLM writes)
│   ├── entities/           ← People, orgs, products, places
│   ├── concepts/           ← Topics, ideas, frameworks
│   ├── sources/            ← Per-source summary pages
│   ├── index.md            ← Catalog of all pages
│   └── log.md              ← Append-only operation log
└── CLAUDE.md               ← Schema: structure, conventions, workflows
```

---

## Operations

**Ingest.** You drop a new source into the raw collection and tell the LLM to
process it. An example flow: the LLM reads the source, discusses key takeaways
with you, writes a summary page in the wiki, updates the index, updates relevant
entity and concept pages across the wiki, and appends an entry to the log. A
single source might touch 10–15 wiki pages. Personally I prefer to ingest
sources one at a time and stay involved — I read the summaries, check the
updates, and guide the LLM on what to emphasize. But you could also batch-ingest
many sources at once with less supervision. It's up to you to develop the
workflow that fits your style and document it in the schema for future sessions.

**Query.** You ask questions against the wiki. The LLM searches for relevant
pages, reads them, and synthesizes an answer with citations. Answers can take
different forms depending on the question — a markdown page, a comparison table,
a slide deck (Marp), a chart (matplotlib), a canvas. The important insight:
**good answers can be filed back into the wiki as new pages.** A comparison you
asked for, an analysis, a connection you discovered — these are valuable and
shouldn't disappear into chat history. This way your explorations compound in
the knowledge base just like ingested sources do.

**Lint.** Periodically, ask the LLM to health-check the wiki. Look for:
contradictions between pages, stale claims that newer sources have superseded,
orphan pages with no inbound links, important concepts mentioned but lacking
their own page, missing cross-references, data gaps that could be filled with a
web search. The LLM is good at suggesting new questions to investigate and new
sources to look for. This keeps the wiki healthy as it grows.

---

## Indexing and logging

Two special files help the LLM (and you) navigate the wiki as it grows. They
serve different purposes:

**index.md** is content-oriented. It's a catalog of everything in the wiki —
each page listed with a link, a one-line summary, and optionally metadata like
date or source count. Organized by category (entities, concepts, sources, etc.).
The LLM updates it on every ingest. When answering a query, the LLM reads the
index first to find relevant pages, then drills into them. This works
surprisingly well at moderate scale (~100 sources, ~hundreds of pages) and
avoids the need for embedding-based RAG infrastructure.

**log.md** is chronological. It's an append-only record of what happened and
when — ingests, queries, lint passes. A useful tip: if each entry includes what
changed and why, the log becomes a high-level history of how your understanding
of a topic evolved. Useful for auditing the wiki and for orienting new sessions.

---

## Tips and notes

**On page granularity.** The right level of granularity is: one page per concept
or entity, sized such that you could read the whole page in one sitting and
understand the subject well. Not one paragraph per concept (too thin, not useful
for synthesis), not one mega-document (too hard for the LLM to maintain and
update incrementally). Think of Wikipedia pages as a rough guide to scope.

**On contradictions.** The LLM should never silently overwrite old claims with
new ones. When a new source contradicts existing wiki content, the LLM should
flag the contradiction explicitly on the affected pages. Something like:
`> **Contradiction noted [date]:** Source A says X; Source B says Y. Unresolved.`
This keeps the wiki honest. You can resolve contradictions manually or ask the
LLM to weigh the sources and make a call — but the flag should stay until you
decide.

**On the schema.** The schema (CLAUDE.md or AGENTS.md) is the most important
file. Write it collaboratively — start a session, ask the LLM to help you design
the page structure and naming conventions for your domain, and document them.
Then paste in Karpathy's core operations above as the default workflows. As you
use the wiki, you'll discover what works and what doesn't — update the schema.
Maybe you need a new page type. Maybe your frontmatter needs more fields. Maybe
your ingest workflow should include a step you didn't anticipate.

You and the LLM co-evolve this over time.

**On tools.** The raw/ directory can contain anything: markdown, PDF, plain
text, HTML. The wiki/ directory should be pure markdown — readable by both you
and the LLM without any preprocessing. I use Obsidian to browse the wiki in
real time as the LLM edits it. The graph view is particularly useful for
spotting isolated nodes (orphan pages) and dense clusters (core concepts that
keep being referenced). You don't need Obsidian — any markdown viewer works —
but the graph view and backlinks make the structure visible in a way that's hard
to replicate otherwise.

**On scale.** This approach works well up to a few hundred sources and a few
hundred wiki pages with a capable model and a large context window. Beyond that,
you may need to shard the index, use embeddings for initial retrieval, or break
the wiki into domains. But most personal knowledge bases never reach that scale,
so this approach is probably sufficient.

---

## Historical context

The idea is related in spirit to Vannevar Bush's **Memex** (1945) — a personal,
curated knowledge store with associative trails between documents. Bush's vision
was closer to this than to what the web became: private, actively curated, with
the connections between documents as valuable as the documents themselves. The
part he couldn't solve was who does the maintenance.

**The LLM handles that.**

---

## Quick-start prompt

To bootstrap your wiki, paste the following into Claude Code after reading this
file:

```
You are my LLM Wiki agent. I want to build a personal knowledge base using
the pattern described in this file. Help me:

1. Create the folder structure: raw/, wiki/, and the schema file (CLAUDE.md)
2. Design the page types and naming conventions for my domain
3. Set up index.md and log.md
4. Walk me through ingesting my first source

Ask me one question to get started: what is this wiki for?
```

---

*Andrej Karpathy — gist published April 4, 2026*  
*https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f*  
*5 000+ stars · 5 000+ forks*