"""
Export Utilities for Fast PDF Duplicate Finder.
Supports exporting duplicate reports to CSV and Excel (.xlsx) formats.
"""
from __future__ import annotations

import csv
import os
from typing import List, Optional, Tuple

from scanner import DuplicateGroup, is_heuristic_match


def export_to_csv(duplicate_groups: List[DuplicateGroup], filepath: str) -> Tuple[bool, str]:
    """Exports duplicate groups to a CSV report."""
    try:
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Group ID",
                "Match Type",
                "Group Size",
                "File Name",
                "File Size (Bytes)",
                "Formatted Size",
                "Modified Date",
                "Full Path"
            ])

            for g_idx, group in enumerate(duplicate_groups, start=1):
                for finfo in group.files:
                    writer.writerow([
                        f"Group {g_idx}",
                        group.match_type,
                        group.formatted_size,
                        finfo.filename,
                        finfo.size,
                        finfo.formatted_size,
                        finfo.formatted_mtime,
                        finfo.path
                    ])
        return True, f"Successfully exported {len(duplicate_groups)} groups to {os.path.basename(filepath)}"
    except Exception as e:
        return False, str(e)


def export_to_excel(duplicate_groups: List[DuplicateGroup], filepath: str) -> Tuple[bool, str]:
    """Exports duplicate groups to an Excel spreadsheet using openpyxl."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        # Fallback to CSV if openpyxl is not present
        csv_fallback = os.path.splitext(filepath)[0] + ".csv"
        return export_to_csv(duplicate_groups, csv_fallback)

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Duplicate Report"

        # Headers
        headers = [
            "Group #", "Match Type", "Filename", "Size", "Date Modified", "Folder Location", "Full File Path"
        ]
        ws.append(headers)

        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")

        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        row_fill_a = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
        row_fill_b = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

        current_row = 2
        for g_idx, group in enumerate(duplicate_groups, start=1):
            fill = row_fill_a if g_idx % 2 == 1 else row_fill_b
            for finfo in group.files:
                ws.append([
                    f"Group {g_idx}",
                    group.match_type,
                    finfo.filename,
                    finfo.formatted_size,
                    finfo.formatted_mtime,
                    finfo.folder,
                    finfo.path
                ])
                for col_num in range(1, len(headers) + 1):
                    c = ws.cell(row=current_row, column=col_num)
                    c.fill = fill
                    c.font = Font(name="Segoe UI", size=10)
                current_row += 1

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 60)

        wb.save(filepath)
        return True, f"Successfully exported report to {os.path.basename(filepath)}"
    except Exception as e:
        return False, str(e)


def export_to_html(duplicate_groups: List[DuplicateGroup], filepath: str, stats: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Exports duplicate groups to a standalone, responsive, interactive HTML dashboard.
    Works 100% offline with zero external CDNs or JavaScript libraries.
    Includes SVG Donut Chart, live client-side search filtering, and 1-click path copying.
    """
    try:
        import time
        import html

        def fmt_bytes(sz: int) -> str:
            val = float(sz)
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if val < 1024.0:
                    return f"{val:.1f} {unit}" if unit != 'B' else f"{int(val)} B"
                val /= 1024.0
            return f"{val:.1f} PB"

        total_groups = len(duplicate_groups)
        has_potential = any(is_heuristic_match(g.match_type) for g in duplicate_groups)
        total_files = sum(len(g.files) for g in duplicate_groups)
        total_wasted = sum(g.wasted_bytes for g in duplicate_groups)
        total_kept = sum(g.files[0].size for g in duplicate_groups if g.files)
        total_redundant = sum(len(g.files) - 1 for g in duplicate_groups if len(g.files) > 1)

        combined_storage = total_wasted + total_kept
        wasted_pct = (total_wasted / combined_storage * 100) if combined_storage > 0 else 0
        kept_pct = 100.0 - wasted_pct if combined_storage > 0 else 100.0

        # Donut Chart SVG calculations (Circumference of r=54 is ~339.29)
        circ = 339.292
        wasted_dash = circ * (wasted_pct / 100.0)
        kept_dash = circ * (kept_pct / 100.0)
        wasted_offset = 0
        kept_offset = -wasted_dash

        gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
        scan_info = ""
        if stats and isinstance(stats, dict):
            tot_s = stats.get("total_scanned_files") or stats.get("total_files")
            elap = stats.get("elapsed_seconds")
            if tot_s:
                scan_info += f" • Scanned {tot_s:,} files"
            if elap is not None:
                scan_info += f" in {float(elap):.2f}s"

        cards_html = []
        for g_idx, group in enumerate(duplicate_groups, start=1):
            potential = is_heuristic_match(group.match_type)
            rows_html = []
            for f_idx, finfo in enumerate(group.files):
                is_keep = (f_idx == 0)
                badge = (
                    '<span class="badge badge-keep">⭐ KEEP</span>'
                    if is_keep
                    else '<span class="badge badge-del">🗑️ DUPLICATE</span>'
                )
                if potential:
                    badge = '<span class="badge">POTENTIAL MATCH (Manual Review Required)</span>'
                row_cls = "row-keep" if is_keep else "row-del"
                escaped_path = html.escape(finfo.path)
                escaped_name = html.escape(finfo.filename)
                escaped_folder = html.escape(finfo.folder)

                rows_html.append(f"""
                <tr class="{row_cls}">
                    <td>{badge}</td>
                    <td class="file-name" title="{escaped_path}"><b>{escaped_name}</b></td>
                    <td class="file-size">{finfo.formatted_size}</td>
                    <td class="file-date">{finfo.formatted_mtime}</td>
                    <td class="file-loc" title="{escaped_path}">{escaped_folder}</td>
                    <td class="file-act">
                        <button class="btn-copy" data-path="{escaped_path}" onclick="copyPath(this, this.dataset.path)">📋 Copy</button>
                    </td>
                </tr>
                """)

            escaped_match = html.escape(group.match_type)
            space_label = "Compared data" if potential else "Wasted"
            cards_html.append(f"""
            <div class="group-card" data-group="{g_idx}" data-match="{escaped_match}">
                <div class="group-header">
                    <div class="group-title">
                        <span class="group-badge">📦 Group #{g_idx}</span>
                        <span class="match-badge">{escaped_match}</span>
                        <span class="copies-badge">{len(group.files)} copies</span>
                    </div>
                    <div class="group-stats">
                        <span>Group Size: <b>{group.formatted_size}</b></span>
                        <span class="wasted-pill">{space_label}: <b>{group.formatted_size if potential else group.formatted_wasted}</b></span>
                    </div>
                </div>
                <div class="table-wrap">
                    <table class="group-table">
                        <thead>
                            <tr>
                                <th style="width: 100px;">Status</th>
                                <th>Filename</th>
                                <th style="width: 110px;">Size</th>
                                <th style="width: 150px;">Modified Date</th>
                                <th>Folder Location</th>
                                <th style="width: 80px;">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(rows_html)}
                        </tbody>
                    </table>
                </div>
            </div>
            """)

        all_cards_html = "".join(cards_html)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>All Files Duplicate Checker — Audit Report</title>
<style>
:root {{
    --bg: #0b0f19;
    --card-bg: #151d2f;
    --card-border: #223049;
    --text-primary: #f1f5f9;
    --text-muted: #94a3b8;
    --accent-blue: #3b82f6;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --accent-amber: #f59e0b;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    background-color: var(--bg);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
    padding: 24px;
}}
.container {{ max-width: 1400px; margin: 0 auto; }}
header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--card-border);
    padding-bottom: 20px;
    margin-bottom: 24px;
}}
.header-title h1 {{ font-size: 24px; font-weight: 700; color: #fff; }}
.header-title p {{ font-size: 13px; color: var(--text-muted); margin-top: 4px; }}
.header-badge {{
    background: rgba(59, 130, 246, 0.15);
    border: 1px solid rgba(59, 130, 246, 0.4);
    color: #60a5fa;
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
}}

/* Metric Cards */
.metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.metric-card {{
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 20px;
}}
.metric-lbl {{ font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
.metric-val {{ font-size: 28px; font-weight: 800; color: #fff; margin-top: 6px; }}
.metric-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
.color-wasted {{ color: var(--accent-red); }}
.color-kept {{ color: var(--accent-green); }}

/* Storage Visualizer Card */
.visualizer-card {{
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 36px;
    flex-wrap: wrap;
}}
.chart-container {{ position: relative; width: 140px; height: 140px; }}
.donut-svg {{ transform: rotate(-90deg); }}
.chart-center {{
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
}}
.chart-center span {{ font-size: 11px; color: var(--text-muted); }}
.chart-center strong {{ display: block; font-size: 15px; color: #fff; font-weight: 700; }}
.chart-legend {{ display: flex; flex-direction: column; gap: 12px; flex: 1; }}
.legend-item {{ display: flex; align-items: center; justify-content: space-between; font-size: 14px; }}
.legend-tag {{ display: flex; align-items: center; gap: 8px; font-weight: 600; }}
.legend-dot {{ width: 12px; height: 12px; border-radius: 3px; }}

/* Controls Bar */
.controls-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}}
.search-box {{
    flex: 1;
    min-width: 280px;
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 8px;
    padding: 10px 16px;
    color: #fff;
    font-size: 14px;
    outline: none;
}}
.search-box:focus {{ border-color: var(--accent-blue); }}
.count-pill {{
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-muted);
}}

/* Duplicate Group Cards */
.groups-container {{ display: flex; flex-direction: column; gap: 16px; }}
.group-card {{
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    overflow: hidden;
}}
.group-header {{
    background: rgba(255, 255, 255, 0.02);
    border-bottom: 1px solid var(--card-border);
    padding: 12px 18px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
}}
.group-title {{ display: flex; align-items: center; gap: 10px; }}
.group-badge {{ font-weight: 700; font-size: 14px; color: #fff; }}
.match-badge {{
    background: rgba(59, 130, 246, 0.2);
    color: #93c5fd;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
}}
.copies-badge {{
    background: rgba(245, 158, 11, 0.15);
    color: #fcd34d;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
}}
.group-stats {{ display: flex; gap: 16px; font-size: 12px; color: var(--text-muted); align-items: center; }}
.wasted-pill {{
    background: rgba(239, 68, 68, 0.15);
    color: #fca5a5;
    padding: 3px 8px;
    border-radius: 4px;
}}

/* Table */
.table-wrap {{ overflow-x: auto; }}
.group-table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
.group-table th {{
    background: rgba(0, 0, 0, 0.2);
    color: var(--text-muted);
    font-weight: 600;
    padding: 8px 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.group-table td {{ padding: 10px 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); vertical-align: middle; }}
.row-keep {{ background: rgba(16, 185, 129, 0.04); }}
.row-del {{ background: transparent; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
.badge-keep {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }}
.badge-del {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
.file-name {{ font-family: monospace; font-size: 13px; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.file-loc {{ color: var(--text-muted); font-size: 12px; max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.file-size {{ font-weight: 600; font-family: monospace; }}
.file-date {{ color: var(--text-muted); font-size: 12px; }}
.btn-copy {{
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--card-border);
    color: var(--text-muted);
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    transition: all 0.15s ease;
}}
.btn-copy:hover {{ background: rgba(255, 255, 255, 0.15); color: #fff; }}

footer {{
    margin-top: 40px;
    border-top: 1px solid var(--card-border);
    padding-top: 20px;
    text-align: center;
    color: var(--text-muted);
    font-size: 12px;
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <div class="header-title">
            <h1>⚡ All Files Duplicate Checker</h1>
            <p>Generated on {gen_time}{scan_info} • Studio Deduplication Audit</p>
        </div>
        <div class="header-badge">
            Studio Edition
        </div>
    </header>

    <section class="metrics-grid">
        <div class="metric-card">
            <div class="metric-lbl">Duplicate Groups</div>
            <div class="metric-val">{total_groups:,}</div>
            <div class="metric-sub">Identified candidate clusters</div>
        </div>
        <div class="metric-card">
            <div class="metric-lbl">Redundant Copies</div>
            <div class="metric-val">{total_redundant:,}</div>
            <div class="metric-sub">Across {total_files:,} scanned duplicates</div>
        </div>
        <div class="metric-card">
            <div class="metric-lbl">Reclaimable Storage</div>
            <div class="metric-val color-wasted">{fmt_bytes(total_wasted)}</div>
            <div class="metric-sub">Wasted disk space</div>
        </div>
        <div class="metric-card">
            <div class="metric-lbl">Kept Original Data</div>
            <div class="metric-val color-kept">{fmt_bytes(total_kept)}</div>
            <div class="metric-sub">Protected master files</div>
        </div>
    </section>

    <section class="visualizer-card">
        <div class="chart-container">
            <svg class="donut-svg" width="140" height="140" viewBox="0 0 140 140">
                <circle cx="70" cy="70" r="54" fill="none" stroke="#223049" stroke-width="16" />
                <circle cx="70" cy="70" r="54" fill="none" stroke="#10b981" stroke-width="16"
                    stroke-dasharray="{kept_dash:.2f} {circ:.2f}" stroke-dashoffset="{kept_offset:.2f}" />
                <circle cx="70" cy="70" r="54" fill="none" stroke="#ef4444" stroke-width="16"
                    stroke-dasharray="{wasted_dash:.2f} {circ:.2f}" stroke-dashoffset="{wasted_offset:.2f}" />
            </svg>
            <div class="chart-center">
                <span>RECLAIM</span>
                <strong>{wasted_pct:.0f}%</strong>
            </div>
        </div>
        <div class="chart-legend">
            <div class="legend-item">
                <div class="legend-tag">
                    <div class="legend-dot" style="background: #ef4444;"></div>
                    <span>Reclaimable Duplicate Space</span>
                </div>
                <div><b>{fmt_bytes(total_wasted)}</b> ({wasted_pct:.1f}%)</div>
            </div>
            <div class="legend-item">
                <div class="legend-tag">
                    <div class="legend-dot" style="background: #10b981;"></div>
                    <span>Protected Master Content</span>
                </div>
                <div><b>{fmt_bytes(total_kept)}</b> ({kept_pct:.1f}%)</div>
            </div>
        </div>
    </section>

    <div class="controls-bar">
        <input type="text" id="searchInput" class="search-box"
            placeholder="🔍 Instant filter by filename, folder location, or match type (type live)..."
            oninput="filterGroups(this.value)">
        <div id="countPill" class="count-pill">Showing {total_groups} of {total_groups} Groups</div>
    </div>

    <div id="groupsContainer" class="groups-container">
        {all_cards_html}
    </div>

    <footer>
        <p>All Files Duplicate Checker • High-Performance Multi-Core Deduplication Engine</p>
    </footer>
</div>

<script>
function filterGroups(query) {{
    const q = query.trim().toLowerCase();
    const cards = document.querySelectorAll('.group-card');
    let visible = 0;
    cards.forEach(card => {{
        const text = card.textContent.toLowerCase();
        if (!q || text.includes(q)) {{
            card.style.display = '';
            visible++;
        }} else {{
            card.style.display = 'none';
        }}
    }});
    document.getElementById('countPill').textContent = `Showing ${{visible}} of ${{cards.length}} Groups`;
}}

function copyPath(btn, path) {{
    if (!navigator.clipboard || !navigator.clipboard.writeText) {{
        prompt("Copy path manually:", path);
        return;
    }}
    navigator.clipboard.writeText(path).then(() => {{
        const orig = btn.textContent;
        btn.textContent = '✓ Copied!';
        btn.style.color = '#34d399';
        setTimeout(() => {{
            btn.textContent = orig;
            btn.style.color = '';
        }}, 1500);
    }}).catch(() => {{
        prompt("Copy path manually:", path);
    }});
}}
</script>
</body>
</html>
"""
        if has_potential:
            for old, new in {
                "Redundant Copies": "Potential Matches",
                "Reclaimable Storage": "Data Requiring Review",
                "Wasted disk space": "POTENTIAL MATCH (Manual Review Required)",
                "Kept Original Data": "Reference Data",
                "Protected master files": "Reference selections; content not verified",
                "Reclaimable Duplicate Space": "Candidate data; savings unverified",
                "Protected Master Content": "Reference data",
                "RECLAIM": "REVIEW",
                "scanned duplicates": "candidate files",
            }.items():
                html_content = html_content.replace(old, new)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        return True, f"Successfully exported interactive HTML report to {os.path.basename(filepath)}"
    except Exception as e:
        return False, str(e)

