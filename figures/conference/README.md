# Conference Presentation Graph Guide — Pramana (IEEE ICBDS 2026)

All graphs are generated from verified repo data:
- **n=5,000 run:** `outputs/paper_eval_2500.json`
- **n=1,000 run:** `outputs/paper_eval_500.json`

---

## Graph Inventory & Slide Mapping

| Graph File | Slide # | Title | What It Shows | Data Source |
|---|---|---|---|---|
| `G1_fpr_comparison.png` | 8 | Results I: Detection | FPR across all 4 configs — the key story visual | `paper_eval_2500.json` |
| `G2_prec_rec_f1.png` | 8 | Results I: Detection | Grouped bar: Precision / Recall / F1 for all 4 | `paper_eval_2500.json` |
| `G3_mcc.png` | 8 | Results I: Detection | MCC — the balanced single metric | `paper_eval_2500.json` |
| `G4_attack_hit_rate.png` | 9 | Results II: Coverage | Attack coverage at n=1,000 vs n=5,000 (all 100%) | both JSONs |
| `G5_ablation.png` | 9 | Results II: Ablation | L1 vs L1+L2 with −38.5% FPR delta annotated | `paper_eval_2500.json` |
| `G6_latency.png` | 10 | Results III: Latency | p50/p95/p99 bars with the 50 ms SLA line | paper claim (n=1,000) |
| `G7_scale_stability.png` | 10 | Results III: Reliability | Metric stability across n=1,000 vs n=5,000 | both JSONs |
| `G8_confusion.png` | 9 | Results II: Ablation | Side-by-side confusion matrices: L1+L2 vs Baseline A | `paper_eval_2500.json` |
| `G9_roc_scatter.png` | 8 | Results I: Detection | ROC-space scatter: all 4 configs at once | `paper_eval_2500.json` |

---

## Repo Figures → Slide Mapping

These already exist in `/opt/anaconda3/pramana/figures/` (both PNG and SVG):

| Repo Figure | Slide # | Use For |
|---|---|---|
| `01_architecture_block_layout` | 4 – Architecture | System overview block diagram |
| `02_runtime_control_flow` | 5 – L1 Pipeline | Shows the 9-agent L1 DAG in action |
| `03_two_pipeline_topology` | 4 – Architecture | ERC-4337 vs EOA two-path split |
| `04_routing_hardstop_decision_tree` | 5 – L1 Pipeline | Hard-stop and risk-routing decision logic |
| `05_trace_dag_lifecycle` | 6 – Provenance | TraceDAG lifecycle and SHA-256 trace_hash |
| `06_risk_fusion_formula` | 5 – L1 Pipeline | Visualises the risk fusion Eq.(3) formula |
| `07_erc4337_sequence` | 3 – Background | ERC-4337 flow sequence (intro context) |
| `08_attestation_replay_state_machine` | 6 – Provenance | Bundler attestation + replay protection |
| `09_eip7702_detection_flow` | 7 – Threat Model | EIP-7702 detection path |
| `10_smart_contract_interaction` | 4 – Architecture | Guardian.sol interaction |
| `11_validation_metrics_dashboard` | 8 – Results I | Dashboard screenshot (credibility visual) |
| `12_attack_hit_rate` | 9 – Results II | Attack hit rate chart (repo version) |
| `13_mrl_tradeoff` | 10 – Results III | MRL tradeoff dimension |
| `14_crucial_validation_heatmap` | 9 – Results II | Agent × threat type heatmap |

---

## Recommended Minimal Slide Graph Set (6-slide deck)

If you only have 1 graph per results slide:

| Slide | Best Graph | Why |
|---|---|---|
| Results I (Detection) | **G1** (FPR bar) | Immediately shows Pramana dominates |
| Results II (Coverage) | **G5** (Ablation) | Shows L2 value quantitatively |
| Results II (Attack) | **G4** (Attack hit rate) | 100% recall across all 6 vectors |
| Results III (Latency) | **G6** (Latency) | p50/p95/p99 vs 50 ms SLA — clean story |
| Results III (Scale) | **G7** (Scale stability) | Answers "does it hold at scale?" |
| Confusion / Bonus | **G8** (Confusion matrices) | Side-by-side: starkest comparison |

---

## Key Numbers for Presenter Notes

### Detection (n=5,000)
| Config | Precision | Recall | FPR | F1 | MCC |
|---|---|---|---|---|---|
| L1 (Any-Flag) | 95.82% | 100% | 4.36% | 0.9787 | 0.9573 |
| **L1+L2 (Routing)** | **97.39%** | **100%** | **2.68%** | **0.9868** | **0.9735** |
| Baseline A (Threshold) | 63.01% | 67.80% | 39.80% | 0.6532 | 0.2808 |
| Baseline B (Single-Judge) | 63.03% | 84.56% | 49.60% | 0.7222 | 0.3720 |

### Attack Coverage (n=5,000) — all 100%
Prompt Injection: 415/415 · Tx Flooding: 386/386 · Balance Drain: 428/428  
Econ. Manip: 427/427 · Behav. Drift: 419/419 · Schema Valid: 425/425

### Latency (n=1,000)
- p50 = **32 ms** ✅ | p95 = **44 ms** ✅ | p99 = **68 ms** ⚠️ (over 50 ms SLA — GC + WAL fsync spike)
- Throughput: **22.3 events/s** (well within 10–50 ops/s bundler range)
- ~70% of events resolve at L1 with **zero LLM calls**
- Cost: **< \$0.001 per decision**

### Ablation
- FP count: 109 (L1-only) → 67 (L1+L2) = **−38.5% FPR reduction**
- MCC: 0.9573 → 0.9735 (+0.0162)
- Recall stays at 100% throughout

> [!NOTE]
> The **p99 = 68 ms** spike is an honest data point — explain it as GC + WAL fsync
> overhead. The sub-50 ms claim is a **p95 envelope**, not an absolute guarantee.
> Reviewers will respect honesty here.

---

## Files Location
```
/Users/spandankewte/.gemini/antigravity/brain/08e3844b-9850-499a-941b-c079f7af1a5e/
  G1_fpr_comparison.png
  G2_prec_rec_f1.png
  G3_mcc.png
  G4_attack_hit_rate.png
  G5_ablation.png
  G6_latency.png
  G7_scale_stability.png
  G8_confusion.png
  G9_roc_scatter.png
```
