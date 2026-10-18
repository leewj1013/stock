"""Frame shared by the domestic dashboard and the /us page: CSS, header, tabs, theme/tab-key script."""
import html

PAGE_CSS = r""":root{color-scheme:light dark;
--bg-page:#f6f7f9;--bg-surface:#ffffff;--bg-surface-alt:#f8fafc;--bg-accent-card:#ffffff;--bg-section:#ffffff;--section-border:transparent;
--text-primary:#111;--text-secondary:#64748b;--text-muted:#666;--text-strong:#334155;--text-on-accent:#111;--text-on-accent-muted:#64748b;
--border:#e5e7eb;--border-strong:#d0d5dd;--hover-overlay:rgba(0,0,0,.05);
--accent:#2563eb;--accent-bg:#eef6ff;--accent-text:#1d4ed8;--accent-border:#bfdbfe;
--danger-bg:#fef3f2;--danger-border:#fecdca;--danger-text:#b42318;
--success-bg:#ecfdf3;--success-border:#abefc6;--success-text:#147a2e;
--warn-text:#9a6700;--warn-bg:#fff8e6;--warn-border:#fde68a;--pos:#047857;--neg:#dc2626;--zero:#64748b;
--shadow-color:#ddd;--table-border:#eee;--table-header-bg:#fafafa;--details-bg:#eef2f6;--track-bg:#e2e8f0;--pill-neutral-bg:#f1f5f9;--pager-active-bg:#111;
--font-xs:12px;--font-sm:13px;--font-table:14px;--font-body:15px;--font-control:15px;--font-status:18px;--font-section:22px;--font-kpi:24px;--font-page:26px}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg-page:#0b0f17;--bg-surface:#171b26;--bg-surface-alt:#1c2130;--bg-accent-card:#1e293b;--bg-section:#0b0f17;--section-border:transparent;
--text-primary:#e5e7eb;--text-secondary:#94a3b8;--text-muted:#94a3b8;--text-strong:#cbd5e1;--text-on-accent:#fff;--text-on-accent-muted:#cbd5e1;
--border:#2d3444;--border-strong:#3a4254;--hover-overlay:rgba(255,255,255,.08);
--accent:#60a5fa;--accent-bg:#1e3a5f;--accent-text:#93c5fd;--accent-border:#2d5b8a;
--danger-bg:#3f1d1d;--danger-border:#7f1d1d;--danger-text:#fca5a5;
--success-bg:#14291d;--success-border:#14532d;--success-text:#86efac;
--warn-text:#fbbf24;--warn-bg:#3a2e0a;--warn-border:#78350f;--pos:#34d399;--neg:#f87171;--zero:#94a3b8;
--shadow-color:rgba(0,0,0,.5);--table-border:#2d3444;--table-header-bg:#1c2130;--details-bg:var(--bg-page);--track-bg:#2d3444;--pill-neutral-bg:#232a3b;--pager-active-bg:#3a4254}
:root:not([data-theme="light"]) .dashboard-logo{filter:brightness(0) invert(1);opacity:.92}
}
:root[data-theme="light"]{color-scheme:light}
:root[data-theme="dark"]{color-scheme:dark;
--bg-page:#0b0f17;--bg-surface:#171b26;--bg-surface-alt:#1c2130;--bg-accent-card:#1e293b;--bg-section:#0b0f17;--section-border:transparent;
--text-primary:#e5e7eb;--text-secondary:#94a3b8;--text-muted:#94a3b8;--text-strong:#cbd5e1;--text-on-accent:#fff;--text-on-accent-muted:#cbd5e1;
--border:#2d3444;--border-strong:#3a4254;--hover-overlay:rgba(255,255,255,.08);
--accent:#60a5fa;--accent-bg:#1e3a5f;--accent-text:#93c5fd;--accent-border:#2d5b8a;
--danger-bg:#3f1d1d;--danger-border:#7f1d1d;--danger-text:#fca5a5;
--success-bg:#14291d;--success-border:#14532d;--success-text:#86efac;
--warn-text:#fbbf24;--warn-bg:#3a2e0a;--warn-border:#78350f;--pos:#34d399;--neg:#f87171;--zero:#94a3b8;
--shadow-color:rgba(0,0,0,.5);--table-border:#2d3444;--table-header-bg:#1c2130;--details-bg:var(--bg-page);--track-bg:#2d3444;--pill-neutral-bg:#232a3b;--pager-active-bg:#3a4254}
:root[data-theme="dark"] .dashboard-logo{filter:brightness(0) invert(1);opacity:.92}
*{box-sizing:border-box} html{overflow-x:hidden;overflow-y:scroll;scrollbar-gutter:stable} body{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:24px;padding-top:72px;background:var(--bg-page);color:var(--text-primary);line-height:1.5} .dashboard-header,.tabs{max-width:1600px;margin-left:auto;margin-right:auto}.sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
.dashboard-header{position:fixed;top:0;left:0;right:0;z-index:30;background:var(--bg-page);padding:9px 24px;border-bottom:1px solid var(--border);will-change:transform;backface-visibility:hidden;display:flex;align-items:center;justify-content:space-between;gap:16px}.dashboard-header-left,.dashboard-header-right{display:flex;align-items:center;gap:16px;min-width:0}.dashboard-header-right{justify-content:flex-end}
.theme-toggle{display:flex;background:var(--bg-surface-alt);border-radius:999px;padding:3px;gap:2px;flex-shrink:0} .theme-toggle-btn{display:flex;align-items:center;justify-content:center;width:30px;height:30px;border:0;border-radius:999px;background:transparent;color:var(--text-secondary);cursor:pointer} .theme-toggle-btn[aria-pressed="true"]{background:var(--bg-surface);color:var(--text-primary);box-shadow:0 1px 2px var(--shadow-color)}
.dashboard-brand{display:inline-flex;align-items:center;padding:0;line-height:0} .dashboard-logo{display:block;height:36px;width:auto;max-width:220px;object-fit:contain} .dashboard-meta{color:var(--text-secondary);font-size:13px;white-space:nowrap} h2{line-height:1.3} .muted{color:var(--text-muted);overflow-wrap:anywhere} .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:22px 0}
.card{min-width:0;background:var(--bg-surface);border-radius:12px;padding:16px;box-shadow:0 1px 4px var(--shadow-color)} .card span{display:block;font-size:24px;margin-top:8px;overflow-wrap:anywhere}
.home-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:22px 0 10px} .home-heading h2{margin:0 0 4px;font-size:24px} .home-heading p{margin:0} .system-pill{padding:8px 12px;border-radius:999px;background:var(--bg-surface);border:1px solid var(--border-strong);white-space:nowrap} .system-pill.ok{background:var(--success-bg);border-color:var(--success-border)} .system-pill.bad{background:var(--danger-bg);border-color:var(--danger-border)}
.profile-compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0 24px} .profile-compare-card{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:14px;padding:20px;box-shadow:0 1px 4px var(--shadow-color)} .profile-compare-label{display:block;color:var(--text-on-accent-muted);font-size:13px} .profile-compare-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:12px} .profile-compare-card strong{display:block;font-size:clamp(22px,2vw,28px);margin:10px 0;overflow-wrap:anywhere} .sparkline{flex-shrink:0;overflow:visible} .profile-compare-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:13px;color:var(--text-on-accent-muted)} .profile-compare-stats b{font-weight:700}
.operation-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px} .operation-grid>div{background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:16px 18px;min-width:0} .operation-grid span,.operation-grid b{display:block} .operation-grid span{font-size:13px;color:var(--text-on-accent-muted)} .operation-grid b{margin-top:6px;font-size:17px;overflow-wrap:anywhere} .operation-grid small{display:block;color:var(--text-on-accent-muted);font-size:12px;margin-top:7px}.section-divider{margin:24px 0 10px}.section-divider h2{margin-bottom:4px}.observation-details{margin:16px 0}.home-alert{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 18px;margin:14px 0;border:1px solid var(--border);border-radius:10px;background:var(--bg-surface)}.home-alert b{font-size:16px}.home-alert span{color:var(--text-secondary);font-size:13px}.home-alert.warn{border-color:var(--warn-border);background:var(--warn-bg)}.home-alert.ok{border-color:var(--success-border);background:var(--success-bg)}.state-badge{display:inline-block!important;width:max-content;padding:3px 9px;border-radius:999px;font-size:14px!important}.state-ok{color:var(--success-text);background:var(--success-bg)}.state-warn{color:var(--warn-text);background:var(--warn-bg)}.state-bad{color:var(--danger-text);background:var(--danger-bg)}.state-neutral{color:var(--text-secondary);background:var(--pill-neutral-bg)} .empty-state{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:22px;border:1px dashed var(--border-strong);border-radius:10px;background:var(--bg-surface-alt)} .empty-state b{color:var(--text-strong)} .empty-state span{color:var(--text-secondary)} .progress-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px} .progress-track{height:12px;background:var(--track-bg);border-radius:999px;overflow:hidden} .progress-track span{display:block;height:100%;background:var(--accent);border-radius:inherit} .learning-status p{margin-bottom:0}
.tracking-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0} .tracking-card{background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:17px 18px;box-shadow:0 1px 4px var(--shadow-color)} .tracking-card span,.tracking-card b{display:block} .tracking-card span{color:var(--text-on-accent-muted);font-size:13px} .tracking-card b{font-size:22px;margin-top:7px}
.highlight-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}
.highlight{background:var(--bg-surface);color:var(--text-primary);border-radius:14px;padding:16px;box-shadow:0 1px 4px var(--shadow-color);border:1px solid var(--border)} .highlight b{display:block;color:var(--text-strong)} .highlight span{display:block;color:var(--text-primary);font-size:24px;font-weight:800;margin-top:8px}
.tabs{margin-top:12px} .tab-input{display:none} .tab-labels{display:flex;flex-direction:column;gap:4px;width:200px;position:fixed;top:86px;left:max(24px,calc((100vw - 1648px)/2 + 24px));z-index:20;background:var(--bg-accent-card);border-radius:12px;padding:10px;box-shadow:0 1px 4px var(--shadow-color)} .tab-label{display:flex;align-items:center;gap:10px;border-radius:8px;padding:11px 14px;cursor:pointer;font-weight:600;color:var(--text-on-accent-muted)} .tab-label:hover{background:var(--hover-overlay)} .tab-label svg{flex-shrink:0}
.tab-panel{display:none;margin-left:228px;min-width:0} #tab-stocks:checked~.tab-labels label[for="tab-stocks"],#tab-tracking:checked~.tab-labels label[for="tab-tracking"],#tab-trader:checked~.tab-labels label[for="tab-trader"],#tab-research:checked~.tab-labels label[for="tab-research"],#tab-real-account:checked~.tab-labels label[for="tab-real-account"],#tab-system:checked~.tab-labels label[for="tab-system"]{background:var(--accent-bg);color:var(--accent-text)}
#tab-stocks:checked~#stocks-panel,#tab-tracking:checked~#tracking-panel,#tab-trader:checked~#trader-panel,#tab-research:checked~#research-panel,#tab-real-account:checked~#real-account-panel,#tab-system:checked~#system-panel{display:block}
section{min-width:0;background:var(--bg-section);border:1px solid var(--section-border);border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 1px 4px var(--shadow-color);overflow-x:auto;overflow-y:hidden} section h2{margin:0 0 16px}
details{min-width:0;background:var(--details-bg);border-radius:12px;margin:20px 0} details summary{cursor:pointer;padding:16px 18px;font-weight:700} .details-body{padding:0 18px 2px} .details-body section{box-shadow:none;border:1px solid var(--section-border)}.compact-details{margin:14px 0;background:var(--bg-surface-alt);border:1px solid var(--border)}.inline-details{margin:5px 0 0;background:transparent}.inline-details summary{display:inline;padding:0;color:var(--accent-text);font-size:12px;font-weight:600}.inline-details div{margin-top:6px;color:var(--text-secondary);font-size:12px;line-height:1.5;white-space:normal;min-width:260px}
table{border-collapse:collapse;width:100%;font-size:14px} th,td{border-bottom:1px solid var(--table-border);text-align:left;padding:10px 12px;white-space:nowrap} th{background:var(--table-header-bg);position:sticky;top:0;z-index:1} .num{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--success-text);font-weight:600} .warn{color:var(--warn-text);font-weight:600} .bad{color:var(--danger-text);font-weight:600} .pos{color:var(--pos);font-weight:700} .neg{color:var(--neg);font-weight:700} .zero{color:var(--zero);font-weight:600}
.status-pill{display:inline-block;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap} .status-pill.pill-accent{background:var(--accent-bg);color:var(--accent-text)} .status-pill.pill-danger{background:var(--danger-bg);color:var(--danger-text)} .status-pill.pill-neutral{background:var(--pill-neutral-bg);color:var(--text-secondary)} .status-pill.pill-warn{background:var(--warn-bg);color:var(--warn-text)}
.nav-badge{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;margin-left:auto;border-radius:999px;background:var(--danger-bg);color:var(--danger-text);font-size:11px;font-weight:700} .nav-badge[hidden]{display:none}
.table-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:0 0 16px} .table-heading h2{margin:0} .table-count{color:var(--text-secondary);font-size:13px;font-weight:600;white-space:nowrap}
.pager{display:flex;gap:6px;align-items:center;justify-content:center;margin-top:10px} .pager button{border:1px solid var(--border-strong);background:var(--bg-surface);color:var(--text-primary);border-radius:8px;padding:6px 10px;cursor:pointer} .pager button.active{background:var(--pager-active-bg);color:#fff;border-color:var(--pager-active-bg)}
.home-note{margin:-6px 0 18px;font-size:13px} .curve-card{background:var(--bg-surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:0 0 20px} .equity-curve{width:100%;height:auto;margin-top:6px} .curve-grid{stroke:var(--table-border)} .curve-axis{fill:var(--text-secondary);font-size:11px} .curve-series{opacity:.22;stroke-width:1.4;transition:opacity .18s,stroke-width .18s} .curve-series.is-active,.curve-series.is-benchmark{opacity:1;stroke-width:3} .curve-legend{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:10px;font-size:13px;color:var(--text-secondary)} .curve-legend-item{display:inline-flex;align-items:center;gap:7px;opacity:.45;transition:opacity .18s} .curve-legend-item.is-active,.curve-legend-item.is-benchmark{opacity:1;color:var(--text-primary)} .curve-legend-item i{width:14px;height:3px;border-radius:2px;display:inline-block} .curve-legend-item b{font-weight:700} .regime-card{background:var(--bg-surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:0 0 20px} .regime-card p{margin:10px 0 0;font-size:13px} .review-text{white-space:pre-wrap;word-break:break-word;background:var(--bg-surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;font-size:13px;line-height:1.6;max-height:520px;overflow:auto;margin:0}
.trader-profile-toggle{display:flex;gap:8px;margin:0 0 18px} .profile-button{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-strong);background:var(--bg-surface);color:var(--text-secondary);font-weight:600;cursor:pointer} .profile-button[aria-pressed="true"]{background:var(--accent-bg);border-color:var(--accent);color:var(--accent-text)}
.trader-account-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0} .trader-balance{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:14px;padding:20px;box-shadow:0 1px 4px var(--shadow-color)} .trader-balance span{display:block;color:var(--text-on-accent-muted)} .trader-balance strong{display:block;font-size:clamp(21px,2vw,28px);margin-top:8px;overflow-wrap:anywhere} .trader-status{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:10px;padding:14px 16px;margin:18px 0} .trader-form{display:flex;gap:10px 12px;align-items:center;flex-wrap:wrap} .trader-form label{font-weight:600} .trader-form input{min-width:0;width:min(100%,320px);padding:10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--bg-surface);color:var(--text-primary)} .trader-form button{padding:10px 14px;border:0;border-radius:8px;background:var(--bg-accent-card);color:var(--text-on-accent);cursor:pointer} .trader-form button:disabled{opacity:.4;cursor:not-allowed}
.trader-breakdown{display:flex;gap:12px 24px;justify-content:flex-end;flex-wrap:wrap;margin:0 2px 18px;color:var(--text-strong)}
.trader-risk-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 16px} .trader-risk-grid>div{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:14px 16px} .trader-risk-grid span,.trader-risk-grid b{display:block} .trader-risk-grid b>span{display:inline;color:inherit;font-size:inherit} .trader-risk-grid span{color:var(--text-on-accent-muted);font-size:13px} .trader-risk-grid b{margin-top:5px;overflow-wrap:anywhere} .order-status{overflow:visible} .order-status>.trader-status{margin:0;background:var(--bg-surface-alt);border-color:var(--border)} .account-actions{background:var(--bg-surface);border:1px solid var(--border);box-shadow:0 1px 4px var(--shadow-color)} .account-actions summary{font-size:18px} .account-actions .trader-controls{margin-top:0}
.sale-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0 20px} .sale-summary-grid>div{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:14px 16px} .sale-summary-grid span,.sale-summary-grid b{display:block} .sale-summary-grid span{color:var(--text-on-accent-muted);font-size:13px} .sale-summary-grid b{font-size:20px;margin-top:6px;overflow-wrap:anywhere} .table-scroll{overflow-x:auto}
.decision-timeline{display:grid;gap:0;padding:0 4px 16px}.decision-item{display:grid;grid-template-columns:110px minmax(0,1fr);gap:18px;padding:16px 6px;border-bottom:1px solid var(--table-border)}.decision-item:last-child{border-bottom:0}.decision-item time{color:var(--text-secondary);font-size:13px;font-variant-numeric:tabular-nums}.decision-item h3{margin:0 0 5px;font-size:16px}.decision-item p{margin:0;color:var(--text-secondary);line-height:1.6}
.experiment-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.experiment-card{min-width:0;padding:16px;border:1px solid var(--border);border-radius:10px;background:var(--bg-surface-alt)}.experiment-card-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.experiment-card h3{margin:0;font-size:15px}.experiment-card>b{display:block;margin:14px 0 6px;font-size:16px}.experiment-card p{margin:0;color:var(--text-secondary);font-size:13px;line-height:1.55}
.trader-chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:20px 0} .trader-chart-grid .donut-card{margin:0;overflow:visible} .donut-layout{display:grid;grid-template-columns:minmax(190px,240px) minmax(0,1fr);align-items:center;gap:24px} .donut-ring{width:220px;aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:var(--track-bg);margin:auto;transition:background .2s ease} .donut-hole{width:58%;aspect-ratio:1;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:var(--bg-surface);box-shadow:0 0 0 1px var(--border)} .donut-hole span{font-size:13px;color:var(--text-secondary)} .donut-hole b{font-size:18px;margin-top:5px;max-width:110px;overflow-wrap:anywhere;color:var(--text-primary)} .donut-legend{display:grid;gap:10px;min-width:0} .donut-legend-row{display:grid;grid-template-columns:12px minmax(0,1fr) auto;align-items:center;gap:9px;font-size:14px} .donut-swatch{width:12px;height:12px;border-radius:4px} .donut-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .donut-value{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap} .donut-empty{color:var(--text-secondary)}
button:focus-visible,input:focus-visible,.tab-label:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
li{margin:4px 0}
/* Shared typography scale: keep the same information role at the same size on every tab and on /us. */
body{font-size:var(--font-body)}
.home-heading h2{font-size:var(--font-page)}
.home-heading p,.muted{font-size:var(--font-body)}
section h2,.section-divider h2{font-size:var(--font-section)}
.tab-label,.profile-button,details summary{font-size:var(--font-control)}
.account-actions summary{font-size:16px}
.card span,.highlight span,.tracking-card b,.sale-summary-grid b{font-size:var(--font-kpi)}
.profile-compare-card strong,.trader-balance strong{font-size:var(--font-kpi)}
.operation-grid b,.trader-risk-grid b{font-size:var(--font-status)}
.home-alert b,.experiment-card>b{font-size:var(--font-status);font-weight:700;line-height:1.4}
.operation-grid span,.profile-compare-label,.profile-compare-stats,.tracking-card span,.sale-summary-grid span,.trader-risk-grid span,.table-count,.home-note,.dashboard-meta,.curve-legend,.regime-card p,.experiment-card p,.decision-item time{font-size:var(--font-sm)}
table{font-size:var(--font-table)}
.status-pill{font-size:var(--font-xs)}
.state-badge{font-size:var(--font-sm)!important}
.experiment-card h3,.decision-item h3{font-size:16px}
@media(max-width:1100px){.donut-layout{grid-template-columns:1fr}}
@media(max-width:1000px){.trader-account-grid,.trader-risk-grid,.sale-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){body{margin:14px} .tab-labels{flex-direction:row;flex-wrap:wrap;position:static;width:auto;box-shadow:none;padding:0;background:transparent} .tab-panel{margin-left:0} .tab-label{padding:9px 12px;background:var(--bg-accent-card);color:var(--text-on-accent-muted)} .home-heading{align-items:flex-start} section{padding:16px} th,td{padding:9px 10px}}
@media(max-width:480px){.profile-compare-grid,.trader-account-grid,.trader-risk-grid,.sale-summary-grid{grid-template-columns:1fr} .home-heading{display:block} .system-pill{display:inline-block;margin-top:10px} .profile-compare-card strong{font-size:24px} .trader-breakdown{justify-content:flex-start;flex-direction:column;gap:6px} .trader-form>*{width:100%}}"""

SHELL_JS = r"""const tabLabels=[...document.querySelectorAll(".tab-label")];
function syncTabs() { tabLabels.forEach(label=>label.setAttribute("aria-selected",document.getElementById(label.htmlFor).checked?"true":"false")); }
tabLabels.forEach((label,index)=>{label.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();label.click();}if(event.key==="ArrowRight"||event.key==="ArrowLeft"){event.preventDefault();const next=(index+(event.key==="ArrowRight"?1:-1)+tabLabels.length)%tabLabels.length;tabLabels[next].focus();tabLabels[next].click();}});label.addEventListener("click",()=>setTimeout(syncTabs));}); syncTabs();
const themeKey = "stockAlarm.theme";
const themeButtons = [...document.querySelectorAll(".theme-toggle-btn")];
function applyTheme(choice) {
  if(choice) document.documentElement.setAttribute("data-theme", choice);
  else document.documentElement.removeAttribute("data-theme");
  const effective = choice || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  themeButtons.forEach(btn=>btn.setAttribute("aria-pressed", btn.dataset.themeChoice === effective ? "true" : "false"));
}
themeButtons.forEach(btn=>btn.addEventListener("click", () => {
  localStorage.setItem(themeKey, btn.dataset.themeChoice);
  applyTheme(btn.dataset.themeChoice);
}));
applyTheme(localStorage.getItem(themeKey));"""

NAV_ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>',
    "chart": '<path d="M6 20V14M12 20V6M18 20v-8"/>',
    "settings": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
    "flask": '<path d="M9 3h6M10 3v6L4.5 18.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3"/><path d="M7 15h10"/>',
    "wallet": '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><circle cx="16" cy="14" r="1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>',
}


def nav_icon(name: str) -> str:
    return f'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{NAV_ICONS[name]}</svg>'


def e(value: object) -> str:
    return html.escape(str(value or ""))


def shell_header(logo_uri: str, generated: str, extra: str = "") -> str:
    """`generated` is already-escaped text; `extra` is raw HTML placed at the end of the header."""
    return f'''<header class="dashboard-header">
<div class="dashboard-header-left">
<a class="dashboard-brand" href="#" aria-label="stockAlarm 대시보드 홈"><img class="dashboard-logo" src="{logo_uri}" alt="stockAlarm"></a>
<div class="dashboard-meta">{generated}</div>
</div>
<div class="dashboard-header-right">
<div class="theme-toggle" role="radiogroup" aria-label="테마 선택">
<button type="button" class="theme-toggle-btn" data-theme-choice="light" aria-label="라이트 모드" aria-pressed="false">{nav_icon("sun")}</button>
<button type="button" class="theme-toggle-btn" data-theme-choice="dark" aria-label="다크 모드" aria-pressed="false">{nav_icon("moon")}</button>
</div>{extra}</div>
</header>'''


NL = "\n"


def shell_tabs(tabs: list[tuple[str, str, str, str]], panels: dict[str, str]) -> str:
    """tabs: (id, icon, label, panel_id) with ids matching the per-id CSS rules (tab-stocks/-trader/-research/-system)."""
    inputs = NL.join(f'<input class="tab-input" id="{i}" name="tabs" type="radio"{" checked" if n == 0 else ""}>' for n, (i, *_rest) in enumerate(tabs))
    labels = NL.join(f'<label class="tab-label" for="{i}" role="tab" tabindex="0">{nav_icon(icon)}{text}</label>' for i, icon, text, _p in tabs)
    body = NL.join(f'<div class="tab-panel" id="{pid}" role="tabpanel">{panels[pid]}</div>' for _i, _icon, _t, pid in tabs)
    return f'''<div class="tabs">
{inputs}
<div class="tab-labels" role="tablist" aria-label="대시보드 화면">
{labels}
</div>
{body}
</div>'''
