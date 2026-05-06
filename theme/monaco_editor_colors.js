/**
 * Monaco editor theme colors — aligned with theme/palette.py / defineTheme in monaco.html.
 * Loaded before the inline bootstrap script in monaco.html.
 */
window.QUARK_MONACO_THEME_DARK = {
  'editor.background': '#070d17',
  'editorGutter.background': '#070d17',
  'editorLineNumber.foreground': 'rgba(148,163,184,0.45)',
  'editorLineNumber.activeForeground': 'rgba(148,163,184,0.85)',
  'editorCursor.foreground': '#5ee9ff',
  'editor.selectionBackground': 'rgba(148,163,184,0.24)',
  'editor.inactiveSelectionBackground': 'rgba(148,163,184,0.14)',
  'editorWhitespace.foreground': 'rgba(100,116,139,0.35)',
  'scrollbarSlider.background': 'rgba(255,255,255,0.1)',
  'scrollbarSlider.activeBackground': 'rgba(255,255,255,0.14)',
  'scrollbarSlider.hoverBackground': 'rgba(255,255,255,0.18)',
  'editorIndentGuide.background': 'rgba(255,255,255,0.04)',
  'editorIndentGuide.activeBackground': 'rgba(94,233,255,0.12)',
  // Tone down diagnostics so gutter/scrollbar don't look harsh red.
  'editorLineNumber.errorForeground': 'rgba(148,163,184,0.55)',
  'editorLineNumber.warningForeground': 'rgba(148,163,184,0.55)',
  'editorError.foreground': 'rgba(252,165,165,0.38)',
  'editorWarning.foreground': 'rgba(252,211,77,0.34)',
  'editorInfo.foreground': 'rgba(94,233,255,0.32)',
  'editorOverviewRuler.errorForeground': 'rgba(252,165,165,0.20)',
  'editorOverviewRuler.warningForeground': 'rgba(252,211,77,0.18)',
  'editorOverviewRuler.infoForeground': 'rgba(94,233,255,0.18)',
  'editorOverviewRuler.border': 'rgba(255,255,255,0.05)',
  'editorOverviewRuler.background': 'transparent',
  'editorOverviewRuler.findMatchForeground': 'transparent',
  'editorOverviewRuler.rangeHighlightForeground': 'transparent',
  'editorOverviewRuler.selectionHighlightForeground': 'transparent',
  'editorOverviewRuler.wordHighlightForeground': 'transparent',
  'editorOverviewRuler.wordHighlightStrongForeground': 'transparent',
};

window.QUARK_MONACO_THEME_LIGHT = {
  'editor.background': '#f8fbff',
  'editorGutter.background': '#f8fbff',
  'editorLineNumber.foreground': 'rgba(71,85,105,0.55)',
  'editorLineNumber.activeForeground': 'rgba(15,23,42,0.90)',
  'editorCursor.foreground': '#0ea5e9',
  'editor.selectionBackground': 'rgba(100,116,139,0.24)',
  'editor.inactiveSelectionBackground': 'rgba(100,116,139,0.14)',
  'editorWhitespace.foreground': 'rgba(100,116,139,0.28)',
  'scrollbarSlider.background': 'rgba(100,116,139,0.22)',
  'scrollbarSlider.activeBackground': 'rgba(100,116,139,0.28)',
  'scrollbarSlider.hoverBackground': 'rgba(100,116,139,0.34)',
  'editorIndentGuide.background': 'rgba(15,23,42,0.06)',
  'editorIndentGuide.activeBackground': 'rgba(14,165,233,0.18)',
  'editorLineNumber.errorForeground': 'rgba(71,85,105,0.65)',
  'editorLineNumber.warningForeground': 'rgba(71,85,105,0.65)',
  'editorError.foreground': 'rgba(220,38,38,0.25)',
  'editorWarning.foreground': 'rgba(202,138,4,0.25)',
  'editorInfo.foreground': 'rgba(14,165,233,0.24)',
  'editorOverviewRuler.errorForeground': 'rgba(220,38,38,0.12)',
  'editorOverviewRuler.warningForeground': 'rgba(202,138,4,0.12)',
  'editorOverviewRuler.infoForeground': 'rgba(14,165,233,0.12)',
  'editorOverviewRuler.border': 'rgba(15,23,42,0.10)',
  'editorOverviewRuler.background': 'transparent',
  'editorOverviewRuler.findMatchForeground': 'transparent',
  'editorOverviewRuler.rangeHighlightForeground': 'transparent',
  'editorOverviewRuler.selectionHighlightForeground': 'transparent',
  'editorOverviewRuler.wordHighlightForeground': 'transparent',
  'editorOverviewRuler.wordHighlightStrongForeground': 'transparent',
};

window.getQuarkMonacoThemeColors = function(mode){
  return String(mode || 'dark').toLowerCase() === 'light'
    ? window.QUARK_MONACO_THEME_LIGHT
    : window.QUARK_MONACO_THEME_DARK;
};
