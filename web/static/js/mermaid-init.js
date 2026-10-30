// SPDX-License-Identifier: Apache-2.0
// Diagrams written as ```mermaid fences in content/*.md (pages/views.py makes each a <pre class="mermaid">). Mermaid is
// fetched only on a page that has one, pinned; "strict" because no diagram needs click handlers. It is themed from the
// site's tokens (g6.css): paper, ink and rule greys, Archivo, thin lines; teal only on a node marked "approve" (a person
// approves, or a result is verified), a dashed edge on one marked "stop". Drawn again when prefers-color-scheme changes.
const nodes = [...document.querySelectorAll('pre.mermaid')];
if (nodes.length) {
  const { default: mermaid } = await import('https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs');
  const sources = nodes.map((node) => node.textContent);
  const scheme = matchMedia('(prefers-color-scheme: dark)');
  const draw = async () => {
    const css = getComputedStyle(document.documentElement);
    const t = (name) => css.getPropertyValue(name).trim();
    const paper = t('--paper'), paper2 = t('--paper-2'), ink = t('--ink'), rule = t('--rule');
    const graphite = t('--graphite'), teal = t('--teal'), font = t('--font');
    mermaid.initialize({
      startOnLoad: false, securityLevel: 'strict', theme: 'base', fontFamily: font,
      themeVariables: {
        darkMode: scheme.matches && document.documentElement.dataset.theme !== 'light', background: paper, fontFamily: font, fontSize: '15px',
        primaryColor: paper, primaryTextColor: ink, primaryBorderColor: ink, mainBkg: paper, nodeBorder: ink,
        secondaryColor: paper2, secondaryTextColor: ink, secondaryBorderColor: rule,
        tertiaryColor: paper2, tertiaryTextColor: ink, tertiaryBorderColor: rule,
        lineColor: graphite, textColor: ink, titleColor: ink, edgeLabelBackground: paper,
        clusterBkg: paper2, clusterBorder: rule,
        actorBkg: paper, actorBorder: ink, actorTextColor: ink, actorLineColor: rule,
        signalColor: ink, signalTextColor: ink, labelBoxBkgColor: paper, labelBoxBorderColor: ink,
        labelTextColor: ink, loopTextColor: ink, noteBkgColor: paper2, noteBorderColor: rule, noteTextColor: ink,
        activationBkgColor: paper2, activationBorderColor: graphite, sequenceNumberColor: paper,
      },
      themeCSS: `
        .node rect, .node polygon, .node circle, .node path, .actor, rect.note { stroke-width: 1px; }
        .flowchart-link, .messageLine0, .messageLine1, .loopLine { stroke-width: 1px !important; }
        .node.approve rect, .node.approve polygon, .node.approve circle, .node.approve path
          { stroke: ${teal} !important; stroke-width: 2px !important; }
        .node.stop rect, .node.stop polygon, .node.stop path { stroke-dasharray: 5 3 !important; }
        .edgeLabel, .edgeLabel p { color: ${graphite}; background: ${paper}; font-size: 13px; }
        .cluster-label, .cluster-label p { color: ${graphite}; }`,
      flowchart: { curve: 'linear', useMaxWidth: false, padding: 12, nodeSpacing: 34, rankSpacing: 40 },
      sequence: { useMaxWidth: false, mirrorActors: false, actorMargin: 36, boxMargin: 8, noteMargin: 10,
                  actorFontFamily: font, noteFontFamily: font, messageFontFamily: font },
    });
    nodes.forEach((node, i) => { node.removeAttribute('data-processed'); node.textContent = sources[i]; });
    await mermaid.run({ nodes });
  };
  if (document.fonts) await document.fonts.ready;      // labels are measured in Archivo, not a fallback
  await draw();
  scheme.addEventListener('change', draw);
}
