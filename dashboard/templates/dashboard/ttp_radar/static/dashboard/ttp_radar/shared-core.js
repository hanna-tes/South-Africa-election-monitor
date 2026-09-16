function dominantLanguage(records) {
  const langCounts = new Map();
  records.forEach(record => {
    const lang = record.language && String(record.language).trim().toLowerCase();
    if (lang && lang !== '') {
      langCounts.set(lang, (langCounts.get(lang) || 0) + 1);
    }
  });
  const sorted = [...langCounts.entries()].sort((a, b) => b[1] - a[1]);
  return sorted[0] ? sorted[0][0] : null;  // e.g., 'ar', 'es', 'en', …
}

window.DisarmCore = (() => {
  const SOCIAL_HOST_HINTS = [
    'facebook.com', 'fb.com', 'instagram.com', 'x.com', 'twitter.com', 't.co', 'youtube.com', 'youtu.be',
    'tiktok.com', 'telegram.me', 't.me', 'whatsapp.com', 'reddit.com', 'discord.gg', 'discord.com', 'linkedin.com',
  ];

  const TECHNIQUE_CATALOG = {
    'T0049': {
      id: 'T0049',
      name: "Flood Information Space",
      summary: "Flooding sources of information with a high volume of inauthentic content to shape conversation or drown out legitimate information.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0049.md',
    },
    'T0049.002': {
      id: 'T0049.002',
      name: "Flood Existing Hashtag",
      summary: "Repeatedly injecting campaign content into an existing hashtag to maximize exposure or degrade the hashtag’s original use.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0049.002.md',
    },
    'T0049.003': {
      id: 'T0049.003',
      name: "Bots Amplify via Automated Forwarding and Reposting",
      summary: "Automated forwarding and reposting used to increase content exposure without equivalent human effort.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0049.003.md',
    },
    'T0049.005': {
      id: 'T0049.005',
      name: "Conduct Swarming",
      summary: "Coordinated use of accounts to overwhelm the information space around a specific event, target, or actor.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0049.005.md',
    },
    'T0016': {
      id: 'T0016',
      name: "Create Clickbait",
      summary: "Create attention-grabbing headlines or framing to drive traffic and engagement.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0016.md',
    },
    'T0060': {
      id: 'T0060',
      name: "Continue to Amplify",
      summary: "Continue narrative or message amplification after the main incident work has finished.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0060.md',
    },
    'T0097.202': {
      id: 'T0097.202',
      name: "News Outlet Persona",
      summary: "Present an institution or property as a news organization to establish legitimacy.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0097.202.md',
    },
    'T0143.003': {
      id: 'T0143.003',
      name: "Impersonated Persona",
      summary: "Impersonate an existing individual or institution to conceal identity or add legitimacy.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0143.003.md',
    },
    'T0119': {
      id: 'T0119',
      name: "Cross-Posting",
      summary: "Identical text payloads appearing across distinct contexts.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0119.md',
    },
    'T0119.001': {
      id: 'T0119.001',
      name: "Post across Groups",
      summary: "Same payload duplicated across multiple tracked community IDs.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0119.001.md',
    },
    'T0119.002': {
      id: 'T0119.002',
      name: "Post across Platform",
      summary: "Matching text or hashes across multi-platform exports.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0119.002.md',
    },
    'T0097.102': {
      id: 'T0097.102',
      name: "Journalist Persona",
      summary: "Bios claiming independent reporter, investigator, news-desk, or editorial status.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0097.102.md',
    },
    'T0143.002': {
      id: 'T0143.002',
      name: "Fabricated Persona",
      summary: "Highly templated bios, generic stock presentation, or mismatched regional cues used to simulate legitimacy.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0143.002.md',
    },
    'T0149.003': {
      id: 'T0149.003',
      name: "Lookalike Domain",
      summary: "Typosquatting, hyphenated spoof domains, or other deceptive domain mimicry.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0149.003.md',
    },
    'T0084.002': {
      id: 'T0084.002',
      name: "Plagiarise Content",
      summary: "Large blocks of text matching known authentic sources without citation.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0084.002.md',
    },
    'T0145.001': {
      id: 'T0145.001',
      name: "Copy Account Imagery",
      summary: "Replicate profile imagery from another account using perceptual hashing.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0145.001.md',
    },
    'T0145.002': {
      id: 'T0145.002',
      name: "AI-Gen Account Imagery",
      summary: "Detect generated or synthetic profile imagery using image artifact analysis.",
      sourceUrl: 'https://github.com/DISARMFoundation/DISARMframeworks/blob/main/generated_pages/techniques/T0145.002.md',
    },
  };

const CUE_LIBRARY = [
  // --- suppression / hidden truth framing ---
  {
    label: 'suppression / hidden truth framing',
    category: 'suppression',
    weight: 2.5,
    patterns: [
      /they do(?:n| not)['’]?t want you to know/i,
      /hidden truth/i,
      /before it gets deleted/i,
      /bury it/i,
      /cover[- ]?up/i,
      /media won['’]?t tell you/i,
      /what they won['’]?t show/i,
      /censored/i,
      /they are hiding/i,
      /keep this quiet/i,
      /shadow ban/i,
      /blackout/i,
      /information war/i,
      /nothing to see here/i,
      /just a coincidence/i,
      /move along/i,
      /they don['’]?t report/i,
      /silenced/i,
      /muzzle/i,
      /no platforming/i,
      /don['’]?t let them silence/i,
      /truth they don['’]?t want you to hear/i,
    ],
  },
  // --- conspiracy / covert actor framing ---
  {
    label: 'conspiracy / covert actor framing',
    category: 'conspiracy',
    weight: 2.3,
    patterns: [
      /deep state/i,
      /globalist/i,
      /shadow network/i,
      /staged outrage/i,
      /setup/i,
      /coordinated cover[- ]?up/i,
      /funded the staged/i,
      /they are behind it/i,
      /hidden hand/i,
      /puppet master/i,
      /controlled opposition/i,
      /false flag/i,
      /crisis actor/i,
      /inside job/i,
      /the real power/i,
      /they planned it/i,
      /not a coincidence/i,
    ],
  },
  // --- context distortion cues ---
  {
    label: 'context distortion cues',
    category: 'distortion',
    weight: 2.2,
    patterns: [
      /out of context/i,
      /cropped clip/i,
      /actually proves/i,
      /false narrative/i,
      /staged the panic/i,
      /proves the opposite/i,
      /fabricated/i,
      /manipulated/i,
      /taken out of context/i,
      /context collapse/i,
      /selective editing/i,
      /missing context/i,
      /twisted/i,
      /distorted/i,
      /doctored/i,
      /photoshopped/i,
      /deepfake/i,
      /altered video/i,
      /misrepresent/i,
      /quote mine/i,
      /half[- ]?truth/i,
      /not the full story/i,
      /what they left out/i,
    ],
  },
  // --- mobilization / urgency language ---
  {
    label: 'mobilization / urgency language',
    category: 'mobilization',
    weight: 1.3,
    patterns: [
      /urgent/i,
      /share (?:this|widely)/i,
      /read (?:this )?(?:now|before)/i,
      /everyone must see/i,
      /watch the clip/i,
      /patriots need to see/i,
      /spread the word/i,
      /retweet now/i,
      /please share/i,
      /don['’]?t scroll past/i,
      /signal boost/i,
      /must watch/i,
      /do your part/i,
      /we need everyone/i,
      /don['’]?t ignore/i,
      /share before they delete/i,
      /wake up/i,
      /open your eyes/i,
      /the time is now/i,
    ],
  },
  // --- proof / scandal framing ---
  {
    label: 'proof / scandal framing',
    category: 'proof',
    weight: 1.8,
    patterns: [
      /proof they lied/i,
      /real scandal/i,
      /experts confirm/i,
      /this is the proof/i,
      /patriots were right/i,
      /the real story/i,
      /smoking gun/i,
      /undeniable/i,
      /beyond doubt/i,
      /they got caught/i,
      /caught red[- ]?handed/i,
      /hard evidence/i,
      /breaking evidence/i,
      /leaked documents prove/i,
      /conclusive/i,
      /revealed/i,
      /finally exposed/i,
    ],
  },
  // --- emotional manipulation ---
  {
    label: 'emotional manipulation / outrage bait',
    category: 'emotional',
    weight: 1.8,
    patterns: [
      /this will make you angry/i,
      /you should be furious/i,
      /how dare they/i,
      /makes my blood boil/i,
      /outrageous/i,
      /unforgivable/i,
      /disgusting/i,
      /heartbreaking/i,
      /tearjerker/i,
      /rage bait/i,
    ],
  },
  // --- whataboutism / deflection ---
  {
    label: 'whataboutism / deflection',
    category: 'deflection',
    weight: 1.6,
    patterns: [
      /but what about/i,
      /what about (?:the )?(?:other side|them)/i,
      /you forgot about/i,
      /why aren['’]?t you talking about/i,
      /double standard/i,
      /they do it too/i,
      /both sides/i,
      /hypocrisy/i,
    ],
  },
  // --- discrediting the messenger ---
  {
    label: 'discrediting / ad hominem',
    category: 'discredit',
    weight: 1.7,
    patterns: [
      /paid shill/i,
      /government troll/i,
      /bot account/i,
      /fake news outlet/i,
      /propaganda machine/i,
      /biased source/i,
      /they are funded by/i,
      /who pays you/i,
      /part of the agenda/i,
    ],
  },
  // --- impersonation / persona language ---
  {
    label: 'persona mimicry language',
    category: 'impersonation',
    weight: 2.0,
    patterns: [
      /as a concerned citizen/i,
      /as a journalist/i,
      /as an independent researcher/i,
      /i used to believe/i,
      /i was once like you/i,
      /don['’]?t trust the mainstream/i,
      /i risk my life to report/i,
      /the media won['’]?t tell you but i will/i,
    ],
  },
];

  const STOPWORDS = new Set([
    'the','and','for','that','with','this','from','have','were','will','your','about','into','than','then','them','they','their','there','here','what','when','where','which','while','whose','been','being','after','before','because','would','could','should','very','much','many','more','most','some','such','only','just','also','over','under','between','across','within','without','against','onto','upon','through','during','each','same','other','another','still','again','even','like','must','need','does','doing','done','make','made','using','used','use','said','says','say','our','out','off','via','amp','you','are','was','is','be','to','of','in','on','at','it','as','an','or','by','if','not','no','we','he','she','his','her','its','a','now','read','watch','share','widely','urgent','everyone','must','see'
  ]);

  function parseCsv(text) {
    const rows = [];
    let current = '';
    let row = [];
    let inQuotes = false;
    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      const next = text[i + 1];
      if (char === '"') {
        if (inQuotes && next === '"') {
          current += '"';
          i += 1;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ',' && !inQuotes) {
        row.push(current);
        current = '';
      } else if ((char === '\n' || char === '\r') && !inQuotes) {
        if (char === '\r' && next === '\n') i += 1;
        row.push(current);
        if (row.some(cell => String(cell).trim() !== '')) rows.push(row);
        row = [];
        current = '';
      } else {
        current += char;
      }
    }
    if (current.length || row.length) {
      row.push(current);
      if (row.some(cell => String(cell).trim() !== '')) rows.push(row);
    }
    return rows;
  }

  function normalizeHeader(value) {
    return String(value || '').trim().toLowerCase().replace(/[_-]+/g, ' ');
  }

  function safeCell(value) {
    return String(value == null ? '' : value).trim();
  }

  function toTimestamp(value) {
    const ts = Date.parse(String(value || '').trim());
    return Number.isFinite(ts) ? ts : NaN;
  }

  function normalizeUrl(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    try {
      const url = new URL(raw);
      url.hash = '';
      ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid'].forEach(key => url.searchParams.delete(key));
      const entries = [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b));
      url.search = '';
      entries.forEach(([key, val]) => url.searchParams.append(key, val));
      return url.toString();
    } catch {
      return raw;
    }
  }

  function getDomain(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch {
      return '';
    }
  }

  function isSocialHost(host) {
    const domain = String(host || '').toLowerCase();
    return SOCIAL_HOST_HINTS.some(item => domain === item || domain.endsWith(`.${item}`));
  }

  function extractUrlsFromText(text) {
    const matches = String(text || '').match(/https?:\/\/[^\s)]+/g) || [];
    return dedupeList(matches.map(normalizeUrl).filter(Boolean));
  }

  function normalizeText(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/https?:\/\/\S+/g, ' ')
      .replace(/[#@]/g, ' ')
      .replace(/[^a-z0-9\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function uniqueTokens(normalizedText) {
    return dedupeList(String(normalizedText || '')
      .split(' ')
      .map(token => token.trim())
      .filter(token => token && token.length >= 3 && !STOPWORDS.has(token)));
  }

  function extractHashtags(text) {
    return dedupeList((String(text || '').match(/#[\p{L}\p{N}_-]+/gu) || []).map(item => item.toLowerCase()));
  }

  function analyzeManipulationCues(post) {
    const text = String(post || '');
    const hits = [];
    const categories = new Set();
    let score = 0;
    CUE_LIBRARY.forEach(rule => {
      const matched = rule.patterns.some(pattern => pattern.test(text));
      if (matched) {
        hits.push(rule.label);
        categories.add(rule.category);
        score += rule.weight;
      }
    });
    return {
      hits,
      categories: [...categories],
      score: round(score, 2),
      hasManipulationCue: score >= 1,
    };
  }

  function ingestCsv(text) {
    const rows = parseCsv(text || '');
    if (!rows.length) return { records: [] };
    const headers = rows[0].map(normalizeHeader);
    const index = findColumnIndices(headers);
    const records = rows.slice(1)
      .map((row, i) => buildRecord(row, index, i))
      .filter(Boolean)
      .sort((a, b) => a.timestamp - b.timestamp);
    return { records };
  }

  function findColumnIndices(headers) {
    const pick = options => headers.findIndex(header => options.includes(header));
    return {
      account: pick(['account', 'account name', 'actor', 'handle', 'user', 'username']),
      post: pick(['post', 'text', 'content', 'message', 'body']),
      date: pick(['publication date', 'publication_date', 'published', 'timestamp', 'date', 'created at', 'created_at']),
      url: pick(['url', 'link', 'source url', 'source_url']),
      displayName: pick(['display name', 'display_name', 'profile name', 'profile_name', 'page name', 'page_name']),
      bio: pick(['bio', 'description', 'profile description', 'profile_description', 'about']),
      title: pick(['title', 'page title', 'page_title', 'headline', 'article title', 'article_title']),
      groupId: pick(['group id', 'group_id', 'channel id', 'channel_id', 'community id', 'community_id']),
      platform: pick(['platform', 'network', 'site']),
      accountHandle: pick(['account handle', 'account_handle', 'profile handle', 'profile_handle']),
      parentAccount: pick(['parent account', 'parent_account', 'parent handle', 'parent_handle']),
      repostOfPostId: pick(['repost of post id', 'repost_of_post_id', 'reshare of post id', 'reshare_of_post_id']),
      replyToPostId: pick(['reply to post id', 'reply_to_post_id', 'in reply to', 'in_reply_to']),
      domain: pick(['domain', 'source domain', 'source_domain']),
      normalizedUrl: pick(['normalized url', 'normalized_url', 'canonical url', 'canonical_url']),
      language: pick(['language', 'lang']),
      geo: pick(['geo', 'geolocation', 'location']),
      locale: pick(['locale', 'region', 'country']),
      engagementSnapshot: pick(['engagement counts snapshot', 'engagement_counts_snapshot', 'engagement snapshot', 'engagement_snapshot']),
      mediaHash: pick(['media hash', 'media_hash', 'perceptual hash', 'perceptual_hash']),
    };
  }

  function buildRecord(row, index, i) {
    const account = safeCell(row[index.account]);
    const post = safeCell(row[index.post]);
    const dateValue = safeCell(row[index.date]);
    const urlCell = safeCell(row[index.url]);
    const displayName = safeCell(row[index.displayName]);
    const bio = safeCell(row[index.bio]);
    const title = safeCell(row[index.title]);
    const groupId = safeCell(row[index.groupId]);
    const platform = safeCell(row[index.platform]);
    const accountHandle = safeCell(row[index.accountHandle]);
    const parentAccount = safeCell(row[index.parentAccount]);
    const repostOfPostId = safeCell(row[index.repostOfPostId]);
    const replyToPostId = safeCell(row[index.replyToPostId]);
    const domain = safeCell(row[index.domain]);
    const normalizedUrlField = safeCell(row[index.normalizedUrl]);
    const language = safeCell(row[index.language]);
    const geo = safeCell(row[index.geo]);
    const locale = safeCell(row[index.locale]);
    const engagementSnapshot = safeCell(row[index.engagementSnapshot]);
    const mediaHash = safeCell(row[index.mediaHash]);
    if (!account && !post && !dateValue && !urlCell) return null;
    const timestamp = toTimestamp(dateValue);
    if (!Number.isFinite(timestamp)) return null;

    const normalizedUrlCell = normalizeUrl(normalizedUrlField || urlCell);
    const postUrls = extractUrlsFromText(post);
    const nonSocialPostUrl = postUrls.find(item => !isSocialHost(getDomain(item))) || '';
    const chosenPrimaryUrl = nonSocialPostUrl || (!isSocialHost(getDomain(normalizedUrlCell)) ? normalizedUrlCell : '');
    const primaryUrl = chosenPrimaryUrl || postUrls[0] || normalizedUrlCell;
    const primaryDomain = domain || (!isSocialHost(getDomain(primaryUrl)) ? getDomain(primaryUrl) : '');
    const manipulation = analyzeManipulationCues(post);
    const normalizedText = normalizeText(post);

    return {
      id: `r${i + 1}`,
      account: account || `unknown_${i + 1}`,
      accountHandle,
      parentAccount,
      displayName,
      bio,
      title,
      groupId,
      platform,
      repostOfPostId,
      replyToPostId,
      domain,
      normalizedUrl: normalizedUrlCell,
      language,
      geo,
      locale,
      engagementSnapshot,
      mediaHash,
      post,
      publicationDate: new Date(timestamp).toISOString(),
      timestamp,
      rawUrl: urlCell,
      transportUrl: normalizedUrlCell,
      transportHost: getDomain(normalizedUrlCell),
      postUrls,
      primaryUrl,
      primaryDomain,
      normalizedText,
      hashtags: extractHashtags(post),
      tokens: uniqueTokens(normalizedText),
      manipulationHits: manipulation.hits,
      manipulationCategories: manipulation.categories,
      manipulationScore: manipulation.score,
      hasManipulationCue: manipulation.hasManipulationCue,
      sourceType: inferSourceType(primaryDomain || getDomain(primaryUrl) || getDomain(normalizedUrlCell)),
      contentType: inferContentType(primaryDomain || getDomain(primaryUrl) || getDomain(normalizedUrlCell)),
    };
  }

  function analyzeRecords(records, params, options = {}) {
    const config = { filterNetworksWithTechniques: true, ...options };
    const accountToRecords = groupBy(records, item => item.account);
    const accounts = Object.keys(accountToRecords);
    const pairMap = buildPairSignals(records, params);
    const edges = [...pairMap.values()].filter(edge => edge.totalEvents >= params.minEvidence || edge.weightedScore >= params.networkThreshold / 2);
    const components = connectedComponents(accounts, edges);
    const allNetworks = components
      .map((component, index) => summarizeNetwork(component, index, edges, accountToRecords, records, params))
      .filter(Boolean)
      .sort((a, b) => b.riskScore - a.riskScore);

    const visibleNetworks = config.filterNetworksWithTechniques
      ? allNetworks.filter(network => network.candidateTechniques.length > 0)
      : allNetworks;

    const contentDomains = records.map(record => record.primaryDomain).filter(Boolean);
    return {
      generatedAt: new Date().toISOString(),
      dataset: {
        rows: records.length,
        accounts: accounts.length,
        topDomain: sortedEntries(countValues(contentDomains))[0]?.[0] || '—',
      },
      methodology: {
        summary: 'Network detection uses exact-link coordination, near-duplicate messaging, burst timing, lexical repetition, and repeated manipulation cues. Social platform transport hosts are excluded from domain concentration scoring unless an outbound content URL is present.',
        params,
      },
      allNetworks,
      networks: visibleNetworks,
      pairEdges: edges.map(edge => ({
        source: edge.source,
        target: edge.target,
        weightedScore: round(edge.weightedScore, 1),
        signals: edge.signals,
        evidence: edge.evidence.slice(0, 4),
      })),
      preview: records.slice(0, 25),
      stats: {
        ttpCount: visibleNetworks.reduce((sum, network) => sum + network.candidateTechniques.length, 0),
      },
    };
  }

  function buildPairSignals(records, params) {
    const pairMap = new Map();
    const maxWindowMs = Math.max(params.linkWindowMin, params.postWindowMin, 120) * 60 * 1000;
    for (let i = 0; i < records.length; i += 1) {
      const a = records[i];
      for (let j = i + 1; j < records.length; j += 1) {
        const b = records[j];
        const deltaMs = b.timestamp - a.timestamp;
        if (deltaMs > maxWindowMs && !likelySameNarrative(a, b, params.textThreshold)) break;
        if (a.account === b.account) continue;
        const deltaMin = Math.abs(deltaMs) / 60000;
        const pair = getPair(pairMap, a.account, b.account);
        let touched = false;

        if (a.primaryUrl && a.primaryUrl === b.primaryUrl && deltaMin <= params.linkWindowMin) {
          pair.signals.coLink += 1;
          pair.linkDeltas.push(deltaMin);
          pair.evidence.push({
            layer: 'Exact-link coordination',
            summary: `${a.account} and ${b.account} shared the same content URL within ${round(deltaMin, 2)} minutes.`,
            excerpt: truncate(a.post || b.post, 170),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        const similarity = jaccard(a.tokens, b.tokens);
        const exactText = a.normalizedText && a.normalizedText === b.normalizedText;
        if ((similarity >= params.textThreshold || exactText) && (deltaMin <= 1440 || exactText)) {
          pair.signals.coText += exactText ? 2 : 1;
          pair.textSimilarities.push(exactText ? 1 : similarity);
          pair.evidence.push({
            layer: 'Near-duplicate narrative coordination',
            summary: `${a.account} and ${b.account} posted ${exactText ? 'identical' : 'highly similar'} wording (${Math.round((exactText ? 1 : similarity) * 100)}% overlap).`,
            excerpt: truncate(a.post, 170),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        if (deltaMin <= params.postWindowMin && ((a.primaryDomain && a.primaryDomain === b.primaryDomain) || similarity >= 0.45 || sharedTokens(a.tokens, b.tokens).length >= 3)) {
          pair.signals.nearPosting += 1;
          pair.postDeltas.push(deltaMin);
          pair.evidence.push({
            layer: 'Near-time burst activity',
            summary: `${a.account} and ${b.account} posted inside ${round(deltaMin, 2)} minutes while sharing timing or content cues.`,
            excerpt: truncate(`${a.post} | ${b.post}`, 180),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        if (a.primaryDomain && a.primaryDomain === b.primaryDomain && deltaMin <= 60) {
          pair.signals.domainBurst += 1;
          pair.evidence.push({
            layer: 'Content-domain concentration',
            summary: `${a.account} and ${b.account} concentrated on ${a.primaryDomain} inside the same hour.`,
            excerpt: truncate(`${a.post} | ${b.post}`, 180),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        const sharedTags = intersect(a.hashtags, b.hashtags);
        const sharedLex = sharedTokens(a.tokens, b.tokens).filter(token => token.length > 4);
        if (deltaMin <= 60 && (sharedTags.length > 0 || sharedLex.length >= 3)) {
          pair.signals.lexicalFlood += 1;
          pair.evidence.push({
            layer: 'Lexical / hashtag repetition',
            summary: `${a.account} and ${b.account} reused hashtags or dense lexical bundles inside a short window.`,
            excerpt: truncate(sharedTags.concat(sharedLex).slice(0, 8).join(', '), 150),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        if (a.hasManipulationCue && b.hasManipulationCue && intersect(a.manipulationCategories, b.manipulationCategories).length > 0) {
          pair.signals.manipulationCue = (pair.signals.manipulationCue || 0) + 1;
          pair.evidence.push({
            layer: 'Repeated manipulation framing',
            summary: `${a.account} and ${b.account} used overlapping manipulation cues (${intersect(a.manipulationCategories, b.manipulationCategories).join(', ')}).`,
            excerpt: truncate(`${a.post} | ${b.post}`, 180),
            recordIds: [a.id, b.id],
          });
          touched = true;
        }

        if (touched) pair.totalEvents += 1;
      }
    }

    pairMap.forEach(pair => {
      pair.weightedScore = (
        pair.signals.coLink * 6 +
        pair.signals.coText * 4.5 +
        pair.signals.nearPosting * 2 +
        pair.signals.domainBurst * 2.5 +
        pair.signals.lexicalFlood * 2.5 +
        (pair.signals.manipulationCue || 0) * 3 +
        (pair.linkDeltas.length && median(pair.linkDeltas) <= 2 ? 8 : 0) +
        (pair.postDeltas.length && median(pair.postDeltas) <= 3 ? 4 : 0)
      );
    });
    return pairMap;
  }

  function summarizeNetwork(component, index, edges, accountToRecords, records, params) {
    const nodeSet = new Set(component.nodes);
    const networkEdges = edges.filter(edge => nodeSet.has(edge.source) && nodeSet.has(edge.target));
    const networkRecords = records.filter(record => nodeSet.has(record.account));
    if (!networkRecords.length) return null;

    const signalTotals = { coLink: 0, coText: 0, nearPosting: 0, domainBurst: 0, lexicalFlood: 0, manipulationCue: 0 };
    networkEdges.forEach(edge => {
      Object.keys(signalTotals).forEach(key => {
        signalTotals[key] += edge.signals[key] || 0;
      });
    });

    const accountStats = component.nodes.map(account => {
      const rows = accountToRecords[account] || [];
      const manipCount = rows.filter(item => item.hasManipulationCue).length;
      return {
        account,
        posts: rows.length,
        manipulativePosts: manipCount,
        averageManipulationScore: round(rows.reduce((sum, item) => sum + item.manipulationScore, 0) / Math.max(1, rows.length), 2),
      };
    }).sort((a, b) => b.posts - a.posts || b.averageManipulationScore - a.averageManipulationScore);

    const topDomains = sortedEntries(countValues(networkRecords.map(item => item.primaryDomain).filter(Boolean))).slice(0, 5);
    const repeatedUrlGroups = sortedEntries(groupByToArrays(networkRecords.filter(item => item.primaryUrl), item => item.primaryUrl)).filter(([, group]) => group.length >= 2);
    const topUrls = repeatedUrlGroups.map(([url, group]) => [url, group.length]).slice(0, 5);
    const topHashtags = sortedEntries(countValues(networkRecords.flatMap(item => item.hashtags))).slice(0, 5);
    const manipulationPosts = networkRecords.filter(item => item.hasManipulationCue);
    const manipulationShare = networkRecords.length ? manipulationPosts.length / networkRecords.length : 0;
    const manipulationCategories = sortedEntries(countValues(manipulationPosts.flatMap(item => item.manipulationCategories)));
    const sharedManipulationCategories = manipulationCategories.filter(([, count]) => count >= 2).map(([name]) => name);
    const avgEdgeScore = networkEdges.length ? networkEdges.reduce((sum, edge) => sum + edge.weightedScore, 0) / networkEdges.length : 0;
    const topDomainShare = networkRecords.length ? ((topDomains[0]?.[1] || 0) / networkRecords.length) : 0;
    const topUrlShare = networkRecords.length ? ((topUrls[0]?.[1] || 0) / networkRecords.length) : 0;
    const densestBurst = findDensestBurst(networkRecords, params.postWindowMin);
    const medianLinkDelta = median(networkEdges.flatMap(edge => edge.linkDeltas || []));
    const medianPostDelta = median(networkEdges.flatMap(edge => edge.postDeltas || []));
    const repeatedUrlCount = repeatedUrlGroups.length;

    const riskScore = clamp(
      avgEdgeScore * 1.25 +
      manipulationShare * 34 +
      sharedManipulationCategories.length * 5 +
      (component.nodes.length >= 4 ? 8 : 0) +
      Math.min(16, (topUrls[0]?.[1] || 0) * 2) +
      (densestBurst.uniqueAccounts >= 4 ? 8 : 0),
      0,
      100,
    );

    const repeatedLinkTarget = topUrls[0]?.[0] || '—';
    const networkType = classifyNetwork({ signalTotals, sharedManipulationCategories, topUrlShare, componentSize: component.nodes.length, repeatedUrlCount });

    const context = {
      componentSize: component.nodes.length,
      signalTotals,
      topHashtags,
      topDomains,
      topUrls,
      manipulationShare,
      sharedManipulationCategories,
      densestBurst,
      medianLinkDelta,
      medianPostDelta,
      repeatedUrlCount,
      repeatedLinkTarget,
      networkRecords,
      repeatedUrlGroups,
      topUrlShare,
      topDomainShare,
      riskScore,
    };

    const candidateTechniques = mapRuleBasedTechniques(context);

    return {
      id: `network_${index + 1}`,
      label: `Network ${index + 1}`,
      type: networkType,
      accounts: component.nodes,
      accountStats,
      posts: networkRecords.length,
      riskScore: round(riskScore, 1),
      riskBand: riskBand(riskScore),
      signalTotals,
      dominantSignals: sortedEntries(signalTotals).slice(0, 4).filter(([, value]) => value > 0),
      manipulationShare: round(manipulationShare, 3),
      sharedManipulationCategories,
      manipulationCategories,
      topDomains,
      topUrls,
      topHashtags,
      repeatedLinkTarget,
      repeatedUrlGroups: repeatedUrlGroups.map(([url, group]) => ({ url, count: group.length })),
      densestBurst: {
        windowMinutes: densestBurst.windowMinutes,
        count: densestBurst.records.length,
        uniqueAccounts: densestBurst.uniqueAccounts,
        start: densestBurst.start,
        end: densestBurst.end,
      },
      candidateTechniques,
      llmDossier: buildLlmDossier({
        id: `network_${index + 1}`,
        type: networkType,
        componentSize: component.nodes.length,
        signalTotals,
        manipulationShare,
        sharedManipulationCategories,
        topDomains,
        topUrls,
        topHashtags,
        densestBurst,
        medianLinkDelta,
        medianPostDelta,
        repeatedLinkTarget,
        networkRecords,
      }),
    };
  }

  function classifyNetwork({ signalTotals, sharedManipulationCategories, topUrlShare, componentSize, repeatedUrlCount }) {
    const activeLayers = sortedEntries(signalTotals).filter(([, value]) => value > 0).length;
    if (activeLayers >= 4 && sharedManipulationCategories.length >= 2) return 'Hybrid manipulation network';
    if (signalTotals.coLink >= Math.max(signalTotals.coText, signalTotals.lexicalFlood) && repeatedUrlCount >= 2 && topUrlShare >= 0.3) return 'Synchronized link amplifier';
    if (signalTotals.coText >= Math.max(signalTotals.coLink, signalTotals.lexicalFlood) && sharedManipulationCategories.length) return 'Narrative clone network';
    if (signalTotals.lexicalFlood >= 4) return 'Lexical flooding network';
    if (componentSize >= 4) return 'Burst coordination network';
    return 'Suspicious coordinated cluster';
  }

  function mapRuleBasedTechniques(ctx) {
    const techniques = [];
    if (ctx.manipulationShare < 0.45 || !ctx.sharedManipulationCategories.length) return techniques;

    if (ctx.componentSize >= 3 && ctx.signalTotals.coLink >= 3 && ctx.medianLinkDelta <= 3 && ctx.repeatedUrlCount >= 2) {
      techniques.push(makeTechnique('T0049.003', 78, `${ctx.signalTotals.coLink} exact-link coordination events were observed with a median repost gap of ${round(ctx.medianLinkDelta || 0, 2)} minutes, alongside repeated manipulation framing.`, buildEvidencePosts(selectRecordsForRapidReposting(ctx), 'Repeated rapid reposting of the same content URL with manipulation cues.', 6)));
    }

    if (ctx.componentSize >= 4 && ctx.densestBurst.uniqueAccounts >= 4 && (ctx.signalTotals.coLink + ctx.signalTotals.nearPosting) >= 8) {
      techniques.push(makeTechnique('T0049.005', 84, `${ctx.densestBurst.uniqueAccounts} accounts posted in the densest ${ctx.densestBurst.windowMinutes}-minute burst while amplifying the same topic or link with manipulation framing.`, buildEvidencePosts(selectBurstRecords(ctx), 'A dense multi-account burst focused attention around the same content and manipulation frame.', 6)));
    }

    if (ctx.componentSize >= 4 && (ctx.signalTotals.coLink + ctx.signalTotals.coText + ctx.signalTotals.lexicalFlood) >= 10 && (ctx.topUrlShare >= 0.35 || ctx.topDomainShare >= 0.4)) {
      techniques.push(makeTechnique('T0049', 76, `The network repeatedly concentrated on the same content target (${truncate(ctx.repeatedLinkTarget, 70)}) while maintaining high coordination and manipulation-cue repetition.`, buildEvidencePosts(selectRecordsForFlooding(ctx), 'The same content target dominated network output in a coordinated and manipulative way.', 6)));
    }

    return techniques.sort((a, b) => b.score - a.score);
  }

  function makeTechnique(id, score, justification, evidencePosts) {
    const meta = TECHNIQUE_CATALOG[id];
    return {
      techniqueId: id,
      name: meta?.name || id,
      summary: meta?.summary || '',
      sourceUrl: meta?.sourceUrl || '',
      score,
      confidence: confidenceLabel(score),
      justification,
      evidencePosts,
    };
  }

  function selectRecordsForRapidReposting(ctx) {
    const firstGroup = ctx.repeatedUrlGroups.find(([, group]) => group.filter(item => item.hasManipulationCue).length >= 3);
    return (firstGroup ? firstGroup[1] : ctx.networkRecords).filter(item => item.hasManipulationCue);
  }

  function selectBurstRecords(ctx) {
    return (ctx.densestBurst.records || []).filter(item => item.hasManipulationCue).length
      ? ctx.densestBurst.records.filter(item => item.hasManipulationCue)
      : ctx.networkRecords.filter(item => item.hasManipulationCue).slice(0, 8);
  }

  function selectRecordsForFlooding(ctx) {
    const dominantUrl = ctx.topUrls[0]?.[0];
    return ctx.networkRecords.filter(item => item.hasManipulationCue && (!dominantUrl || item.primaryUrl === dominantUrl));
  }

  function buildEvidencePosts(records, reason, limit) {
    const items = [];
    const seen = new Set();
    records.slice().sort((a, b) => a.timestamp - b.timestamp).forEach(record => {
      const key = `${record.account}|${record.primaryUrl}|${record.normalizedText}`;
      if (seen.has(key)) return;
      seen.add(key);
      items.push({
        id: record.id,
        account: record.account,
        publicationDate: record.publicationDate,
        domain: record.primaryDomain || record.transportHost || '—',
        url: record.primaryUrl || record.transportUrl || '',
        sourceType: record.sourceType,
        contentType: record.contentType,
        post: record.post,
        highlights: dedupeList([...(record.hashtags || []), ...(record.manipulationHits || []), ...(record.manipulationCategories || [])]).slice(0, 8),
        reason,
      });
    });
    return items.slice(0, limit);
  }

 function buildLlmDossier(network) {
  const domLang = dominantLanguage(network.networkRecords);
  const useCueFilter = (!domLang || domLang === 'en');   // bypass only for non-English

  const samplePosts = network.networkRecords
    .filter(item => useCueFilter ? item.hasManipulationCue : true)
    .slice(0, 10)
    .map(item => ({
      account: item.account,
      display_name: item.displayName || '',
      bio: item.bio || '',
      title: item.title || '',
      publication_date: item.publicationDate,
      primary_url: item.primaryUrl,
      primary_domain: item.primaryDomain,
      hashtags: item.hashtags,
      manipulation_categories: item.manipulationCategories,
      post: item.post,
    }));

  return {
    network_id: network.id,
    network_type: network.type,
    account_count: network.componentSize,
    signal_totals: network.signalTotals,
    manipulation_share: round(network.manipulationShare, 3),
    shared_manipulation_categories: network.sharedManipulationCategories,
    top_domains: network.topDomains.slice(0, 3),
    top_urls: network.topUrls.slice(0, 3),
    top_hashtags: network.topHashtags.slice(0, 3),
    densest_burst: {
      window_minutes: network.densestBurst.windowMinutes,
      post_count: network.densestBurst.records.length,
      unique_accounts: network.densestBurst.uniqueAccounts,
    },
    median_link_delta_minutes: round(network.medianLinkDelta || 0, 2),
    median_post_delta_minutes: round(network.medianPostDelta || 0, 2),
    repeated_link_target: network.repeatedLinkTarget,
    temporal_profile: buildTemporalProfile(network.networkRecords),
    raw_phase3_indicators: buildPhase3Indicators(network.networkRecords, network.topDomains, network.topUrls, network.topHashtags),
    required_raw_fields_for_high_precision: {
      minimum: ['account', 'post', 'publication date', 'url'],
      recommended_for_phase3: ['display_name', 'bio', 'title', 'group_id', 'platform', 'account_handle', 'parent_account', 'repost_of_post_id', 'reply_to_post_id', 'domain', 'normalized_url', 'language', 'geo', 'locale'],
    },
      evidence_posts: samplePosts,
      allowed_techniques: ['T0049', 'T0049.002', 'T0049.003', 'T0049.005', 'T0016', 'T0060', 'T0097.202', 'T0143.003', 'T0119', 'T0119.001', 'T0119.002', 'T0097.102', 'T0143.002', 'T0149.003', 'T0084.002'],
      auxiliary_techniques: ['T0145.001', 'T0145.002'],
      instructions: 'Only return a TTP if the evidence supports both coordination and clear manipulation intent. Use raw observable cues from the dossier only; do not assume access to investigative reporting at inference time. If uncertain, return no techniques.',
    };
  }

  function buildTemporalProfile(records) {
    const byDay = sortedEntries(groupByToArrays(records, item => String(item.publicationDate || '').slice(0, 10)));
    const urlDays = new Map();
    records.forEach(item => {
      const day = String(item.publicationDate || '').slice(0, 10);
      if (!item.primaryUrl || !day) return;
      if (!urlDays.has(item.primaryUrl)) urlDays.set(item.primaryUrl, new Set());
      urlDays.get(item.primaryUrl).add(day);
    });
    const sustainedTargets = [...urlDays.entries()]
      .map(([url, days]) => [url, days.size])
      .filter(([, dayCount]) => dayCount >= 2)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
    return {
      active_days: byDay.length,
      largest_day_volume: byDay[0]?.[1]?.length || 0,
      sustained_targets: sustainedTargets,
    };
  }

  function buildPhase3Indicators(records, topDomains, topUrls, topHashtags) {
    const outletKeywords = ['news', 'media', 'times', 'post', 'press', 'journal', 'gazette', 'herald', 'chronicle', 'tribune', 'wire'];
    const clickbaitPatterns = [
      /breaking[:!]/i,
      /shocking/i,
      /you won['’]?t believe/i,
      /exclusive/i,
      /must see/i,
      /bombshell/i,
      /urgent[:!]/i,
      /watch now/i,
    ];
    const suspiciousDomains = (topDomains || [])
      .filter(([domain]) => outletKeywords.some(keyword => String(domain || '').toLowerCase().includes(keyword)))
      .slice(0, 5);
    const clickbaitPosts = records
      .filter(item => clickbaitPatterns.some(pattern => pattern.test(item.post || '') || pattern.test(item.title || '')))
      .slice(0, 8)
      .map(item => ({ account: item.account, title: item.title || '', post: truncate(item.post, 140), url: item.primaryUrl || '' }));
    const personaStyledPosts = records
      .filter(item => outletKeywords.some(keyword => String(item.title || item.primaryDomain || '').toLowerCase().includes(keyword)) || /(news|media|journalist|editor)/i.test(item.bio || ''))
      .slice(0, 8)
      .map(item => ({ account: item.account, display_name: item.displayName || '', bio: item.bio || '', domain: item.primaryDomain || '', title: item.title || '' }));
    return {
      suspicious_outlet_like_domains: suspiciousDomains,
      clickbait_examples: clickbaitPosts,
      persona_style_examples: personaStyledPosts,
      repeated_targets: (topUrls || []).slice(0, 3),
      repeated_hashtags: (topHashtags || []).slice(0, 3),
    };
  }

  function findDensestBurst(records, windowMinutes) {
    const sorted = records.slice().sort((a, b) => a.timestamp - b.timestamp);
    const windowMs = windowMinutes * 60000;
    let best = { records: [], uniqueAccounts: 0, windowMinutes, start: '', end: '' };
    for (let i = 0; i < sorted.length; i += 1) {
      const cluster = [sorted[i]];
      for (let j = i + 1; j < sorted.length; j += 1) {
        if (sorted[j].timestamp - sorted[i].timestamp <= windowMs) cluster.push(sorted[j]);
        else break;
      }
      const uniqueAccounts = new Set(cluster.map(item => item.account)).size;
      if (cluster.length > best.records.length || (cluster.length === best.records.length && uniqueAccounts > best.uniqueAccounts)) {
        best = {
          records: cluster,
          uniqueAccounts,
          windowMinutes,
          start: cluster[0]?.publicationDate || '',
          end: cluster[cluster.length - 1]?.publicationDate || '',
        };
      }
    }
    return best;
  }

  function getPair(pairMap, a, b) {
    const source = [a, b].sort()[0];
    const target = [a, b].sort()[1];
    const key = `${source}|||${target}`;
    if (!pairMap.has(key)) {
      pairMap.set(key, {
        source,
        target,
        signals: { coLink: 0, coText: 0, nearPosting: 0, domainBurst: 0, lexicalFlood: 0, manipulationCue: 0 },
        totalEvents: 0,
        weightedScore: 0,
        linkDeltas: [],
        postDeltas: [],
        textSimilarities: [],
        evidence: [],
      });
    }
    return pairMap.get(key);
  }

  function connectedComponents(accounts, edges) {
    const adjacency = new Map(accounts.map(account => [account, new Set()]));
    edges.forEach(edge => {
      adjacency.get(edge.source)?.add(edge.target);
      adjacency.get(edge.target)?.add(edge.source);
    });
    const visited = new Set();
    const components = [];
    accounts.forEach(account => {
      if (visited.has(account)) return;
      const stack = [account];
      const nodes = [];
      visited.add(account);
      while (stack.length) {
        const current = stack.pop();
        nodes.push(current);
        for (const next of adjacency.get(current) || []) {
          if (!visited.has(next)) {
            visited.add(next);
            stack.push(next);
          }
        }
      }
      if (nodes.length > 1) components.push({ nodes: nodes.sort() });
    });
    return components;
  }

  function likelySameNarrative(a, b, threshold) {
    return a.normalizedText === b.normalizedText || jaccard(a.tokens, b.tokens) >= threshold;
  }

  function jaccard(a, b) {
    const setA = new Set(a || []);
    const setB = new Set(b || []);
    if (!setA.size && !setB.size) return 0;
    let intersection = 0;
    setA.forEach(item => { if (setB.has(item)) intersection += 1; });
    return intersection / (setA.size + setB.size - intersection || 1);
  }

  function sharedTokens(a, b) {
    const setB = new Set(b || []);
    return (a || []).filter(item => setB.has(item));
  }

  function intersect(a, b) {
    const setB = new Set(b || []);
    return (a || []).filter(item => setB.has(item));
  }

  function groupBy(collection, fn) {
    return collection.reduce((acc, item) => {
      const key = fn(item);
      if (!acc[key]) acc[key] = [];
      acc[key].push(item);
      return acc;
    }, {});
  }

  function groupByToArrays(collection, fn) {
    return collection.reduce((acc, item) => {
      const key = fn(item);
      if (!acc[key]) acc[key] = [];
      acc[key].push(item);
      return acc;
    }, {});
  }

  function countValues(items) {
    return items.reduce((acc, item) => {
      acc[item] = (acc[item] || 0) + 1;
      return acc;
    }, {});
  }

  function sortedEntries(obj) {
    return Object.entries(obj || {}).sort((a, b) => {
      const av = Array.isArray(a[1]) ? a[1].length : a[1];
      const bv = Array.isArray(b[1]) ? b[1].length : b[1];
      return bv - av;
    });
  }

  function inferSourceType(domain) {
    const host = String(domain || '').toLowerCase();
    if (!host) return 'link';
    if (host.includes('video') || host.includes('media')) return 'media';
    if (host.includes('news') || host.includes('wire') || host.includes('report') || host.includes('archive')) return 'news';
    if (isSocialHost(host)) return 'social';
    return 'link';
  }

  function inferContentType(domain) {
    const host = String(domain || '').toLowerCase();
    if (!host) return 'Post';
    if (host.includes('video') || host.includes('clip')) return 'Media';
    if (host.includes('news') || host.includes('wire') || host.includes('report') || host.includes('archive')) return 'Article';
    return 'Post';
  }

  function riskBand(score) {
    if (score >= 67) return 'High';
    if (score >= 40) return 'Medium';
    return 'Low';
  }

  function confidenceLabel(score) {
    if (score >= 80) return 'High';
    if (score >= 65) return 'Medium';
    return 'Low';
  }

  function median(values) {
    const arr = (values || []).filter(Number.isFinite).slice().sort((a, b) => a - b);
    if (!arr.length) return 0;
    const mid = Math.floor(arr.length / 2);
    return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
  }

  function round(value, digits = 2) {
    const power = 10 ** digits;
    return Math.round((Number(value) || 0) * power) / power;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function dedupeList(items) {
    return [...new Set((items || []).filter(Boolean))];
  }

  function truncate(value, length = 120) {
    const text = String(value || '');
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function escapeRegExp(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function formatDate(value) {
    try {
      return new Intl.DateTimeFormat('en-GB', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
      }).format(new Date(value));
    } catch {
      return String(value || '');
    }
  }

  function highlightTerms(text, terms) {
    let html = escapeHtml(text || '');
    dedupeList((terms || []).filter(Boolean)).sort((a, b) => b.length - a.length).forEach(term => {
      const escaped = escapeRegExp(String(term).replace(/^#/, ''));
      if (!escaped) return;
      const regex = new RegExp(`(\\#?${escaped})`, 'gi');
      html = html.replace(regex, '<span class="hl">$1</span>');
    });
    return html;
  }

  function scoreClass(score) {
    if (score >= 67) return 'high';
    if (score >= 40) return 'medium';
    return 'low';
  }

  return {
    TECHNIQUE_CATALOG,
    ingestCsv,
    analyzeRecords,
    parseCsv,
    formatDate,
    truncate,
    escapeHtml,
    escapeAttribute,
    highlightTerms,
    scoreClass,
    confidenceLabel,
    riskBand,
    round,
  };
})();
