createRadarApp({
  exportName: 'disarm_radar_gemma',
  initialize: () => { /* … unchanged … */ },
  analyze: async (records, params) => {
    const baseUrl = document.getElementById('baseUrl')?.value?.trim();
    const model = document.getElementById('modelName')?.value?.trim() || 'gemma-disarm-phase3-ttp';
    const apiKey = document.getElementById('apiKey')?.value?.trim();
    const systemPrompt = document.getElementById('systemPrompt')?.value?.trim();
    if (!baseUrl) throw new Error('Please provide a model base URL.');

    const base = window.DisarmCore.analyzeRecords(records, params, { filterNetworksWithTechniques: false });

    // ─── DEBUG 1: how many networks were detected? ───
    console.log('DEBUG: Total networks detected by core:', base.allNetworks.length);
    if (base.allNetworks.length > 0) {
      console.log('DEBUG: First network dossier evidence posts count:',
                  base.allNetworks[0].llmDossier?.evidence_posts?.length);
      console.log('DEBUG: First network dominant signals:',
                  base.allNetworks[0].dominantSignals);
      console.log('DEBUG: First network manipulation share:',
                  base.allNetworks[0].manipulationShare);
    }

    if (!base.allNetworks.length) {
      // No networks found at all – thresholds too high or data too sparse
      console.warn('DEBUG: No networks found. Try lowering detection thresholds.');
      return { ...base, networks: [], stats: { ttpCount: 0 } };
    }

    const adjudicated = await window.DisarmLLM.adjudicateNetworks(base.allNetworks, {
      baseUrl,
      apiKey,
      model,
      systemPrompt,
      temperature: 0.1,
      label: 'Fine-tuned Gemma',
    });

    // ─── DEBUG 2: what did the LLM return? ───
    console.log('DEBUG: Networks after adjudication:', adjudicated.length);
    adjudicated.forEach((net, idx) => {
      console.log(`DEBUG: Network ${idx+1} (${net.id})`,
                  'techniques:', net.candidateTechniques?.length || 0,
                  'technique IDs:', (net.candidateTechniques || []).map(t => t.techniqueId));
    });

    const networks = adjudicated.filter(network => (network.candidateTechniques || []).length > 0);

    // ─── DEBUG 3: final networks that pass the filter ───
    console.log('DEBUG: Networks shown to user:', networks.length);

    return {
      ...base,
      networks,
      stats: { ttpCount: networks.reduce((sum, network) => sum + network.candidateTechniques.length, 0) },
    };
  },
});