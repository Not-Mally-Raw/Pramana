# Figure Pack for Pramana Paper & Conference

This folder contains the complete visualization suite for the Pramana paper (IEEE ICBDS 2026) and conference presentation.

## Core Architectural & Pipeline Diagrams (`figures/`)

1. `01_architecture_block_layout.png` / `.svg` — Overall system architecture
2. `02_runtime_control_flow.png` / `.svg` — L1 DAG runtime control flow
3. `03_two_pipeline_topology.png` / `.svg` — Fast-path ERC-4337 vs EOA topology
4. `04_routing_hardstop_decision_tree.png` / `.svg` — Risk routing and hard-stop logic
5. `05_trace_dag_lifecycle.png` / `.svg` — TraceDAG lifecycle & cryptographic provenance
6. `06_risk_fusion_formula.png` / `.svg` — Geometric mean risk fusion formula
7. `07_erc4337_sequence.png` / `.svg` — End-to-end ERC-4337 sequence diagram
8. `08_attestation_replay_state_machine.png` / `.svg` — Replay protection & attestation state machine
9. `09_eip7702_detection_flow.png` / `.svg` — EIP-7702 batch delegation attack detection
10. `10_smart_contract_interaction.png` / `.svg` — Guardian.sol on-chain interaction
11. `11_validation_metrics_dashboard.png` / `.svg` — Live validation metrics dashboard
12. `12_attack_type_hit_rate.png` / `.svg` — Attack type hit rate overview
13. `13_mrl_tradeoff.png` / `.svg` — Matryoshka Representation Learning dimension tradeoff
14. `14_crucial_validation_heatmap.png` / `.svg` — Agent × threat vector validation matrix

## Conference Presentation Visuals (`figures/conference/`)

High-resolution presentation graphs generated directly from benchmark evaluation datasets (`outputs/paper_eval_2500.json` and `outputs/paper_eval_500.json`):

- `G1_fpr_comparison.png` — False positive rate comparison across all 4 configurations (n=5,000)
- `G2_prec_rec_f1.png` — Grouped Precision, Recall, and F1 metrics across configurations
- `G3_mcc.png` — Matthews Correlation Coefficient (MCC) comparison
- `G4_attack_hit_rate.png` — 100% detection rate across all 6 attack vectors at n=1,000 and n=5,000
- `G5_ablation.png` — Ablation study: L1-only vs L1+L2 routing gate (−38.5% FPR reduction)
- `G6_latency.png` — L1 pipeline latency distribution (p50=32ms, p95=44ms, p99=68ms vs 50ms SLA)
- `G7_scale_stability.png` — Scalability and metric stability across evaluation scales
- `G8_confusion.png` — Confusion matrices (Pramana L1+L2 vs Baseline A)
- `G9_roc_scatter.png` — ROC-space scatter plot for all configurations

Reproduce with:
```bash
python scripts/generate_conference_graphs.py
```
