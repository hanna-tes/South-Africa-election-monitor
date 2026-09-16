window.DisarmLLM = (() => {
  const core = window.DisarmCore;

  const TECHNIQUE_ARRAY_KEYS = [
    'techniques', 'techniques_used', 'technique_details',
    'techniques_details', 'techniques_applied',
  ];
  const DOSSIER_ECHO_KEYS = ['evidence_posts', 'signal_totals', 'account_count'];

  function normalizeBaseUrl(baseUrl) {
    const trimmed = String(baseUrl || '').trim().replace(/\/+$/, '');
    if (!trimmed) throw new Error('Missing base URL.');
    if (trimmed.includes('api.anthropic.com')) return trimmed;
    return trimmed.endsWith('/chat/completions') ? trimmed : `${trimmed}/chat/completions`;
  }

  function isAnthropicEndpoint(baseUrl) {
    return String(baseUrl || '').includes('api.anthropic.com');
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  /* ── adjudicateNetworks: batched to avoid overwhelming the Colab GPU ── */
  async function adjudicateNetworks(networks, settings) {
    const BATCH_SIZE = 3;   // max parallel requests per batch
    const BATCH_PAUSE = 800; // ms between batches
    const results = [];

    for (let i = 0; i < networks.length; i += BATCH_SIZE) {
      const batch = networks.slice(i, i + BATCH_SIZE);
      const settled = await Promise.allSettled(
        batch.map(network => adjudicateWithRetry(network, settings))
      );
      settled.forEach((item, idx) => {
        if (item.status === 'fulfilled') results.push(item.value);
        else console.error(`LLM adjudication failed for ${batch[idx]?.id}`, item.reason);
      });
      if (i + BATCH_SIZE < networks.length) await sleep(BATCH_PAUSE);
    }

    return results.filter(Boolean);
  }

  /* ── Retry wrapper: on 500/502, wait 2 s and retry with a trimmed prompt ── */
  async function adjudicateWithRetry(network, settings, maxAttempts = 2) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        return await adjudicateOneNetwork(network, settings, attempt > 1);
      } catch (err) {
        const isServerError = /50[0-9]/.test(err.message || '');
        if (attempt < maxAttempts && isServerError) {
          console.warn(`Attempt ${attempt} failed for ${network.id} (${err.message}) — retrying in 2 s with trimmed prompt…`);
          await sleep(2000);
        } else {
          throw err;
        }
      }
    }
  }

  async function adjudicateOneNetwork(network, settings, trimPrompt = false) {
    const baseUrl = normalizeBaseUrl(settings.baseUrl);
    const dossier = network.llmDossier;

    // On retry, cut evidence_posts to 3 to reduce GPU memory pressure
    const payload = trimPrompt && (dossier?.evidence_posts?.length ?? 0) > 3
      ? { ...dossier, evidence_posts: dossier.evidence_posts.slice(0, 3) }
      : dossier;

    const userContent = [
      'You are a DISARM TTP adjudicator. Evaluate this network dossier.',
      '',
      'STRICT OUTPUT RULES:',
      '- Return ONLY a JSON object. No explanations, no extra text, no markdown fences.',
      '- Do NOT repeat the input dossier. Do NOT include evidence_posts in your output.',
      '- JSON must have exactly these keys: qualifies (bool), reason (string), techniques (array).',
      '- Each techniques item: { "technique_id": "T0049.002", "score": 0-100, "justification": "...", "evidence_post_indices": [0,1,2] }',
      '- Use only technique IDs from the allowed_techniques list in the dossier.',
      '- If evidence is insufficient return: {"qualifies":false,"reason":"...","techniques":[]}',
      '',
      JSON.stringify(payload, null, 2),
    ].join('\n');

    let content;

    if (isAnthropicEndpoint(baseUrl)) {
      const endpoint = baseUrl.endsWith('/v1/messages')
        ? baseUrl
        : `${baseUrl.replace(/\/v1\/?$/, '')}/v1/messages`;

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': settings.apiKey || '',
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: settings.model || 'claude-sonnet-4-20250514',
          max_tokens: 1024,
          system: settings.systemPrompt || 'You are a disinformation analyst. Return only valid JSON.',
          messages: [{ role: 'user', content: userContent }],
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Anthropic API error (${response.status}): ${text.slice(0, 200)}`);
      }
      const body = await response.json();
      content = body?.content?.[0]?.text || '';

    } else {
      const response = await fetch(baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(settings.apiKey ? { Authorization: `Bearer ${settings.apiKey}` } : {}),
        },
        body: JSON.stringify({
          model: settings.model,
          max_tokens: 1024,
          temperature: Number(settings.temperature ?? 0.1),
          ...(settings.useJsonMode === false ? {} : { response_format: { type: 'json_object' } }),
          messages: [
            { role: 'system', content: settings.systemPrompt },
            { role: 'user', content: userContent },
          ],
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Model endpoint error (${response.status}): ${text.slice(0, 200)}`);
      }
      const body = await response.json();
      content = body?.choices?.[0]?.message?.content || '';
    }

    console.log(`RAW MODEL OUTPUT (${network.id}):`, content);

    const parsed = parseJsonObject(content);

    if (isDossierEcho(parsed)) {
      console.warn(`Dossier echo detected for ${network.id} — salvaging technique fields.`);
      return buildResult(network, extractAdjudicationFromEcho(parsed), settings);
    }

    return buildResult(network, parsed, settings);
  }

  function isDossierEcho(parsed) {
    if (!parsed || typeof parsed !== 'object') return false;
    return DOSSIER_ECHO_KEYS.some(k => k in parsed);
  }

  function extractAdjudicationFromEcho(parsed) {
    const items = findTechniqueItems(parsed);
    if (!items.length) return { qualifies: false, reason: 'Dossier echo — no technique data found.', techniques: [] };
    return {
      qualifies: true,
      reason: parsed.overall_assessment || parsed.analysis_summary || 'Extracted from dossier echo.',
      techniques: items,
    };
  }

  function buildResult(network, parsed, settings) {
    const candidateTechniques = parseTechniques(parsed, network);
    const qualifies = parsed?.qualifies === true || candidateTechniques.length > 0;
    if (!qualifies) return { ...network, candidateTechniques: [] };
    return {
      ...network,
      candidateTechniques,
      adjudication: {
        source: settings.label,
        reason: String(parsed?.reason || parsed?.overall_assessment || '').trim(),
      },
    };
  }

  function findTechniqueItems(parsed) {
    for (const key of TECHNIQUE_ARRAY_KEYS) {
      const val = parsed?.[key];
      if (!Array.isArray(val) || !val.length) continue;
      if (typeof val[0] === 'string') {
        const details = parsed.technique_details || parsed.techniques_details || parsed.techniques_used || [];
        if (details.length) return details;
        return val.map(id => ({ technique_id: id, score: 70, justification: '', evidence_post_indices: [] }));
      }
      if (typeof val[0] === 'object' && val[0].technique_id) return val;
    }
    return [];
  }

  function parseTechniques(parsed, network) {
    const catalog = core.TECHNIQUE_CATALOG || {};
    const items = findTechniqueItems(parsed);
    return items
      .map(item => {
        const id = String(item.technique_id || '').trim();
        if (!catalog[id]) return null;
        const evidencePosts = pickEvidencePosts(
          network.llmDossier?.evidence_posts || [],
          item.evidence_post_indices || [],
          String(item.justification || parsed?.reason || 'Model-judged evidence.')
        );
        if (!evidencePosts.length) return null;
        return {
          techniqueId: id,
          name: catalog[id].name,
          summary: catalog[id].summary,
          sourceUrl: catalog[id].sourceUrl,
          score: clampNumber(item.score, 0, 100, 70),
          confidence: confidenceLabel(clampNumber(item.score, 0, 100, 70)),
          justification: String(item.justification || parsed?.reason || '').trim() || catalog[id].summary,
          evidencePosts,
        };
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
  }

  function parseJsonObject(text) {
    const raw = String(text || '').trim();
    try { return JSON.parse(raw); } catch (_) {}
    const fenced = raw.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim();
    try { return JSON.parse(fenced); } catch (_) {}
    const start = raw.indexOf('{');
    const end = raw.lastIndexOf('}');
    if (start !== -1 && end > start) {
      const block = raw.slice(start, end + 1);
      try { return JSON.parse(block); } catch (_) {}
      const fixed = block.replace(/,\s*([}\]])/g, '$1').replace(/([^\\])\n/g, '$1\\n');
      try { return JSON.parse(fixed); } catch (_) {}
      console.warn('Direct extraction failed — attempting truncation recovery.');
      const recovered = recoverTruncated(block);
      if (recovered) { console.warn('Truncation recovery succeeded.'); return recovered; }
      console.error('FAILED JSON EXTRACTION:', block);
    }
    throw new Error('Model did not return parseable JSON.');
  }

  function recoverTruncated(text) {
    for (let i = text.length - 1; i > 0; i--) {
      const ch = text[i];
      if (ch !== '}' && ch !== ']') continue;
      const closed = closeJson(text.slice(0, i + 1));
      try { return JSON.parse(closed); } catch (_) {}
    }
    return null;
  }

  function closeJson(text) {
    const stack = [];
    let inStr = false, esc = false;
    for (const ch of text) {
      if (esc) { esc = false; continue; }
      if (ch === '\\') { esc = true; continue; }
      if (ch === '"') { inStr = !inStr; continue; }
      if (inStr) continue;
      if (ch === '{') stack.push('}');
      else if (ch === '[') stack.push(']');
      else if (ch === '}' || ch === ']') stack.pop();
    }
    return text + stack.reverse().join('');
  }

  function pickEvidencePosts(posts, indices, reason) {
    const chosen = [], seen = new Set();
    (Array.isArray(indices) ? indices : []).forEach(index => {
      const post = posts[Number(index)];
      if (!post) return;
      const key = `${post.account}|${post.primary_url}|${post.post}`;
      if (seen.has(key)) return;
      seen.add(key);
      chosen.push(transformPost(post, reason));
    });
    if (!chosen.length) {
      posts.slice(0, 5).forEach(post => {
        const key = `${post.account}|${post.primary_url}|${post.post}`;
        if (seen.has(key)) return;
        seen.add(key);
        chosen.push(transformPost(post, reason));
      });
    }
    return chosen;
  }

  function transformPost(post, reason) {
    const domain = post.primary_domain || '—';
    return {
      id: `${post.account}-${Math.random().toString(36).slice(2, 8)}`,
      account: post.account,
      publicationDate: post.publication_date,
      domain,
      url: post.primary_url || '',
      sourceType: inferSourceType(domain),
      contentType: inferContentType(domain),
      post: post.post,
      highlights: [...new Set([...(post.hashtags || []), ...(post.manipulation_categories || [])])].slice(0, 8),
      reason,
    };
  }

  function inferSourceType(d) {
    const h = String(d || '').toLowerCase();
    if (h.includes('video') || h.includes('media')) return 'media';
    if (h.includes('news') || h.includes('wire') || h.includes('report') || h.includes('archive')) return 'news';
    return 'link';
  }

  function inferContentType(d) {
    const h = String(d || '').toLowerCase();
    if (h.includes('video') || h.includes('clip')) return 'Media';
    if (h.includes('news') || h.includes('wire') || h.includes('report') || h.includes('archive')) return 'Article';
    return 'Post';
  }

  function confidenceLabel(s) { return s >= 80 ? 'High' : s >= 65 ? 'Medium' : 'Low'; }

  function clampNumber(v, min, max, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
  }

  return { adjudicateNetworks };
})();
