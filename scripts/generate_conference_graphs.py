"""
Conference Presentation Graph Generator for Pramana (IEEE ICBDS 2026)
Generates high-resolution publication & presentation visuals for Pramana.
All baseline numbers sourced directly from outputs/paper_eval_2500.json and outputs/paper_eval_500.json.
"""

from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "figures" / "conference"
OUT.mkdir(parents=True, exist_ok=True)

# ── Verified data from outputs/paper_eval_2500.json ───────────────────────────
# n=5000 (2500 benign + 2500 adversarial)
DATA = {
    "L1 (Any-Flag)":       dict(prec=95.82, rec=100.0, fpr=4.36,  f1=0.9787, mcc=0.9573),
    "L1+L2 (Routing)":     dict(prec=97.39, rec=100.0, fpr=2.68,  f1=0.9868, mcc=0.9735),
    "Baseline A\n(Threshold)": dict(prec=63.01, rec=67.80, fpr=39.80, f1=0.6532, mcc=0.2808),
    "Baseline B\n(Single-Judge)": dict(prec=63.03, rec=84.56, fpr=49.60, f1=0.7222, mcc=0.3720),
}

ATTACK_N1000 = {  # paper_eval_500.json (n=1000)
    "Prompt Injection": (80, 80),
    "Tx Flooding":      (69, 69),
    "Balance Drain":    (86, 86),
    "Econ. Manip.":     (85, 85),
    "Behav. Drift":     (95, 95),
    "Schema Valid.":    (85, 85),
}
ATTACK_N5000 = {  # paper_eval_2500.json (n=5000)
    "Prompt Injection": (415, 415),
    "Tx Flooding":      (386, 386),
    "Balance Drain":    (428, 428),
    "Econ. Manip.":     (427, 427),
    "Behav. Drift":     (419, 419),
    "Schema Valid.":    (425, 425),
}

PALETTE = {
    "pramana_l1":  "#2563EB",   # blue
    "pramana_l2":  "#16A34A",   # green
    "baseline_a":  "#DC2626",   # red
    "baseline_b":  "#EA580C",   # orange
    "accent":      "#7C3AED",   # purple
    "grey_light":  "#E5E7EB",
    "grey_dark":   "#374151",
}

FIGSIZE_WIDE  = (10, 5)
FIGSIZE_SQUARE = (7, 6)
DPI = 300

def save(fig, name):
    fig.savefig(OUT / name, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  Saved: {name}")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 1 — FPR Comparison (4 configs): key visual for Slide 8
# ─────────────────────────────────────────────────────────────────────────────
def g1_fpr_comparison():
    labels = list(DATA.keys())
    fprs   = [DATA[k]["fpr"] for k in labels]
    colors = [PALETTE["pramana_l1"], PALETTE["pramana_l2"],
              PALETTE["baseline_a"], PALETTE["baseline_b"]]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    bars = ax.bar(labels, fprs, color=colors, width=0.55, edgecolor="white", linewidth=1.5)

    for bar, val in zip(bars, fprs):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.8,
                f"{val:.2f}%", ha="center", va="bottom", fontsize=12, fontweight="bold",
                color=bar.get_facecolor())

    ax.axhline(5, color=PALETTE["pramana_l2"], linestyle="--", linewidth=1.5,
               label="5% FPR target")
    ax.set_ylabel("False Positive Rate (%)", fontsize=13)
    ax.set_title("False Positive Rate — Pramana vs. Baselines (n = 5,000)",
                 fontsize=14, fontweight="bold", pad=12)
    ax.set_ylim(0, 60)
    ax.legend(fontsize=11)
    ax.tick_params(axis="x", labelsize=11)
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G1_fpr_comparison.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 2 — Precision / Recall / F1 grouped bar (4 configs)
# ─────────────────────────────────────────────────────────────────────────────
def g2_prec_rec_f1():
    labels = list(DATA.keys())
    precs  = [DATA[k]["prec"]   for k in labels]
    recs   = [DATA[k]["rec"]    for k in labels]
    f1s    = [DATA[k]["f1"]*100 for k in labels]

    x = np.arange(len(labels))
    w = 0.25

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    b1 = ax.bar(x - w,   precs, w, label="Precision", color=PALETTE["pramana_l1"], alpha=0.9)
    b2 = ax.bar(x,        recs,  w, label="Recall",    color=PALETTE["pramana_l2"], alpha=0.9)
    b3 = ax.bar(x + w,    f1s,   w, label="F1 × 100",  color=PALETTE["accent"],     alpha=0.9)

    for bars in [b1, b2, b3]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.3,
                    f"{h:.1f}", ha="center", va="bottom", fontsize=8.5)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Score (%)", fontsize=12)
    ax.set_ylim(0, 115)
    ax.set_title("Precision, Recall & F1 — All Configurations (n = 5,000)",
                 fontsize=13, fontweight="bold", pad=10)
    ax.legend(fontsize=11)
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G2_prec_rec_f1.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 3 — MCC bar chart
# ─────────────────────────────────────────────────────────────────────────────
def g3_mcc():
    labels = list(DATA.keys())
    mccs   = [DATA[k]["mcc"] for k in labels]
    colors = [PALETTE["pramana_l1"], PALETTE["pramana_l2"],
              PALETTE["baseline_a"], PALETTE["baseline_b"]]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    bars = ax.bar(labels, mccs, color=colors, width=0.55, edgecolor="white", linewidth=1.5)
    for bar, val in zip(bars, mccs):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                f"{val:.4f}", ha="center", va="bottom", fontsize=12, fontweight="bold",
                color=bar.get_facecolor())

    ax.set_ylim(0, 1.08)
    ax.axhline(1.0, color="grey", linestyle=":", linewidth=1)
    ax.set_ylabel("Matthews Correlation Coefficient (MCC)", fontsize=12)
    ax.set_title("MCC — Balanced Classifier Performance (n = 5,000)",
                 fontsize=13, fontweight="bold", pad=10)
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G3_mcc.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 4 — Attack-type hit rate at both scales (n=1000 & n=5000)
# ─────────────────────────────────────────────────────────────────────────────
def g4_attack_hit_rate():
    attacks = list(ATTACK_N5000.keys())
    n1k = [ATTACK_N1000[a][0] for a in attacks]
    n5k = [ATTACK_N5000[a][0] for a in attacks]

    x = np.arange(len(attacks))
    w = 0.35

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    ax.bar(x - w/2, n1k, w, label="n=1,000", color=PALETTE["pramana_l1"], alpha=0.85)
    ax.bar(x + w/2, n5k, w, label="n=5,000", color=PALETTE["pramana_l2"], alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(attacks, fontsize=9, rotation=15)
    ax.set_ylabel("Attack Samples")
    ax.set_title("Sample Count per Attack Vector")
    ax.legend(fontsize=10)
    ax.set_facecolor("#F9FAFB")

    ax2 = axes[1]
    rates = [100] * len(attacks)
    bars = ax2.bar(attacks, rates, color=PALETTE["pramana_l2"], alpha=0.9, edgecolor="white")
    for bar, n in zip(bars, n5k):
        ax2.text(bar.get_x() + bar.get_width()/2, 50,
                 f"n={n}\n100%", ha="center", va="center",
                 fontsize=10, fontweight="bold", color="white")
    ax2.set_ylim(0, 120)
    ax2.set_ylabel("Detection Rate (%)")
    ax2.set_title("Detection Rate — All Attack Vectors (n = 5,000)")
    ax2.tick_params(axis="x", labelsize=9, rotation=15)
    ax2.set_facecolor("#F9FAFB")

    fig.suptitle("Per-Attack Coverage: Scale n=1,000 vs n=5,000", fontsize=14,
                 fontweight="bold", y=1.02)
    fig.tight_layout()
    save(fig, "G4_attack_hit_rate.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 5 — Ablation: L1 vs L1+L2
# ─────────────────────────────────────────────────────────────────────────────
def g5_ablation():
    configs = ["L1 Only", "L1 + L2\n(Routing)"]
    fprs   = [4.36,   2.68]
    precs  = [95.82,  97.39]
    mccs   = [0.9573, 0.9735]

    x = np.arange(2)
    w = 0.25

    fig, ax = plt.subplots(figsize=(8, 5))
    b1 = ax.bar(x - w,    fprs,         w, label="FPR (%)",     color=PALETTE["baseline_a"])
    b2 = ax.bar(x,         precs,        w, label="Precision (%)", color=PALETTE["pramana_l1"])
    b3 = ax.bar(x + w,    [m*100 for m in mccs], w, label="MCC × 100", color=PALETTE["accent"])

    for bars in [b1, b2, b3]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x()+bar.get_width()/2, h+0.4,
                    f"{h:.2f}", ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.annotate("", xy=(1-w, fprs[1]+2), xytext=(0-w, fprs[0]+2),
                arrowprops=dict(arrowstyle="->", color=PALETTE["pramana_l2"], lw=2))
    ax.text(0.5, max(fprs)+5, "−38.5% FPR", ha="center", color=PALETTE["pramana_l2"],
            fontsize=11, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(configs, fontsize=13)
    ax.set_ylim(0, 115)
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("Ablation Study: Impact of L2 Council (n = 5,000)",
                 fontsize=13, fontweight="bold", pad=10)
    ax.legend(fontsize=11)
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G5_ablation.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 6 — Latency distribution (p50 / p95 / p99 vs 50 ms SLA line)
# ─────────────────────────────────────────────────────────────────────────────
def g6_latency():
    labels  = ["p50", "p95", "p99"]
    values  = [32,     44,    68]
    colors  = [PALETTE["pramana_l2"], PALETTE["pramana_l1"], PALETTE["baseline_a"]]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, values, color=colors, width=0.45, edgecolor="white", linewidth=2)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1.5,
                f"{val} ms", ha="center", va="bottom", fontsize=13, fontweight="bold")

    ax.axhline(50, color="#DC2626", linestyle="--", linewidth=2.5,
               label="50 ms SLA target")
    ax.axhline(100, color="#9CA3AF", linestyle=":", linewidth=1.5,
               label="100 ms bundler limit")

    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("Pramana L1 Pipeline Latency (n = 1,000)",
                 fontsize=13, fontweight="bold", pad=10)
    ax.set_ylim(0, 120)
    ax.legend(fontsize=11)
    ax.set_facecolor("#F9FAFB")
    ax.text(2, 72, "p99 spike:\nGC + WAL fsync", ha="center", fontsize=9,
            color=PALETTE["baseline_a"], style="italic")
    fig.tight_layout()
    save(fig, "G6_latency.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 7 — Scalability: n=1000 vs n=5000 metric stability
# ─────────────────────────────────────────────────────────────────────────────
def g7_scale_stability():
    metrics = ["Precision (%)", "Recall (%)", "FPR (%)", "F1 × 100", "MCC × 100"]
    n1k = [97.66, 100.0, 1.2,  99.40, 97.35]
    n5k = [97.39, 100.0, 2.68, 98.68, 97.35]

    x = np.arange(len(metrics))
    w = 0.35

    fig, ax = plt.subplots(figsize=(11, 5))
    b1 = ax.bar(x - w/2, n1k, w, label="n = 1,000", color=PALETTE["pramana_l1"], alpha=0.9)
    b2 = ax.bar(x + w/2, n5k, w, label="n = 5,000", color=PALETTE["pramana_l2"], alpha=0.9)

    for bars in [b1, b2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x()+bar.get_width()/2, h+0.3,
                    f"{h:.2f}", ha="center", va="bottom", fontsize=8.5)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics, fontsize=11)
    ax.set_ylim(0, 115)
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("Metric Stability Across Evaluation Scale (L1+L2 Routing Gate)",
                 fontsize=13, fontweight="bold")
    ax.legend(fontsize=12)
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G7_scale_stability.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 8 — Confusion matrices 2×2
# ─────────────────────────────────────────────────────────────────────────────
def g8_confusion():
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))

    def draw_cm(ax, tp, tn, fp, fn, title, color):
        mat = np.array([[tn, fp], [fn, tp]])
        labs = [["TN", "FP"], ["FN", "TP"]]
        total = tp + tn + fp + fn
        for i in range(2):
            for j in range(2):
                val = mat[i, j]
                pct = val / total * 100
                alpha = 0.15 + 0.7*(val/total)
                ax.add_patch(mpatches.FancyBboxPatch(
                    (j+0.05, 1-i+0.05), 0.9, 0.9,
                    boxstyle="round,pad=0.05", linewidth=2,
                    edgecolor="white",
                    facecolor=color if labs[i][j] in ["TP","TN"] else "#FCA5A5",
                    alpha=alpha))
                ax.text(j+0.5, 1-i+0.55, f"{labs[i][j]}\n{val:,}",
                        ha="center", va="center", fontsize=13, fontweight="bold",
                        color="white" if labs[i][j] in ["TP","TN"] else "#7F1D1D")
                ax.text(j+0.5, 1-i+0.3, f"({pct:.1f}%)",
                        ha="center", va="center", fontsize=9, color="white"
                        if labs[i][j] in ["TP","TN"] else "#7F1D1D")
        ax.set_xlim(0, 2)
        ax.set_ylim(0, 2)
        ax.set_xticks([0.5, 1.5])
        ax.set_xticklabels(["Pred: Benign", "Pred: Malicious"], fontsize=10)
        ax.set_yticks([0.5, 1.5])
        ax.set_yticklabels(["Actual:\nMalicious", "Actual:\nBenign"], fontsize=10)
        ax.set_title(title, fontsize=12, fontweight="bold", pad=8)

    draw_cm(axes[0], 2500, 2433, 67, 0, "L1+L2 Routing Gate (n = 5,000)", PALETTE["pramana_l2"])
    draw_cm(axes[1], 1695, 1505, 995, 805, "Baseline A — Threshold Only (n = 5,000)", PALETTE["baseline_a"])

    fig.suptitle("Confusion Matrices: Pramana vs. Best Baseline", fontsize=14,
                 fontweight="bold", y=1.02)
    fig.tight_layout()
    save(fig, "G8_confusion.png")

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH 9 — ROC-style scatter
# ─────────────────────────────────────────────────────────────────────────────
def g9_roc_scatter():
    configs = {
        "L1 (Any-Flag)":      (4.36,  100.0, PALETTE["pramana_l1"], "o"),
        "L1+L2 (Routing)":    (2.68,  100.0, PALETTE["pramana_l2"], "D"),
        "Baseline A":         (39.80, 67.80, PALETTE["baseline_a"], "s"),
        "Baseline B":         (49.60, 84.56, PALETTE["baseline_b"], "^"),
    }

    fig, ax = plt.subplots(figsize=FIGSIZE_SQUARE)
    for label, (fpr, tpr, color, marker) in configs.items():
        ax.scatter(fpr, tpr, color=color, marker=marker, s=200, zorder=5, label=label)
        ax.annotate(label, (fpr, tpr), textcoords="offset points",
                    xytext=(8, -4), fontsize=9, color=color, fontweight="bold")

    ax.plot([0,100],[0,100], "k--", linewidth=1, alpha=0.4, label="Random classifier")
    ax.axvline(5, color="grey", linestyle=":", linewidth=1, alpha=0.5)

    ax.set_xlabel("False Positive Rate (%)", fontsize=12)
    ax.set_ylabel("True Positive Rate / Recall (%)", fontsize=12)
    ax.set_title("ROC-Space: All Configurations (n = 5,000)", fontsize=13,
                 fontweight="bold")
    ax.set_xlim(-2, 60)
    ax.set_ylim(60, 105)
    ax.legend(fontsize=10, loc="lower right")
    ax.set_facecolor("#F9FAFB")
    fig.tight_layout()
    save(fig, "G9_roc_scatter.png")

if __name__ == "__main__":
    print("Generating conference graphs to figures/conference/...")
    g1_fpr_comparison()
    g2_prec_rec_f1()
    g3_mcc()
    g4_attack_hit_rate()
    g5_ablation()
    g6_latency()
    g7_scale_stability()
    g8_confusion()
    g9_roc_scatter()
    print(f"\nDone. All graphs saved to:\n  {OUT}")
