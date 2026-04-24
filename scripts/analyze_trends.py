"""
Temporal, specialty, and pathway analysis of FDA AI device evidence transparency.

Reads:   data/fda_classifications.db  +  quantitative_metrics table
Writes:  reports/fig_*.png
         data/summary_*.csv

Run from the FDA_AIeMD_DB directory:
    python scripts/analyze_trends.py
"""
import os
import sqlite3

import matplotlib
matplotlib.use('Agg')   # non-interactive backend — works without a display
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd

DB_PATH   = 'data/fda_classifications.db'
REPORT_DIR = 'reports'

CAT_COLORS = {
    'A': '#2ecc71',   # green  – quantitative
    'B': '#3498db',   # blue   – qualitative
    'C': '#e67e22',   # orange – technical only
    'D': '#e74c3c',   # red    – equivalence only
}
CAT_LABELS = {
    'A': 'A – Quantitative',
    'B': 'B – Qualitative',
    'C': 'C – Technical Only',
    'D': 'D – Equivalence Only',
}

PCT_METRICS = ['sensitivity', 'specificity', 'accuracy', 'ppv', 'npv', 'precision']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_classifications(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT * FROM classifications WHERE category IS NOT NULL",
        conn,
    )
    df['submission_type'] = df['k_number'].apply(_infer_type)
    return df


def load_metrics(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        '''SELECT qm.*, c.panel, c.decision_year, c.study_type, c.dataset_size
           FROM quantitative_metrics qm
           JOIN classifications c ON qm.k_number = c.k_number''',
        conn,
    )
    # Normalise proportion-scale values to percentage scale
    mask = df['metric_type'].isin(PCT_METRICS) & (df['metric_value'] < 1.5)
    df.loc[mask, 'metric_value'] = df.loc[mask, 'metric_value'] * 100
    # Drop implausible accuracy zeros (measurement-range false positives)
    df = df[~((df['metric_type'] == 'accuracy') & (df['metric_value'] == 0))]
    return df


def _infer_type(k: str) -> str:
    if k.startswith('DEN'):
        return 'De Novo'
    if k.startswith('P'):
        return 'PMA'
    return '510(k)'


def save(fig: plt.Figure, name: str) -> None:
    path = os.path.join(REPORT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  saved -> {path}")


def style_ax(ax, title: str, xlabel: str = '', ylabel: str = '') -> None:
    ax.set_title(title, fontsize=13, fontweight='bold', pad=10)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)


# ---------------------------------------------------------------------------
# Figure 1 – Evidence category mix over time (stacked bar by year)
# ---------------------------------------------------------------------------

def fig_temporal(df: pd.DataFrame) -> None:
    yearly = (
        df.groupby(['decision_year', 'category'])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=['A', 'B', 'C', 'D'], fill_value=0)
    )
    yearly_pct = yearly.div(yearly.sum(axis=1), axis=0) * 100

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Raw counts
    bottom = pd.Series(0, index=yearly.index)
    for cat in ['A', 'B', 'C', 'D']:
        axes[0].bar(yearly.index, yearly[cat], bottom=bottom,
                    color=CAT_COLORS[cat], label=CAT_LABELS[cat])
        bottom += yearly[cat]
    style_ax(axes[0], 'AI Device Clearances by Year', 'Year', 'Number of Devices')
    axes[0].legend(fontsize=8, loc='upper left')

    # Percentage
    bottom = pd.Series(0.0, index=yearly_pct.index)
    for cat in ['A', 'B', 'C', 'D']:
        axes[1].bar(yearly_pct.index, yearly_pct[cat], bottom=bottom,
                    color=CAT_COLORS[cat], label=CAT_LABELS[cat])
        bottom += yearly_pct[cat]
    axes[1].yaxis.set_major_formatter(mtick.PercentFormatter())
    style_ax(axes[1], '% Category Mix by Year', 'Year', '% of Devices')
    axes[1].legend(fontsize=8, loc='upper left')

    fig.suptitle('Evidence Transparency Over Time', fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_01_temporal_trends.png')

    # Export summary
    out = yearly.copy()
    out['total'] = out.sum(axis=1)
    out['pct_quantitative'] = (out['A'] / out['total'] * 100).round(1)
    out.to_csv('data/summary_temporal.csv')
    print("  saved -> data/summary_temporal.csv")


# ---------------------------------------------------------------------------
# Figure 2 – Panel transparency (% Category A, horizontal bar)
# ---------------------------------------------------------------------------

def fig_panel(df: pd.DataFrame) -> None:
    panel_stats = (
        df.groupby('panel')
        .agg(
            total=('category', 'count'),
            quantitative=('category', lambda x: (x == 'A').sum()),
        )
        .assign(pct_A=lambda d: d['quantitative'] / d['total'] * 100)
        .sort_values('pct_A', ascending=True)
    )

    # Only panels with ≥ 5 devices
    panel_stats = panel_stats[panel_stats['total'] >= 5]

    fig, ax = plt.subplots(figsize=(10, max(5, len(panel_stats) * 0.45)))
    bars = ax.barh(
        panel_stats.index,
        panel_stats['pct_A'],
        color=[CAT_COLORS['A'] if v >= 30 else '#bdc3c7' for v in panel_stats['pct_A']],
    )
    # Annotate bar ends
    for bar, (_, row) in zip(bars, panel_stats.iterrows()):
        ax.text(
            bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{row['pct_A']:.0f}%  (n={row['total']})",
            va='center', fontsize=8,
        )
    ax.axvline(x=30.8, color='gray', linestyle='--', linewidth=1, label='Overall avg (30.8%)')
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, 85)
    # Custom legend: explain bar colour coding + avg line
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=CAT_COLORS['A'], label='≥ 30% (at/above overall avg)'),
        Patch(facecolor='#bdc3c7',       label='< 30% (below overall avg)'),
        plt.Line2D([0], [0], color='gray', linestyle='--', linewidth=1,
                   label='Overall avg (30.8%)'),
    ]
    ax.legend(handles=legend_handles, fontsize=8, loc='lower right')
    style_ax(ax, 'Quantitative Evidence Reporting by Medical Specialty',
             '% of Devices Reporting Numeric Metrics (Category A)', '')
    fig.tight_layout()
    save(fig, 'fig_02_panel_transparency.png')

    panel_stats.to_csv('data/summary_panel.csv')
    print("  saved -> data/summary_panel.csv")


# ---------------------------------------------------------------------------
# Figure 3 – Submission pathway vs transparency
# ---------------------------------------------------------------------------

def fig_pathway(df: pd.DataFrame) -> None:
    pathway_stats = (
        df.groupby('submission_type')
        .agg(
            total=('category', 'count'),
            quantitative=('category', lambda x: (x == 'A').sum()),
            qualitative=('category',  lambda x: (x == 'B').sum()),
            technical=('category',   lambda x: (x == 'C').sum()),
            equivalence=('category', lambda x: (x == 'D').sum()),
        )
        .assign(pct_A=lambda d: d['quantitative'] / d['total'] * 100)
    )

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # % Category A per pathway
    axes[0].bar(pathway_stats.index, pathway_stats['pct_A'],
                color=[CAT_COLORS['A'], '#16a085', '#8e44ad'][:len(pathway_stats)])
    for i, (idx, row) in enumerate(pathway_stats.iterrows()):
        axes[0].text(i, row['pct_A'] + 0.5, f"{row['pct_A']:.1f}%\n(n={row['total']})",
                     ha='center', fontsize=10)
    axes[0].yaxis.set_major_formatter(mtick.PercentFormatter())
    axes[0].set_ylim(0, 60)
    style_ax(axes[0], '% Quantitative Evidence by Pathway', '', '% Category A')

    # Stacked category mix per pathway
    cats = ['A', 'B', 'C', 'D']
    bottom = [0] * len(pathway_stats)
    for cat in cats:
        vals = pathway_stats[{'A': 'quantitative', 'B': 'qualitative',
                               'C': 'technical', 'D': 'equivalence'}[cat]]
        pcts = vals / pathway_stats['total'] * 100
        axes[1].bar(pathway_stats.index, pcts, bottom=bottom,
                    color=CAT_COLORS[cat], label=CAT_LABELS[cat])
        bottom = [b + p for b, p in zip(bottom, pcts)]
    axes[1].yaxis.set_major_formatter(mtick.PercentFormatter())
    axes[1].legend(fontsize=8)
    style_ax(axes[1], 'Category Mix by Submission Pathway', '', '% of Devices')

    fig.suptitle('Evidence Transparency by Regulatory Pathway',
                 fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_03_pathway_analysis.png')

    pathway_stats.to_csv('data/summary_pathway.csv')
    print("  saved -> data/summary_pathway.csv")


# ---------------------------------------------------------------------------
# Figure 4 – Metric value distributions (sensitivity & specificity box plots)
# ---------------------------------------------------------------------------

def fig_metric_distributions(metrics: pd.DataFrame) -> None:
    top_panels = (
        metrics[metrics['metric_type'] == 'sensitivity']
        .groupby('panel')['k_number']
        .nunique()
        .nlargest(7)
        .index.tolist()
    )

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for ax, mtype in zip(axes, ['sensitivity', 'specificity']):
        data = metrics[
            (metrics['metric_type'] == mtype) & (metrics['panel'].isin(top_panels))
        ]
        groups = [data[data['panel'] == p]['metric_value'].dropna().tolist()
                  for p in top_panels]
        bp = ax.boxplot(groups, labels=top_panels, patch_artist=True,
                        medianprops={'color': 'black', 'linewidth': 2})
        for patch in bp['boxes']:
            patch.set_facecolor('#aed6f1')
        ax.set_xticklabels(top_panels, rotation=30, ha='right', fontsize=8)
        ax.yaxis.set_major_formatter(mtick.PercentFormatter())
        ax.set_ylim(0, 105)
        style_ax(ax, f'{mtype.capitalize()} Distribution by Panel', '', f'{mtype.capitalize()} (%)')

    fig.suptitle('Performance Metric Distributions (Category A Devices)',
                 fontsize=14, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_04_metric_distributions.png')


# ---------------------------------------------------------------------------
# Figure 5 – AUC distribution
# ---------------------------------------------------------------------------

def fig_auc(metrics: pd.DataFrame) -> None:
    auc = metrics[metrics['metric_type'] == 'auc']['metric_value'].dropna()
    if auc.empty:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(auc, bins=20, color=CAT_COLORS['A'], edgecolor='white', linewidth=0.5)
    ax.axvline(auc.median(), color='red', linestyle='--', linewidth=1.5,
               label=f'Median: {auc.median():.2f}')
    ax.axvline(0.9, color='gray', linestyle=':', linewidth=1, label='AUC = 0.90')
    ax.legend(fontsize=9)
    style_ax(ax, f'AUC Distribution (n={len(auc)} extractions from {auc.nunique()}-value range)',
             'AUC', 'Count')
    fig.tight_layout()
    save(fig, 'fig_05_auc_distribution.png')


# ---------------------------------------------------------------------------
# Figure 6 – Category C (bench-only) concentration in Radiology
# ---------------------------------------------------------------------------

def fig_category_c_radiology(df: pd.DataFrame) -> None:
    panels = (
        df.groupby('panel')
        .agg(total=('category', 'count'),
             cat_C=('category', lambda x: (x == 'C').sum()))
        .assign(pct_C=lambda d: d['cat_C'] / d['total'] * 100)
        .query('total >= 10')
        .sort_values('pct_C', ascending=True)
    )

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left: % Cat C by panel
    colors_c = ['#e67e22' if p == 'Radiology' else '#bdc3c7' for p in panels.index]
    bars = axes[0].barh(panels.index, panels['pct_C'], color=colors_c)
    for bar, (_, row) in zip(bars, panels.iterrows()):
        axes[0].text(
            bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
            f"{row['pct_C']:.0f}%  (n={row['cat_C']})",
            va='center', fontsize=8,
        )
    axes[0].xaxis.set_major_formatter(mtick.PercentFormatter())
    axes[0].set_xlim(0, 35)
    style_ax(axes[0], '% Bench-Only (Category C) by Specialty',
             '% of Devices with Technical Evidence Only', '')

    # Right: absolute Cat C count — who owns the total?
    cat_c_all = df[df['category'] == 'C'].copy()
    cat_c_all['panel_grouped'] = cat_c_all['panel'].apply(
        lambda p: p if p == 'Radiology' else 'All Other Specialties'
    )
    pie_data = cat_c_all.groupby('panel_grouped').size()
    pie_colors = ['#e67e22' if p == 'Radiology' else '#bdc3c7' for p in pie_data.index]
    wedges, texts, autotexts = axes[1].pie(
        pie_data, labels=pie_data.index, colors=pie_colors,
        autopct='%1.0f%%', startangle=90,
        textprops={'fontsize': 10},
    )
    for at in autotexts:
        at.set_fontsize(11)
        at.set_fontweight('bold')
    axes[1].set_title('Share of All Cat C Devices\n(Technical/Bench-Only)',
                      fontsize=13, fontweight='bold', pad=10)

    fig.suptitle('Bench-Only Evidence: A Radiology-Specific Pattern',
                 fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_06_category_c_radiology.png')

    panels.to_csv('data/summary_category_c.csv')
    print('  saved -> data/summary_category_c.csv')


# ---------------------------------------------------------------------------
# Figure 7 – Company transparency (top submitters vs Cat A rate)
# ---------------------------------------------------------------------------

def fig_company_transparency(df: pd.DataFrame) -> None:
    company_stats = (
        df.groupby('company')
        .agg(total=('category', 'count'),
             cat_A=('category', lambda x: (x == 'A').sum()))
        .assign(pct_A=lambda d: d['cat_A'] / d['total'] * 100)
        .query('total >= 5')
        .sort_values('total', ascending=False)
        .head(20)
        .sort_values('pct_A', ascending=True)   # re-sort for chart readability
    )

    overall_pct_A = (df['category'] == 'A').mean() * 100

    fig, ax = plt.subplots(figsize=(11, 7))
    colors = [CAT_COLORS['A'] if v >= overall_pct_A else '#bdc3c7'
              for v in company_stats['pct_A']]
    bars = ax.barh(company_stats.index, company_stats['pct_A'], color=colors)

    for bar, (_, row) in zip(bars, company_stats.iterrows()):
        ax.text(
            bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
            f"{row['pct_A']:.0f}%  ({row['cat_A']}/{row['total']} devices)",
            va='center', fontsize=8,
        )

    ax.axvline(overall_pct_A, color='gray', linestyle='--', linewidth=1,
               label=f'Overall avg ({overall_pct_A:.1f}%)')

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=CAT_COLORS['A'], label=f'≥ overall avg ({overall_pct_A:.1f}%)'),
        Patch(facecolor='#bdc3c7',       label=f'< overall avg'),
        plt.Line2D([0], [0], color='gray', linestyle='--', linewidth=1,
                   label=f'Overall avg ({overall_pct_A:.1f}%)'),
    ]
    ax.legend(handles=legend_handles, fontsize=8, loc='lower right')
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, 65)
    style_ax(ax, 'Quantitative Evidence Rate — Top 20 Companies by Device Count',
             '% of Devices Reporting Numeric Metrics (Category A)', '')
    fig.tight_layout()
    save(fig, 'fig_07_company_transparency.png')

    company_stats.sort_values('total', ascending=False).to_csv(
        'data/summary_company.csv'
    )
    print('  saved -> data/summary_company.csv')


# ---------------------------------------------------------------------------
# Figure 8 – Confidence interval reporting
# ---------------------------------------------------------------------------

def fig_ci_reporting(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    metrics_raw = pd.read_sql(
        '''SELECT qm.metric_type, qm.ci_lower, c.decision_year, c.panel
           FROM quantitative_metrics qm
           JOIN classifications c ON qm.k_number = c.k_number''',
        conn,
    )

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # --- Left: CI rate by metric type ---
    ci_by_type = (
        metrics_raw.groupby('metric_type')
        .apply(lambda x: pd.Series({
            'total': len(x),
            'with_ci': x['ci_lower'].notna().sum(),
            'pct_ci': 100 * x['ci_lower'].notna().mean(),
        }))
        .sort_values('pct_ci', ascending=True)
    )
    bar_colors = ['#2980b9' if v >= 30 else '#bdc3c7' for v in ci_by_type['pct_ci']]
    b = axes[0].barh(ci_by_type.index, ci_by_type['pct_ci'], color=bar_colors)
    for bar, (_, row) in zip(b, ci_by_type.iterrows()):
        axes[0].text(
            bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{row['pct_ci']:.0f}%  (n={int(row['total'])})",
            va='center', fontsize=8,
        )
    axes[0].xaxis.set_major_formatter(mtick.PercentFormatter())
    axes[0].set_xlim(0, 90)
    style_ax(axes[0], 'CI Reporting Rate\nby Metric Type',
             '% of Extractions with 95% CI', '')

    # --- Middle: CI rate by year ---
    ci_by_year = (
        metrics_raw[metrics_raw['decision_year'] >= 2016]
        .groupby('decision_year')
        .apply(lambda x: 100 * x['ci_lower'].notna().mean())
        .reset_index()
    )
    ci_by_year.columns = ['year', 'pct_ci']
    axes[1].plot(ci_by_year['year'], ci_by_year['pct_ci'],
                 marker='o', color='#2980b9', linewidth=2)
    axes[1].fill_between(ci_by_year['year'], ci_by_year['pct_ci'],
                         alpha=0.15, color='#2980b9')
    axes[1].yaxis.set_major_formatter(mtick.PercentFormatter())
    axes[1].set_ylim(0, 80)
    style_ax(axes[1], 'CI Reporting Rate Over Time',
             'Year', '% of Metrics with 95% CI')

    # --- Right: CI rate by panel (panels with ≥ 5 metric rows) ---
    ci_by_panel = (
        metrics_raw.groupby('panel')
        .apply(lambda x: pd.Series({
            'total': len(x),
            'pct_ci': 100 * x['ci_lower'].notna().mean(),
        }))
        .query('total >= 5')
        .sort_values('pct_ci', ascending=True)
    )
    axes[2].barh(ci_by_panel.index, ci_by_panel['pct_ci'], color='#2980b9')
    for i, (_, row) in enumerate(ci_by_panel.iterrows()):
        axes[2].text(
            row['pct_ci'] + 0.5, i,
            f"{row['pct_ci']:.0f}%  (n={int(row['total'])})",
            va='center', fontsize=8,
        )
    axes[2].xaxis.set_major_formatter(mtick.PercentFormatter())
    axes[2].set_xlim(0, 90)
    style_ax(axes[2], 'CI Reporting Rate\nby Specialty',
             '% of Metrics with 95% CI', '')

    fig.suptitle('Confidence Interval Reporting in Category A Devices',
                 fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_08_ci_reporting.png')


# ---------------------------------------------------------------------------
# Figure 9 – AI-ethics signal prevalence (requires ai_ethics_signals table)
# ---------------------------------------------------------------------------

def fig_ethics_prevalence(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    try:
        ethics = pd.read_sql(
            '''SELECT ae.*, c.panel, c.decision_year, c.category
               FROM ai_ethics_signals ae
               JOIN classifications c ON ae.k_number = c.k_number
               WHERE ae.text_source != 'unavailable' ''',
            conn,
        )
    except Exception:
        print("  fig_09: ai_ethics_signals table not found — skipping.")
        return

    if ethics.empty:
        print("  fig_09: no ethics signal data yet — skipping.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))

    # --- Left: overall concept prevalence (horizontal bar) ---
    concepts = {
        'Data Provenance\n(prospective/retrospective/synthetic)': 'has_data_provenance',
        'Privacy / HIPAA':                                         'has_privacy',
        'Fairness / Bias':                                         'has_fairness_bias',
        'XAI — General\n(explainability/transparency)':            'has_xai_general',
        'XAI — Named Methods\n(SHAP/LIME/saliency/…)':             'has_xai_method',
        'AI Ethics (general)':                                     'has_ethics_general',
    }
    labels = list(concepts.keys())
    pcts   = [100 * ethics[col].mean() for col in concepts.values()]
    bar_colors = ['#2980b9' if p >= 5 else '#bdc3c7' for p in pcts]

    b = axes[0].barh(labels, pcts, color=bar_colors)
    for bar, pct in zip(b, pcts):
        n = int(round(pct / 100 * len(ethics)))
        axes[0].text(bar.get_width() + 0.3,
                     bar.get_y() + bar.get_height() / 2,
                     f"{pct:.1f}%  (n={n})", va='center', fontsize=8)
    axes[0].xaxis.set_major_formatter(mtick.PercentFormatter())
    axes[0].set_xlim(0, max(pcts) + 18)
    style_ax(axes[0], 'AI Ethics & Transparency\nSignal Prevalence (all devices)',
             '% of Devices Mentioning Concept', '')

    # --- Right: XAI named methods breakdown ---
    xai_methods = {
        'SHAP':              'has_shap',
        'LIME':              'has_lime',
        'Saliency Maps':     'has_saliency',
        'Probability Maps':  'has_probability_map',
        'Tornado Plots':     'has_tornado_plot',
        'Grad-CAM':          'has_grad_cam',
    }
    xai_labels = list(xai_methods.keys())
    xai_pcts   = [100 * ethics[col].mean() for col in xai_methods.values()]
    xai_ns     = [int(ethics[col].sum()) for col in xai_methods.values()]

    b2 = axes[1].barh(xai_labels, xai_pcts, color='#8e44ad')
    for bar, pct, n in zip(b2, xai_pcts, xai_ns):
        axes[1].text(bar.get_width() + 0.05,
                     bar.get_y() + bar.get_height() / 2,
                     f"{pct:.2f}%  (n={n})", va='center', fontsize=8)
    axes[1].xaxis.set_major_formatter(mtick.PercentFormatter())
    axes[1].set_xlim(0, max(xai_pcts or [1]) + 3)
    style_ax(axes[1], 'Named XAI Methods Mentioned\nin Submissions',
             '% of Devices', '')

    fig.suptitle('AI Ethics, Fairness & Explainability Language in FDA Submissions',
                 fontsize=14, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_09_ethics_prevalence.png')


# ---------------------------------------------------------------------------
# Figure 10 – Ethics signals by panel and over time
# ---------------------------------------------------------------------------

def fig_ethics_by_panel_and_time(conn: sqlite3.Connection) -> None:
    try:
        ethics = pd.read_sql(
            '''SELECT ae.has_fairness_bias, ae.has_privacy, ae.has_xai_general,
                      ae.has_xai_method, ae.has_data_provenance, ae.has_ethics_general,
                      ae.total_signal_count,
                      c.panel, c.decision_year
               FROM ai_ethics_signals ae
               JOIN classifications c ON ae.k_number = c.k_number
               WHERE ae.text_source != 'unavailable'
                 AND c.decision_year IS NOT NULL''',
            conn,
        )
    except Exception:
        print("  fig_10: ai_ethics_signals table not found — skipping.")
        return

    if ethics.empty:
        return

    signal_cols = ['has_fairness_bias', 'has_privacy', 'has_xai_general',
                   'has_xai_method', 'has_data_provenance', 'has_ethics_general']
    short_labels = ['Fairness/Bias', 'Privacy', 'XAI General',
                    'XAI Methods', 'Data Provenance', 'Ethics']

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # --- Left: heatmap — panel × concept ---
    top_panels = (
        ethics.groupby('panel').size()
        .nlargest(8).index.tolist()
    )
    heat_data = (
        ethics[ethics['panel'].isin(top_panels)]
        .groupby('panel')[signal_cols]
        .mean() * 100
    )
    heat_data.columns = short_labels
    heat_data = heat_data.loc[heat_data.sum(axis=1).sort_values(ascending=False).index]

    im = axes[0].imshow(heat_data.values, aspect='auto', cmap='YlOrRd', vmin=0, vmax=60)
    axes[0].set_xticks(range(len(short_labels)))
    axes[0].set_xticklabels(short_labels, rotation=30, ha='right', fontsize=9)
    axes[0].set_yticks(range(len(heat_data)))
    axes[0].set_yticklabels(heat_data.index, fontsize=9)
    for r in range(len(heat_data)):
        for c_idx in range(len(short_labels)):
            val = heat_data.values[r, c_idx]
            axes[0].text(c_idx, r, f'{val:.0f}%', ha='center', va='center',
                         fontsize=8, color='black' if val < 40 else 'white')
    plt.colorbar(im, ax=axes[0], label='% of Devices Mentioning Concept')
    style_ax(axes[0], '% Devices Mentioning Each Concept\nby Medical Specialty', '', '')

    # --- Right: temporal trend (any ethics signal) ---
    yearly = (
        (ethics[ethics['decision_year'] >= 2016]
         .groupby('decision_year')[signal_cols]
         .mean() * 100)
        .reset_index()
    )
    colors_line = ['#e74c3c', '#3498db', '#2ecc71', '#9b59b6', '#f39c12', '#1abc9c']
    for col, label, color in zip(signal_cols, short_labels, colors_line):
        axes[1].plot(yearly['decision_year'], yearly[col],
                     marker='o', label=label, color=color, linewidth=1.8)
    axes[1].yaxis.set_major_formatter(mtick.PercentFormatter())
    axes[1].legend(fontsize=8, loc='upper left')
    axes[1].set_ylim(0, None)
    style_ax(axes[1], 'Ethics & Transparency Language\nOver Time (2016–present)',
             'Year', '% of Devices')

    fig.suptitle('Where and When Ethics Language Appears in FDA AI Submissions',
                 fontsize=14, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_10_ethics_by_panel_time.png')


# ---------------------------------------------------------------------------
# Figure 11 – Fairness depth: corrected rate + tier breakdown + demographics
# ---------------------------------------------------------------------------

def fig_fairness_depth(conn: sqlite3.Connection) -> None:
    try:
        fd = pd.read_sql(
            '''SELECT fd.*, c.panel, c.decision_year
               FROM fairness_depth fd
               JOIN classifications c ON fd.k_number = c.k_number''',
            conn,
        )
    except Exception:
        print("  fig_11: fairness_depth table not found — skipping.")
        return

    if fd.empty:
        print("  fig_11: fairness_depth table is empty — skipping.")
        return

    total = len(fd)
    algo_n = fd['has_algorithmic_fairness'].sum()

    fig, axes = plt.subplots(2, 2, figsize=(16, 11))

    # --- Top-left: naive vs corrected prevalence ---
    ax = axes[0, 0]
    naive_pct   = 100 * fd['has_statistical_bias'].sum() / total   # original inflated
    # Original naive = statistical + algorithmic (what the broad \bbias\b caught)
    naive_total = int((fd['has_algorithmic_fairness'] | fd['has_statistical_bias']).sum())
    naive_pct   = 100 * naive_total / total
    algo_pct    = 100 * algo_n / total

    bars = ax.bar(
        ['Naive keyword\n(any "bias" match)', 'True algorithmic\nfairness language'],
        [naive_pct, algo_pct],
        color=['#e74c3c', '#2ecc71'], width=0.5,
    )
    for bar, pct, n in zip(bars, [naive_pct, algo_pct], [naive_total, algo_n]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f'{pct:.1f}%\n(n={n})', ha='center', va='bottom', fontsize=11, fontweight='bold')
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_ylim(0, naive_pct * 1.4)
    style_ax(ax, 'Naive vs Actual Fairness Prevalence',
             '', '% of All Devices (n=1,430)')
    ax.annotate('~10× inflation\nfrom measurement-science\nuses of "bias"',
                xy=(1, algo_pct), xytext=(1.35, (naive_pct + algo_pct) / 2),
                fontsize=8, color='gray',
                arrowprops=dict(arrowstyle='->', color='gray', lw=1))

    # --- Top-right: depth tier breakdown (horizontal bar) ---
    ax = axes[0, 1]
    tier_order  = ['quantified_subgroup', 'tested_subgroup', 'acknowledged',
                   'statistical_only', 'absent']
    tier_labels = ['Quantified subgroup\n(reports metrics by demo)',
                   'Tested subgroup\n(claims testing, no numbers)',
                   'Acknowledged\n(concern only, no evidence)',
                   'Statistical bias only\n(measurement/epidemiology)',
                   'Absent']
    tier_colors = ['#1a9641', '#74c476', '#bae4b3', '#e67e22', '#d5d8dc']
    tier_counts = [fd[fd['fairness_depth'] == t].shape[0] for t in tier_order]
    tier_pcts   = [100 * n / total for n in tier_counts]

    bars = ax.barh(tier_labels[::-1], tier_pcts[::-1], color=tier_colors[::-1])
    for bar, pct, n in zip(bars, tier_pcts[::-1], tier_counts[::-1]):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f'{pct:.1f}%  (n={n})', va='center', fontsize=8)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, 100)
    style_ax(ax, 'Fairness Language Depth Tier',
             '% of All Devices', '')

    # --- Bottom-left: demographics among algorithmic-fairness devices ---
    ax = axes[1, 0]
    demo_cols   = ['mentions_race', 'mentions_sex', 'mentions_age',
                   'mentions_skin_tone', 'mentions_geography', 'mentions_socioeconomic']
    demo_labels = ['Race / Ethnicity', 'Sex / Gender', 'Age Group',
                   'Skin Tone', 'Geography', 'Socioeconomic']
    algo_fd = fd[fd['has_algorithmic_fairness'] == 1]
    demo_pcts = [100 * algo_fd[col].sum() / len(algo_fd) if len(algo_fd) else 0
                 for col in demo_cols]
    demo_ns   = [int(algo_fd[col].sum()) for col in demo_cols]

    order = sorted(range(len(demo_pcts)), key=lambda i: demo_pcts[i])
    sorted_labels = [demo_labels[i] for i in order]
    sorted_pcts   = [demo_pcts[i]   for i in order]
    sorted_ns     = [demo_ns[i]     for i in order]

    bars = ax.barh(sorted_labels, sorted_pcts, color='#8e44ad')
    for bar, pct, n in zip(bars, sorted_pcts, sorted_ns):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                f'{pct:.0f}%  (n={n})', va='center', fontsize=8)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, 85)
    style_ax(ax,
             f'Demographics Mentioned\n(among {len(algo_fd)} algorithmic-fairness devices)',
             '% of Devices', '')

    # --- Bottom-right: algorithmic fairness rate over time ---
    ax = axes[1, 1]
    yearly = (
        fd[fd['decision_year'] >= 2016]
        .groupby('decision_year')
        .agg(
            total=('k_number', 'count'),
            algo=('has_algorithmic_fairness', 'sum'),
            stat=('has_statistical_bias', 'sum'),
        )
        .assign(
            pct_algo=lambda d: 100 * d['algo'] / d['total'],
            pct_stat=lambda d: 100 * d['stat'] / d['total'],
        )
        .reset_index()
    )

    ax.plot(yearly['decision_year'], yearly['pct_algo'],
            marker='o', color='#2ecc71', linewidth=2, label='Algorithmic fairness')
    ax.fill_between(yearly['decision_year'], yearly['pct_algo'],
                    alpha=0.15, color='#2ecc71')
    ax.plot(yearly['decision_year'], yearly['pct_stat'],
            marker='s', color='#e74c3c', linewidth=2, linestyle='--',
            label='Statistical bias only (false positive)')
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_ylim(0, None)
    ax.legend(fontsize=8, loc='upper left')
    style_ax(ax, 'Algorithmic Fairness Over Time',
             'Year', '% of Devices')

    fig.suptitle('AI Fairness in FDA Submissions',
                 fontsize=15, fontweight='bold', y=1.01)
    fig.tight_layout()
    save(fig, 'fig_11_fairness_depth.png')


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def print_text_summary(df: pd.DataFrame, metrics: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("ANALYSIS SUMMARY")
    print("=" * 60)

    print(f"\nTotal classified devices: {len(df)}")
    print(df['category'].value_counts().to_string())

    print("\n--- Temporal trend (% Category A) ---")
    yr = df.groupby('decision_year').apply(
        lambda x: round(100 * (x['category'] == 'A').mean(), 1)
    )
    print(yr.to_string())

    print("\n--- Pathway breakdown ---")
    path = df.groupby('submission_type').agg(
        total=('k_number', 'count'),
        pct_A=('category', lambda x: round(100 * (x == 'A').mean(), 1))
    )
    print(path.to_string())

    print("\n--- Top 5 most transparent panels ---")
    top5 = (
        df.groupby('panel')
        .apply(lambda x: round(100 * (x['category'] == 'A').mean(), 1))
        .sort_values(ascending=False)
        .head(5)
    )
    print(top5.to_string())

    print("\n--- Avg metric values (Category A, after normalisation) ---")
    agg = (
        metrics.groupby('metric_type')['metric_value']
        .agg(['mean', 'median', 'count'])
        .round(2)
        .sort_values('count', ascending=False)
    )
    print(agg.to_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)

    conn    = sqlite3.connect(DB_PATH)
    df      = load_classifications(conn)
    metrics = load_metrics(conn)

    print("Generating figures...")
    fig_temporal(df)
    fig_panel(df)
    fig_pathway(df)
    fig_metric_distributions(metrics)
    fig_auc(metrics)
    fig_category_c_radiology(df)
    fig_company_transparency(df)
    fig_ci_reporting(conn, df)
    fig_ethics_prevalence(conn, df)
    fig_ethics_by_panel_and_time(conn)
    fig_fairness_depth(conn)

    conn.close()

    print_text_summary(df, metrics)
    print(f"\nAll outputs written to {REPORT_DIR}/ and data/")


if __name__ == '__main__':
    main()
