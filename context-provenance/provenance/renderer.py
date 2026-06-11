"""
Swim-lane HTML renderer for provenance records.

Generates a standalone HTML file with four parallel channels,
each showing its sources. Channel 4 is visually distinct —
present but opaque.
"""

import json
from html import escape as esc
from pathlib import Path
from provenance.channels import ProvenanceRecord, ChannelType


CHANNEL_META = {
    ChannelType.SKILL: {
        "label": "1 — Skill Instructions",
        "color": "#2563eb",
        "bg": "#eff6ff",
        "icon": "&#x2699;",
        "desc": "System prompts, loaded skills, methodology",
    },
    ChannelType.RETRIEVED: {
        "label": "2 — Retrieved Context",
        "color": "#059669",
        "bg": "#ecfdf5",
        "icon": "&#x1F4C2;",
        "desc": "Files read, tools called, searches run",
    },
    ChannelType.CONVERSATION: {
        "label": "3 — Conversation Memory",
        "color": "#d97706",
        "bg": "#fffbeb",
        "icon": "&#x1F4AC;",
        "desc": "Prior turns, stored memories, session context",
    },
    ChannelType.TRAINING: {
        "label": "4 — Training Interpolation",
        "color": "#6b7280",
        "bg": "#f3f4f6",
        "icon": "&#x2588;",
        "desc": "Unattributable. The black box.",
    },
}


def _safe_json_embed(json_str: str) -> str:
    """Escape JSON for safe embedding inside an HTML <script> block."""
    return json_str.replace("</" , "<\\/")


def render_swimlane(record: ProvenanceRecord, output_path: str = None) -> str:
    """
    Render a provenance record as a standalone HTML swim-lane diagram.

    Args:
        record: The provenance record to visualize
        output_path: If provided, writes HTML to this file

    Returns:
        The HTML string
    """
    channels_html = ""
    for ct in ChannelType:
        ch = record.channels[ct]
        meta = CHANNEL_META[ct]
        is_opaque = ct == ChannelType.TRAINING

        sources_html = ""
        if is_opaque:
            sources_html = f"""
            <div class="source opaque">
                <div class="source-label">{esc(ch.sources[0].label) if ch.sources else 'Model training data'}</div>
                <div class="source-desc">{esc(ch.sources[0].description) if ch.sources else 'Non-retrievable'}</div>
                {f'<div class="source-note">{esc(ch.note)}</div>' if ch.note else ''}
            </div>"""
        else:
            for s in ch.sources:
                ref = ""
                if s.path:
                    ref = f'<span class="source-ref" title="{esc(s.path)}">&#x1F4CE; {esc(s.path)}</span>'
                elif s.url:
                    ref = f'<a class="source-ref" href="{esc(s.url)}" target="_blank">&#x1F517; {esc(s.url)}</a>'

                weight_html = ""
                if s.weight is not None:
                    pct = int(s.weight * 100)
                    weight_html = f'<span class="weight-badge">{pct}%</span>'

                sources_html += f"""
                <div class="source">
                    <div class="source-label">{esc(s.label)} {weight_html}</div>
                    <div class="source-desc">{esc(s.description)}</div>
                    {ref}
                </div>"""

        if not ch.sources and not is_opaque:
            sources_html = '<div class="source empty">No sources in this channel</div>'

        channels_html += f"""
        <div class="channel {'opaque-channel' if is_opaque else ''}" style="--ch-color: {meta['color']}; --ch-bg: {meta['bg']}">
            <div class="channel-header">
                <span class="channel-icon">{meta['icon']}</span>
                <div>
                    <div class="channel-title">{meta['label']}</div>
                    <div class="channel-desc">{meta['desc']}</div>
                </div>
                <span class="source-count">{len(ch.sources)} source{'s' if len(ch.sources) != 1 else ''}</span>
            </div>
            <div class="sources">{sources_html}</div>
        </div>"""

    opacity_pct = int(record.opacity_ratio * 100)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Provenance: {esc(record.title)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Inter', -apple-system, system-ui, sans-serif; background: #fafafa; color: #1a1a1a; line-height: 1.5; }}

.container {{ max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem; }}

header {{ margin-bottom: 2.5rem; }}
h1 {{ font-size: 1.75rem; font-weight: 600; margin-bottom: 0.25rem; }}
.subtitle {{ color: #6b7280; font-size: 0.9rem; }}
.meta {{ margin-top: 1rem; display: flex; gap: 1.5rem; flex-wrap: wrap; font-size: 0.85rem; color: #6b7280; }}
.meta span {{ display: flex; align-items: center; gap: 0.3rem; }}

.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 2.5rem; }}
.stat {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem 1.25rem; }}
.stat-value {{ font-size: 1.5rem; font-weight: 600; }}
.stat-label {{ font-size: 0.8rem; color: #6b7280; margin-top: 0.15rem; }}
.stat-value.opacity {{ color: #6b7280; }}

.swimlane {{ display: flex; flex-direction: column; gap: 1rem; }}

.channel {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; border-left: 4px solid var(--ch-color); overflow: hidden; }}
.channel-header {{ display: flex; align-items: center; gap: 0.75rem; padding: 1rem 1.25rem; background: var(--ch-bg); border-bottom: 1px solid #e5e7eb; }}
.channel-icon {{ font-size: 1.25rem; }}
.channel-title {{ font-weight: 600; font-size: 0.95rem; }}
.channel-desc {{ font-size: 0.8rem; color: #6b7280; }}
.source-count {{ margin-left: auto; font-size: 0.8rem; color: #6b7280; background: white; padding: 0.15rem 0.6rem; border-radius: 12px; border: 1px solid #e5e7eb; }}

.sources {{ padding: 0.75rem 1.25rem; }}
.source {{ padding: 0.5rem 0; border-bottom: 1px solid #f3f4f6; }}
.source:last-child {{ border-bottom: none; }}
.source-label {{ font-weight: 500; font-size: 0.9rem; display: flex; align-items: center; gap: 0.5rem; }}
.source-desc {{ font-size: 0.82rem; color: #6b7280; margin-top: 0.15rem; }}
.source-ref {{ font-size: 0.78rem; color: #2563eb; text-decoration: none; display: inline-block; margin-top: 0.25rem; word-break: break-all; }}
.source-ref:hover {{ text-decoration: underline; }}
.source-note {{ font-size: 0.82rem; color: #6b7280; margin-top: 0.5rem; font-style: italic; line-height: 1.6; }}
.weight-badge {{ font-size: 0.7rem; background: #eff6ff; color: #2563eb; padding: 0.1rem 0.4rem; border-radius: 4px; }}
.source.empty {{ color: #9ca3af; font-size: 0.85rem; font-style: italic; }}

.opaque-channel {{ border-left-style: dashed; }}
.opaque-channel .channel-header {{ background: repeating-linear-gradient(45deg, #f9fafb, #f9fafb 10px, #f3f4f6 10px, #f3f4f6 20px); }}
.source.opaque {{ background: #f9fafb; padding: 0.75rem; border-radius: 4px; border: 1px dashed #d1d5db; }}

.chain {{ margin-top: 2.5rem; padding: 1.5rem; background: white; border: 1px solid #e5e7eb; border-radius: 8px; }}
.chain h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 0.75rem; }}
.chain p {{ font-size: 0.88rem; color: #374151; line-height: 1.7; }}

footer {{ margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid #e5e7eb; font-size: 0.78rem; color: #9ca3af; text-align: center; }}
footer a {{ color: #6b7280; }}

@media (max-width: 640px) {{
    .summary {{ grid-template-columns: 1fr 1fr; }}
    .meta {{ flex-direction: column; gap: 0.5rem; }}
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Context Provenance</h1>
        <div class="subtitle">{esc(record.title)}</div>
        <div class="meta">
            <span>&#x270D; {esc(record.author)}</span>
            <span>&#x1F4C5; {esc(record.created)}</span>
            {f'<span>&#x1F916; {esc(record.model)}</span>' if record.model else ''}
        </div>
    </header>

    <div class="summary">
        <div class="stat">
            <div class="stat-value">{record.total_sources}</div>
            <div class="stat-label">Total sources identified</div>
        </div>
        <div class="stat">
            <div class="stat-value">{record.retrievable_sources}</div>
            <div class="stat-label">Retrievable (Channels 1-3)</div>
        </div>
        <div class="stat">
            <div class="stat-value opacity">{opacity_pct}%</div>
            <div class="stat-label">Opacity ratio</div>
        </div>
        <div class="stat">
            <div class="stat-value">{len(record.channels[ChannelType.RETRIEVED].sources)}</div>
            <div class="stat-label">Files, tools, searches</div>
        </div>
    </div>

    <div class="swimlane">{channels_html}</div>

    {'<div class="chain"><h2>Chain of Custody</h2><p>' + esc(record.chain_of_custody).replace(chr(10), '</p><p>') + '</p></div>' if record.chain_of_custody else ''}

    <footer>
        Generated by <a href="https://github.com/rubinstein-productions/context-provenance">context-provenance v{record.version}</a>
        &middot; Four-channel attribution for AI-mediated text
    </footer>
</div>

<script>
// Embed provenance JSON for programmatic access
window.__PROVENANCE__ = {_safe_json_embed(record.to_json())};
</script>
</body>
</html>"""

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")

    return html
