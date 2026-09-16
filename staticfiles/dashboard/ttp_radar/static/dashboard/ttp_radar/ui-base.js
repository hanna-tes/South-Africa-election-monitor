window.createRadarApp = function createRadarApp(options) {
  const core = window.DisarmCore;
  const state = {
    csvText: '',
    records: [],
    findings: null,
    selectedNetworkId: null,
    selectedTechniqueId: null,
    expandedFeedIds: new Set(),
  };

  const els = {
    csvFile: document.getElementById('csvFile'),
    loadSampleBtn: document.getElementById('loadSampleBtn'),
    runBtn: document.getElementById('runBtn'),
    exportBtn: document.getElementById('exportBtn'),
    linkWindow: document.getElementById('linkWindow'),
    postWindow: document.getElementById('postWindow'),
    textThreshold: document.getElementById('textThreshold'),
    minEvidence: document.getElementById('minEvidence'),
    networkThreshold: document.getElementById('networkThreshold'),
    networkTableBody: document.getElementById('networkTableBody'),
    kpiRows: document.getElementById('kpiRows'),
    kpiAccounts: document.getElementById('kpiAccounts'),
    kpiNetworks: document.getElementById('kpiNetworks'),
    kpiTtps: document.getElementById('kpiTtps'),
    kpiDomain: document.getElementById('kpiDomain'),
    previewTable: document.getElementById('previewTable'),
    workspace: document.getElementById('workspace'),
    workspaceTitle: document.getElementById('workspaceTitle'),
    workspaceRisk: document.getElementById('workspaceRisk'),
    closeWorkspaceBtn: document.getElementById('closeWorkspaceBtn'),
    networkOverview: document.getElementById('networkOverview'),
    accountCloud: document.getElementById('accountCloud'),
    accountTableBody: document.getElementById('accountTableBody'),
    techniqueList: document.getElementById('techniqueList'),
    targetList: document.getElementById('targetList'),
    evidencePanel: document.getElementById('evidencePanel'),
    evidenceTitle: document.getElementById('evidenceTitle'),
    evidenceChip: document.getElementById('evidenceChip'),
    evidenceFeed: document.getElementById('evidenceFeed'),
    runStatus: document.getElementById('runStatus'),
  };

  attachEvents();
  if (typeof options.initialize === 'function') options.initialize(els);

  function attachEvents() {
    els.csvFile?.addEventListener('change', handleFileUpload);
    els.loadSampleBtn?.addEventListener('click', loadSampleCsv);
    els.runBtn?.addEventListener('click', runAnalysis);
    els.exportBtn?.addEventListener('click', exportFindings);
    els.closeWorkspaceBtn?.addEventListener('click', () => {
      state.selectedNetworkId = null;
      state.selectedTechniqueId = null;
      els.workspace?.classList.add('hidden');
      renderNetworkTable(state.findings?.networks || []);
    });
  }

  async function loadSampleCsv() {
    const res = await fetch('./sample_input.csv');
    const text = await res.text();
    state.csvText = text;
    state.records = core.ingestCsv(text).records;
    renderPreview(state.records);
  }

  function handleFileUpload(event) {
    const [file] = event.target.files || [];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      state.csvText = String(reader.result || '');
      state.records = core.ingestCsv(state.csvText).records;
      renderPreview(state.records);
    };
    reader.readAsText(file);
  }

  async function runAnalysis() {
    const records = state.records.length ? state.records : core.ingestCsv(state.csvText).records;
    if (!records.length) {
      alert('Upload a CSV or load the sample data first.');
      return;
    }
    state.records = records;
    const params = readParams();
    setStatus('Running', 'warn');
    els.runBtn && (els.runBtn.disabled = true);

    try {
      const findings = await options.analyze(records, params, els);
      state.findings = findings;
      state.selectedNetworkId = findings.networks[0]?.id || null;
      state.selectedTechniqueId = findings.networks[0]?.candidateTechniques[0]?.techniqueId || null;
      state.expandedFeedIds = new Set();
      renderFindings(findings);
      setStatus(findings.networks.length ? 'Complete' : 'No mapped TTPs', findings.networks.length ? 'good' : 'warn');
    } catch (error) {
      console.error(error);
      setStatus('Error', 'bad');
      alert(error?.message || 'Analysis failed.');
    } finally {
      els.runBtn && (els.runBtn.disabled = false);
    }
  }

  function exportFindings() {
    if (!state.findings) {
      alert('Run the analysis before exporting findings.');
      return;
    }
    const blob = new Blob([JSON.stringify(state.findings, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${options.exportName || 'disarm_radar_findings'}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function readParams() {
    return {
      linkWindowMin: Number(els.linkWindow?.value || 30),
      postWindowMin: Number(els.postWindow?.value || 15),
      textThreshold: Number(els.textThreshold?.value || 0.82),
      minEvidence: Number(els.minEvidence?.value || 2),
      networkThreshold: Number(els.networkThreshold?.value || 45),
    };
  }

  function setStatus(label, kind) {
    if (!els.runStatus) return;
    els.runStatus.textContent = label;
    els.runStatus.className = `chip${kind ? ` ${kind}` : ''}`;
  }

  function renderFindings(findings) {
    els.kpiRows.textContent = String(findings.dataset.rows);
    els.kpiAccounts.textContent = String(findings.dataset.accounts);
    els.kpiNetworks.textContent = String(findings.networks.length);
    els.kpiTtps.textContent = String(findings.stats.ttpCount);
    els.kpiDomain.textContent = findings.dataset.topDomain || '—';
    renderNetworkTable(findings.networks);
    renderPreview(findings.preview);
    if (state.selectedNetworkId) renderWorkspace(findings.networks.find(item => item.id === state.selectedNetworkId) || null);
    else els.workspace?.classList.add('hidden');
  }

  function renderNetworkTable(networks) {
    if (!networks.length) {
      els.networkTableBody.innerHTML = '<tr><td colspan="7" class="muted-cell">No networks met the TTP threshold. This usually means the coordination observed did not also provide strong enough evidence of manipulation.</td></tr>';
      return;
    }
    els.networkTableBody.innerHTML = networks.map(network => {
      const active = network.id === state.selectedNetworkId ? 'active' : '';
      const scoreClass = core.scoreClass(network.riskScore);
      const ttps = network.candidateTechniques.map(item => `${item.techniqueId} ${item.name}`).join(' · ');
      const manip = network.sharedManipulationCategories.length ? network.sharedManipulationCategories.join(', ') : 'No repeated manipulation categories';
      return `
        <tr class="click-row ${active}" data-network-id="${network.id}">
          <td><strong>${core.escapeHtml(network.label)}</strong></td>
          <td>${core.escapeHtml(network.type)}</td>
          <td>${network.accounts.length}</td>
          <td>${core.escapeHtml(core.truncate(network.repeatedLinkTarget || '—', 72))}</td>
          <td>${core.escapeHtml(manip)}</td>
          <td><span class="score-pill ${scoreClass}">${network.riskScore}</span></td>
          <td>${core.escapeHtml(ttps)}</td>
        </tr>
      `;
    }).join('');

    els.networkTableBody.querySelectorAll('tr[data-network-id]').forEach(row => {
      row.addEventListener('click', () => {
        state.selectedNetworkId = row.dataset.networkId;
        const selected = state.findings.networks.find(item => item.id === state.selectedNetworkId) || null;
        state.selectedTechniqueId = selected?.candidateTechniques[0]?.techniqueId || null;
        state.expandedFeedIds = new Set();
        renderNetworkTable(networks);
        renderWorkspace(selected);
      });
    });
  }

  function renderWorkspace(network) {
    if (!network) {
      els.workspace?.classList.add('hidden');
      return;
    }
    els.workspace?.classList.remove('hidden');
    els.workspaceTitle.textContent = `${network.label} — ${network.type}`;
    els.workspaceRisk.textContent = `${network.riskBand} risk · ${network.riskScore}`;
    els.workspaceRisk.className = `risk-badge ${network.riskBand === 'High' ? 'bad' : network.riskBand === 'Medium' ? 'warn' : 'good'}`;

    renderOverview(network);
    renderAccounts(network);
    renderTargets(network);
    renderTechniques(network);
    renderEvidence(network, network.candidateTechniques.find(item => item.techniqueId === state.selectedTechniqueId) || null);
  }

  function renderOverview(network) {
    const topDomain = network.topDomains[0]?.[0] || 'No non-social content domain';
    const topHashtag = network.topHashtags[0]?.[0] || 'No dominant hashtag';
    els.networkOverview.innerHTML = `
      <article class="soft-card"><strong>${network.accounts.length} accounts · ${network.posts} posts</strong><p>Posts assigned to this network after connected-component detection.</p></article>
      <article class="soft-card"><strong>${Math.round(network.manipulationShare * 100)}%</strong><p>Share of posts with explicit manipulation cues.</p></article>
      <article class="soft-card"><strong>${core.escapeHtml(topDomain)}</strong><p>Top non-social content domain, excluding transport hosts like Facebook or X.</p></article>
      <article class="soft-card"><strong>${core.escapeHtml(topHashtag)}</strong><p>Top recurring hashtag attached to the network output.</p></article>
    `;
  }

  function renderAccounts(network) {
    // ── Network graph ────────────────────────────────────────────
    renderNetworkGraph(network);

    // ── Accounts table (unchanged) ───────────────────────────────
    els.accountTableBody.innerHTML = network.accountStats.map(item => `
      <tr>
        <td>${core.escapeHtml(item.account)}</td>
        <td>${item.posts}</td>
        <td>${item.manipulativePosts}</td>
        <td>${item.averageManipulationScore}</td>
      </tr>
    `).join('');
  }

  // ── D3 Force-directed network graph ───────────────────────────
  function renderNetworkGraph(network) {
    const container = els.accountCloud;
    if (!container) return;

    // Clear previous graph + any lingering tooltip
    container.innerHTML = '';
    document.getElementById('graph-tooltip')?.remove();

    const accountSet = new Set(network.accounts);

    // Build node list from accountStats
    const statsMap = new Map(network.accountStats.map(s => [s.account, s]));
    const nodes = network.accounts.map(acc => {
      const s = statsMap.get(acc) || { posts: 1, manipulativePosts: 0, averageManipulationScore: 0 };
      return {
        id: acc,
        posts: s.posts || 1,
        manipulativePosts: s.manipulativePosts || 0,
        avgScore: s.averageManipulationScore || 0,
        manipShare: s.posts > 0 ? (s.manipulativePosts / s.posts) : 0,
      };
    });

    // Build edge list — filter to only edges within this network
    const allEdges = state.findings?.pairEdges || [];
    const links = allEdges
      .filter(e => accountSet.has(e.source) && accountSet.has(e.target))
      .map(e => ({
        source: e.source,
        target: e.target,
        weight: e.weightedScore || 1,
        signals: e.signals || {},
      }));

    // If D3 not loaded yet, inject it then draw
    if (!window.d3) {
      const script = document.createElement('script');
      script.src = 'https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js';
      script.onload = () => drawGraph(container, nodes, links, network);
      document.head.appendChild(script);
    } else {
      drawGraph(container, nodes, links, network);
    }
  }

  function drawGraph(container, nodes, links, network) {
    const d3 = window.d3;
    const W = container.clientWidth || 600;
    const H = 300;

    // ── Scales ───────────────────────────────────────────────────
    const maxPosts  = Math.max(...nodes.map(n => n.posts), 1);
    const rScale    = d3.scaleSqrt().domain([0, maxPosts]).range([6, 24]);
    const maxWeight = Math.max(...links.map(l => l.weight), 1);
    const strokeW   = d3.scaleLinear().domain([0, maxWeight]).range([1, 4.5]);
    const strokeOp  = d3.scaleLinear().domain([0, maxWeight]).range([0.18, 0.65]);

    // Warm, understated palette — matches the light paper UI
    function nodeColor(manipShare) {
      if (manipShare === 0)  return '#94a3b8';  // slate  — no signal
      if (manipShare < 0.35) return '#d97706';  // amber  — low
      if (manipShare < 0.65) return '#b45309';  // dark amber — medium
      return '#991b1b';                          // red    — high
    }

    function nodeFill(manipShare) {
      if (manipShare === 0)  return '#f1f5f9';
      if (manipShare < 0.35) return '#fef3e2';
      if (manipShare < 0.65) return '#fde68a';
      return '#fee2e2';
    }

    function edgeColor(signals) {
      if ((signals.coLink || 0) > 0)      return '#b45309';
      if ((signals.coText || 0) > 0)      return '#1a4b8c';
      if ((signals.nearPosting || 0) > 0) return '#0f766e';
      return '#cbd5e1';
    }

    // ── SVG ──────────────────────────────────────────────────────
    const svg = d3.select(container)
      .append('svg')
      .attr('width', '100%')
      .attr('height', H)
      .attr('viewBox', `0 0 ${W} ${H}`)
      .style('display', 'block');

    svg.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', '#f9f7f4');

    const g = svg.append('g');

    // ── Legend ───────────────────────────────────────────────────
    const legendData = [
      { label: 'No signal', fill: '#f1f5f9', stroke: '#94a3b8' },
      { label: 'Low',       fill: '#fef3e2', stroke: '#d97706' },
      { label: 'High',      fill: '#fee2e2', stroke: '#991b1b' },
    ];
    const legend = svg.append('g').attr('transform', `translate(10, ${H - 14})`);
    legendData.forEach((d, i) => {
      const row = legend.append('g').attr('transform', `translate(${i * 80}, 0)`);
      row.append('circle').attr('r', 5).attr('cx', 5).attr('cy', 0)
        .attr('fill', d.fill).attr('stroke', d.stroke).attr('stroke-width', 1.5);
      row.append('text').attr('x', 14).attr('y', 4)
        .attr('font-size', '10px').attr('font-family', 'Inter, sans-serif')
        .attr('fill', '#9a9a90').text(d.label);
    });

    // ── Simulation ───────────────────────────────────────────────
    const sim = d3.forceSimulation(nodes)
      .force('link',    d3.forceLink(links).id(d => d.id).distance(d => Math.max(72, 120 - d.weight * 1.5)).strength(0.5))
      .force('charge',  d3.forceManyBody().strength(-160))
      .force('center',  d3.forceCenter(W / 2, H / 2 - 10))
      .force('collide', d3.forceCollide().radius(d => rScale(d.posts) + 10))
      .force('x',       d3.forceX(W / 2).strength(0.05))
      .force('y',       d3.forceY(H / 2).strength(0.07));

    // ── Edges ────────────────────────────────────────────────────
    const link = g.append('g').selectAll('line')
      .data(links).join('line')
      .attr('stroke',         d => edgeColor(d.signals))
      .attr('stroke-width',   d => strokeW(d.weight))
      .attr('stroke-opacity', d => strokeOp(d.weight));

    // ── Nodes ────────────────────────────────────────────────────
    const node = g.append('g').selectAll('g')
      .data(nodes).join('g')
      .style('cursor', 'pointer')
      .call(d3.drag()
        .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag',  (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end',   (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
      );

    // Node circle — filled with tint, stroked with color
    node.append('circle')
      .attr('r',            d => rScale(d.posts))
      .attr('fill',         d => nodeFill(d.manipShare))
      .attr('stroke',       d => nodeColor(d.manipShare))
      .attr('stroke-width', 2);

    // Short label inside larger nodes
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .attr('font-size',   d => rScale(d.posts) > 13 ? '8px' : '0')
      .attr('font-family', 'JetBrains Mono, monospace')
      .attr('font-weight', '500')
      .attr('fill',        d => nodeColor(d.manipShare))
      .attr('pointer-events', 'none')
      .text(d => d.id.slice(0, 4).toUpperCase());

    // Floating name label below node — always visible
    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy',          d => rScale(d.posts) + 11)
      .attr('font-size',   '9px')
      .attr('font-family', 'Inter, sans-serif')
      .attr('fill',        '#6b6b63')
      .attr('pointer-events', 'none')
      .text(d => d.id.length > 14 ? d.id.slice(0, 13) + '…' : d.id);

    // ── Tooltip ──────────────────────────────────────────────────
    const tooltip = d3.select('body').append('div')
      .attr('id', 'graph-tooltip')
      .style('position',       'fixed')
      .style('pointer-events', 'none')
      .style('display',        'none')
      .style('z-index',        '9999')
      .style('background',     '#ffffff')
      .style('border',         '1px solid #d0cbc2')
      .style('border-radius',  '8px')
      .style('padding',        '12px 16px')
      .style('box-shadow',     '0 4px 16px rgba(0,0,0,.10), 0 1px 4px rgba(0,0,0,.06)')
      .style('font-family',    'Inter, sans-serif')
      .style('font-size',      '13px')
      .style('color',          '#3d3d38')
      .style('min-width',      '190px')
      .style('line-height',    '1.5');

    node
      .on('mouseover', (event, d) => {
        link.attr('stroke-opacity', l =>
          (l.source.id === d.id || l.target.id === d.id)
            ? Math.min(strokeOp(l.weight) * 2, 0.9)
            : 0.05
        );
        node.select('circle')
          .attr('fill-opacity', n =>
            n.id === d.id || links.some(l =>
              (l.source.id === d.id && l.target.id === n.id) ||
              (l.target.id === d.id && l.source.id === n.id)
            ) ? 1 : 0.25
          );

        const pct = Math.round(d.manipShare * 100);
        const lvl = pct === 0 ? 'None' : pct < 35 ? 'Low' : pct < 65 ? 'Medium' : 'High';
        const lvlColor = pct === 0 ? '#94a3b8' : pct < 35 ? '#d97706' : pct < 65 ? '#b45309' : '#991b1b';

        tooltip.style('display', 'block').html(`
          <div style="font-size:11px;font-weight:600;color:#1a1a18;margin-bottom:8px;
                      word-break:break-all;border-bottom:1px solid #e2ddd6;padding-bottom:7px">
            ${d.id}
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <tr>
              <td style="color:#9a9a90;padding:2px 0">Posts</td>
              <td style="text-align:right;font-weight:600;color:#1a1a18">${d.posts}</td>
            </tr>
            <tr>
              <td style="color:#9a9a90;padding:2px 0">Manipulative</td>
              <td style="text-align:right;font-weight:600;color:#1a1a18">${d.manipulativePosts}</td>
            </tr>
            <tr>
              <td style="color:#9a9a90;padding:2px 0">Avg. cue score</td>
              <td style="text-align:right;font-weight:600;color:#1a1a18">${d.avgScore}</td>
            </tr>
            <tr>
              <td style="color:#9a9a90;padding:2px 0">Manipulation</td>
              <td style="text-align:right;font-weight:600;color:${lvlColor}">${lvl} (${pct}%)</td>
            </tr>
          </table>
        `);
      })
      .on('mousemove', (event) => {
        const pad = 14, tw = 210, th = 150;
        let x = event.clientX + pad, y = event.clientY + pad;
        if (x + tw > window.innerWidth)  x = event.clientX - tw - pad;
        if (y + th > window.innerHeight) y = event.clientY - th - pad;
        tooltip.style('left', x + 'px').style('top', y + 'px');
      })
      .on('mouseout', () => {
        tooltip.style('display', 'none');
        link.attr('stroke-opacity', d => strokeOp(d.weight));
        node.select('circle').attr('fill-opacity', 1);
      });

    // ── Tick ─────────────────────────────────────────────────────
    sim.on('tick', () => {
      const pad = 32;
      link
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      node.attr('transform', d => {
        d.x = Math.max(pad, Math.min(W - pad, d.x));
        d.y = Math.max(pad, Math.min(H - 24, d.y));
        return `translate(${d.x},${d.y})`;
      });
    });

    els.closeWorkspaceBtn?.addEventListener('click', () => {
      document.getElementById('graph-tooltip')?.remove();
    }, { once: true });
  }

  function renderTargets(network) {
    const domainCards = network.topDomains.slice(0, 4).map(([domain, count]) => `
      <div class="soft-card"><strong>${core.escapeHtml(domain)}</strong><p>${count} posts targeting this non-social domain.</p></div>
    `).join('');
    const urlCards = network.topUrls.slice(0, 4).map(([url, count]) => `
      <div class="soft-card"><strong>${core.escapeHtml(core.truncate(url, 90))}</strong><p>${count} posts referencing this content target.</p></div>
    `).join('');
    const fallback = '<div class="soft-card"><p>No non-social content targets were extracted. Transport hosts were intentionally excluded from this panel.</p></div>';
    els.targetList.innerHTML = domainCards || urlCards ? `${domainCards}${urlCards}` : fallback;
  }

  function renderTechniques(network) {
    els.techniqueList.innerHTML = network.candidateTechniques.map(item => {
      const active = item.techniqueId === state.selectedTechniqueId ? 'active' : '';
      return `
        <article class="technique-card ${active}" data-technique-id="${core.escapeAttribute(item.techniqueId)}">
          <div class="technique-meta">
            <div>
              <div class="code-badge">${core.escapeHtml(item.techniqueId)}</div>
              <h4>${core.escapeHtml(item.name)}</h4>
            </div>
            <span class="chip">${core.escapeHtml(item.confidence)}</span>
          </div>
          <p>${core.escapeHtml(item.justification)}</p>
          <div class="inline-tags" style="margin-top:10px;">
            <span class="tag emphasis">${item.score} / 100</span>
            ${item.sourceUrl ? `<a class="tag" href="${core.escapeAttribute(item.sourceUrl)}" target="_blank" rel="noopener noreferrer">DISARM reference</a>` : ''}
          </div>
        </article>
      `;
    }).join('');

    els.techniqueList.querySelectorAll('[data-technique-id]').forEach(card => {
      card.addEventListener('click', () => {
        state.selectedTechniqueId = card.dataset.techniqueId;
        state.expandedFeedIds = new Set();
        renderTechniques(network);
        renderEvidence(network, network.candidateTechniques.find(item => item.techniqueId === state.selectedTechniqueId) || null);
      });
    });
  }

  function renderEvidence(network, technique) {
    if (!technique) {
      els.evidencePanel?.classList.add('hidden');
      return;
    }
    els.evidencePanel?.classList.remove('hidden');
    els.evidenceTitle.textContent = `${technique.techniqueId} evidence feed`;
    els.evidenceChip.textContent = technique.name;
    els.evidenceFeed.innerHTML = (technique.evidencePosts || []).map(item => renderFeedCard(item, technique)).join('');
    els.evidenceFeed.querySelectorAll('[data-expand-id]').forEach(button => {
      button.addEventListener('click', () => {
        const id = button.dataset.expandId;
        if (state.expandedFeedIds.has(id)) state.expandedFeedIds.delete(id);
        else state.expandedFeedIds.add(id);
        renderEvidence(network, technique);
      });
    });
  }

  function renderFeedCard(item, technique) {
    const expanded = state.expandedFeedIds.has(item.id);
    const visible = expanded ? item.post : core.truncate(item.post, 280);
    const highlights = item.highlights || [];
    const badgeClass = item.sourceType === 'news' ? 'news' : item.sourceType === 'media' ? 'media' : item.sourceType === 'social' ? 'social' : 'link';
    const badgeText = item.sourceType === 'news' ? 'NW' : item.sourceType === 'media' ? 'MD' : item.sourceType === 'social' ? 'SM' : 'LN';
    return `
      <article class="feed-card">
        <div class="feed-header">
          <div class="feed-meta">
            <div class="feed-topline">
              <span class="source-badge ${badgeClass}">${badgeText}</span>
              <strong>${core.escapeHtml(item.account)}</strong>
              <span>${core.escapeHtml(item.contentType)}</span>
              <span>·</span>
              <span>${core.escapeHtml(core.formatDate(item.publicationDate))}</span>
            </div>
            <div class="feed-domain">${core.escapeHtml(item.domain || '—')}</div>
          </div>
          <div class="feed-tags">
            ${item.url ? `<a class="btn neutral small" href="${core.escapeAttribute(item.url)}" target="_blank" rel="noopener noreferrer">Open content</a>` : ''}
          </div>
        </div>
        <div class="feed-body">
          <div class="feed-snippet">${core.highlightTerms(visible, highlights)}</div>
          <div class="feed-context"><strong>Why this supports ${core.escapeHtml(technique.techniqueId)}:</strong> ${core.escapeHtml(item.reason)}</div>
        </div>
        <div class="feed-footer">
          <div class="feed-tags">${highlights.map(tag => `<span class="tag">${core.escapeHtml(tag)}</span>`).join('')}</div>
          ${item.post.length > 280 ? `<button class="btn neutral small" data-expand-id="${core.escapeAttribute(item.id)}">${expanded ? 'Show fewer words' : 'Show more words'}</button>` : ''}
        </div>
      </article>
    `;
  }

  function renderPreview(records) {
    if (!records.length) {
      els.previewTable.innerHTML = '<div class="empty-state">No normalized rows loaded yet.</div>';
      return;
    }
    const rows = records.slice(0, 25).map(record => `
      <tr>
        <td>${core.escapeHtml(record.account)}</td>
        <td>${core.escapeHtml(core.truncate(record.post, 110))}</td>
        <td>${core.escapeHtml(core.formatDate(record.publicationDate))}</td>
        <td>${core.escapeHtml(record.primaryDomain || '—')}</td>
        <td>${core.escapeHtml(core.truncate(record.primaryUrl || '—', 80))}</td>
        <td>${core.escapeHtml(record.transportHost || '—')}</td>
        <td>${core.escapeHtml(record.language || '—')}</td>
      </tr>
    `).join('');

    els.previewTable.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Account</th>
            <th>Post</th>
            <th>Date</th>
            <th>Content domain</th>
            <th>Primary content URL</th>
            <th>Transport host</th>
            <th>Language</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }
};
