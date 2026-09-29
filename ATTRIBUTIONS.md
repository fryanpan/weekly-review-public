# Attributions

This file tracks every external project, idea, rubric, or dataset we build on. Two separate categories:

- **Code / pattern borrowing** — subject to the source license. We must preserve the license notice, attribute the source, and only borrow in ways the license allows. Check the license *before* copying code.
- **Idea / framing citation** — not a license issue; we cite so readers can follow the reasoning back to its source.

## Process

1. **Before borrowing code**, check the source project's LICENSE file and record it in the table below. If the license is incompatible with our intended use (now: private repo; possibly later: MIT-licensed public release), don't borrow — reimplement from the description instead.
2. **When borrowing code**, add a header comment in the using file: `# Adapted from <project> (<license>, <url>) — see ATTRIBUTIONS.md`. Include the source project's copyright notice where the license requires it (MIT requires preserving the notice in "copies or substantial portions").
3. **When borrowing an idea or framing**, cite the source in the spec or module docstring. No code-header comment needed.
4. **When in doubt**, reimplement from a description of the approach rather than copy. The cost is small; the license risk isn't.

## License compatibility quick reference

| Their license | Our private use | Our future MIT release |
|---|---|---|
| MIT, BSD, Apache-2.0, ISC | ✅ | ✅ with notice preservation |
| MPL-2.0 | ✅ | ✅ for un-modified files only; modified files stay MPL |
| LGPL | ✅ | ⚠️ only if we dynamically link / don't statically bundle |
| GPL / AGPL | ✅ | ❌ would force our whole project to GPL/AGPL |
| No license / NOASSERTION | ⚠️ verify | ❌ assume all rights reserved until confirmed |
| Custom / non-commercial | verify case-by-case | verify case-by-case |

## Tracked items

### Code / pattern borrowing

| Source | License | Verified | Use | Status |
|---|---|---|---|---|
| [ccusage](https://github.com/ryoppippi/ccusage) | MIT (Copyright (c) 2025 ryoppippi) | 2026-04-19 | Transcript JSONL parsing patterns for `~/.claude/projects/*.jsonl` | Planned — borrow parsing approach, cite at borrow point |
| [CodeBurn](https://github.com/AgentSeal/codeburn) | MIT | 2026-04-19 | Deterministic tool-use-pattern → task-category classification as a first-pass segmentation pre-filter | Planned — read source, adapt categories to our domain, cite at borrow point |
| [analyze_transcript.py](../../ai-project-support/.claude/skills/retro/scripts/analyze_transcript.py) | Internal (the user's own code from ai-project-support) | 2026-04-19 | Transcript extraction and interactive-session detection logic | Planned — reuse directly or port into this repo |
| [work-review.ts](../../ai-project-support/docs/process/work-review/work-review.ts) | Internal (the user's own, the v0 of this project) | 2026-04-19 | Weekly aggregation, project-touch detection, context-switch counts | Planned — port the approach into Python |

### Idea / framing citation (no code borrowed, cite only)

| Source | Concept we cite | Where we use it |
|---|---|---|
| [METR — Measuring AI Ability to Complete Long Tasks (arXiv:2503.14499)](https://arxiv.org/abs/2503.14499) | "Human-expert completion time at which the agent succeeds with 50% reliability" as the output unit | Output estimation rubric — our numerator borrows this framing (spec §8) |
| [METR — Time Horizon 1.1 "Clarifying Limitations" (Jan 2026)](https://metr.org/blog/2026-1-29-time-horizon-1-1/) | Time-horizon methodology measures well-defined technical/reasoning tasks, not messy real-world work | Softens our calibration-target language; spec §8 explicitly accepts the limitation |
| [METR — Changing Our Developer Productivity Experiment Design (Feb 2026)](https://metr.org/blog/2026-02-24-uplift-update/) | Developer productivity measurement is methodologically harder than first framed; adjustments needed | Acknowledged in spec §8 as reason for biweekly recalibration + explicit re-calibratability |
| [METR — Many SWE-bench-Passing PRs Would Not Be Merged (Mar 2026)](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/) | Benchmark velocity ≠ production velocity; task-completion tests overstate real impact | Cited in spec §8 as a reason the LLM estimator should prefer anchors over generic benchmark framing |
| [METR — 19% slowdown RCT (arXiv:2507.09089)](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) | Self-report of AI productivity is systematically wrong; objective measurement matters | Motivation section and anti-patterns |
| [Reock — "AI productivity gains are 10%, not 10x" (DX Mar 2026)](https://newsletter.getdx.com/p/ai-productivity-gains-are-10-not) | 400-company, 15-month longitudinal, 9.97% PR throughput gain — macro answer to "how much more does the team produce." Our leverage ratio is a different thing (output per active hour), not throughput. | Spec §8 "What our leverage ratio is *not*" — the right macro sanity anchor + explicit caveat that high leverage at N=1 ≠ team throughput gain |
| [DX — AI Measurement Framework](https://getdx.com/research/measuring-ai-code-assistants-and-agents/) | utilization × impact × cost; "human-equivalent hours" for autonomous agents | Metric taxonomy; we name things consistently where we can |
| [Toby Ord — Are the Costs of AI Agents Rising Exponentially?](https://www.tobyord.com/writing/hourly-costs-for-ai-agents) | Agent-hourly-rate = cost / human-equivalent-hours (our leverage ratio, inverted) | Secondary metric framing |
| [SPACE framework — Forsgren, Storey, Maddila, Zimmermann et al. 2021, ACM Queue](https://queue.acm.org/detail.cfm?id=3454124) | No single metric captures productivity; combine dimensions; individual-level metrics are dangerous at org scale | "What's different" section and anti-patterns |
| [Parnin & Rugaber 2011, Software Quality Journal](https://link.springer.com/article/10.1007/s11219-010-9104-9) | Empirical data on resumption cost after interruption; ~10% resume in <1 min | Context-switch tax calibration |
| [Mark, Gudith, Klocke 2008, CHI](https://ics.uci.edu/~gmark/chi08-mark.pdf) | ~23 min of other work before returning to interrupted task; interruption costs stress not time | Context-switch tax calibration |
| [Monsell 2003, Trends in Cognitive Sciences](https://www.cell.com/trends/cognitive-sciences/abstract/S1364-6613(03)00028-7) | Switch costs survive preparation; scale with task-set dissimilarity | Why cross-project switches cost more than within-project |
| [Jørgensen 2004 — review of expert vs. model estimation](https://www.simula.no/publications/review-studies-expert-estimation-software-development-effort) | Expert judgment equals or beats formal models for effort estimation when experts are domain-familiar | Rubric design: prime LLM with user's own history, not a generic formula |
| [GitClear — 2025 AI Code Quality Research](https://www.gitclear.com/ai_assistant_code_quality_2025_research) | Churn rate rose 3.1% → 5.7% as AI adoption grew; single output metrics hide rewrites | Rework counter-signal alongside leverage |
| [Meyer et al. 2014, FSE; 2021, IEEE TSE](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/meyer-fse-2014.pdf) | Completing a meaningful task without interruption correlates with "productive day" better than output volume | Framing of what the metric is *for* |
| [Personal Informatics — Ian Li](https://personalinformatics.org/) | Quantified-self research on gaming, motivation erosion, tracking burnout | Anti-patterns section |
| [Cal Newport — Deep Work hour tally](https://calnewport.com) | Single strong input signal (deep hours) beats many noisy signals | V0 defaults |
| [Good Judgment Project / Superforecasting — Tetlock](https://www.goodjudgment.com/) | Calibration-over-time feedback loop for individual judgment | Review tool design philosophy; closest published analogue for our N=1 gaming-resistance argument (spec §14) |
| [notes-repo: `topics/measuring-ai-agent-impact.md`](../../notes-repo/topics/measuring-ai-agent-impact.md) | Peer-synthesized external-research compendium: DX publication timeline, DX vs METR comparison, methodology critique, independent-reproduction analysis | Cross-reference — that file cites this repo for the user's own measurement stance; both remain authoritative on their respective scopes |

## Auditing

Before every release (public or private), run a pass over every file that imports or includes external code and verify:

1. The source is listed in the "Code / pattern borrowing" table above.
2. The header comment matches the source and license.
3. The license file requirements (notice preservation, etc.) are met in the repo or output artifacts.

If you find code that isn't tracked, either add it to the table or remove it.
