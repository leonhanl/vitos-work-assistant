---
name: enterprise-knowledge-search
description: Use this skill when a request may depend on company-internal knowledge such as IT KB articles, policies, internal procedures, employee guides, product documents, FAQs, SharePoint, or Microsoft 365 content. Do not use it for ordinary public knowledge, math, or generic programming questions.
---

# Enterprise knowledge search

Use the available `search_sharepoint` and `read_document` tools to answer from real
enterprise material. Do not add internal facts from general model knowledge.

## Search well

1. Rewrite the user's request into a short lexical query. Prefer core nouns, product
   or system names, exact error text, actions, and likely synonyms. Do not simply send
   a long natural-language question.
2. For example, “我出差的时候怎么访问公司内部系统？” could become `corporate VPN
   remote access travel` or `VPN remote access`.
3. If the first query has no suitable result, try a meaningfully different synonym or
   narrower query. Stop after at most three searches, and stop earlier when results are
   strong enough.

## Select and read

Evaluate `name`, `summary`, and `rank` before reading. Read only the most relevant few
documents rather than every result. When the answer needs steps, policy details,
configuration, limitations, or version requirements, use `read_document`; a search
summary alone is normally insufficient.

## Ground the answer

- Base internal claims on returned search results and document content.
- If results are absent, inconsistent, or insufficient, say: “目前检索到的企业资料不足以
  确认这个问题。”
- Cite only document names and `web_url` values actually returned by the tools. Never
  invent a source or URL.

