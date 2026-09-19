<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=700&size=28&duration=2400&pause=650&color=BD93F9&center=true&vCenter=true&repeat=true&width=950&height=80&lines=AGENTX+%E2%80%94+Adaptive+Personal+Operating+Intelligence;GOAL+%E2%86%92+ACT+%E2%86%92+VERIFY+%E2%86%92+LEARN+%E2%86%92+REUSE+%E2%86%92+REPAIR;Persistent+Memory+%E2%80%A2+World+Model+%E2%80%A2+L0%E2%80%93L5+Strategy;Windows+%E2%80%A2+Browser+%E2%80%A2+Android+%E2%80%A2+Voice+%E2%80%A2+Proactivity;Built+to+accumulate+verified+operational+experience." alt="AgentX animated typing banner"/>

```text
 █████╗  ██████╗ ███████╗███╗   ██╗████████╗██╗  ██╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝╚██╗██╔╝
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║    ╚███╔╝
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║    ██╔██╗
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ██╔╝ ██╗
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝

       A D A P T I V E   O P E R A T I N G   I N T E L L I G E N C E
                     REMEMBER • ACT • VERIFY • LEARN
```

# AgentX

### Persistent. Governed. Adaptive.

**AgentX** is a Windows-first, local-first experimental **Adaptive Personal Operating Intelligence**: a governed agent runtime designed to remember verified experience, learn reusable procedures, detect degradation, repair broken skills, and safely operate across Windows, browsers, devices, voice, and proactive workflows.

> **The goal is not an agent that can act once. The goal is an agent that can remember what worked, prove that it worked, reuse it, and repair it when the world changes.**

[![Python](https://img.shields.io/badge/PYTHON-3.12%2B-8B5CF6?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/WINDOWS-FIRST-00A4EF?style=for-the-badge&logo=windows11&logoColor=white)](#)
[![Security](https://img.shields.io/badge/SECURITY-GOVERNED-50FA7B?style=for-the-badge)](#security-model)
[![Tasks](https://img.shields.io/badge/CANONICAL_TASKS-600-BD93F9?style=for-the-badge)](#project-status)
[![Verified](https://img.shields.io/badge/VERIFIED-586%2F600-50FA7B?style=for-the-badge)](#project-status)
[![Release](https://img.shields.io/badge/RELEASE-1.0.0rc1-F1FA8C?style=for-the-badge&logo=github&logoColor=black)](#release-state)

</div>

---

## ⚡ The AgentX Loop

```text
GOAL
  ↓
RETRIEVE MEMORY + WORLD STATE
  ↓
CHOOSE L0–L5 STRATEGY
  ↓
PLAN
  ↓
ACTIONGATE ── permissions • risk • budgets • policy
  ↓
EXECUTE ───── Windows • Browser • Android • Approved Extensions
  ↓
INDEPENDENTLY VERIFY
  ↓
RECORD VERIFIED EXPERIENCE
  ↓
LEARN / COMPILE PROCEDURE
  ↓
VALIDATE
  ↓
PROMOTE ACTIVE
  ↓
REUSE
  ↓
DETECT DEGRADATION
  ↓
DIAGNOSE → REPAIR → VALIDATE → REUSE
```

## 🧠 Why AgentX Exists

Most agents follow:

```text
USER → MODEL → TOOLS → RESULT
```

AgentX explores a longer-lived architecture:

```text
                       ┌─────────────────────┐
                       │    USER / EVENT     │
                       └──────────┬──────────┘
                                  ▼
┌──────────────────────────────────────────────────────────────┐
│                          AGENTX                              │
│                                                              │
│ HIVE ──▶ WORLD MODEL ──▶ L0–L5 STRATEGY ──▶ ACTIONGATE     │
│                                                   │          │
│                                                   ▼          │
│                                               EXECUTOR       │
│                            ┌──────────┬───────────┼────────┐ │
│                            ▼          ▼           ▼        ▼ │
│                         WINDOWS    BROWSER     ANDROID   EXT │
│                            └──────────┴───────────┴────────┘ │
│                                                   │          │
│                                                   ▼          │
│                                         INDEPENDENT VERIFY   │
│                                                   │          │
│                                                   ▼          │
│                                         VERIFIED EXPERIENCE  │
│                                             │          │     │
│                                             ▼          ▼     │
│                                        LEARN/REUSE   REPAIR  │
└──────────────────────────────────────────────────────────────┘
```

The research thesis is simple: **verified operational experience should compound**.

```text
SUCCESS → VERIFY → EXPERIENCE → CANDIDATE → VALIDATE → ACTIVE → REUSE
                                                     │
ENVIRONMENT CHANGES → FAILURE → DEGRADATION → REPAIR ┘
```

---

## 🧬 Intelligence & Memory

| Layer | Responsibility |
|:---:|---|
| **L0** | Direct deterministic execution |
| **L1** | Simple known actions |
| **L2** | Reusable learned procedures |
| **L3** | Composed procedures / workflows |
| **L4** | Exploratory planning and reasoning |
| **L5** | Governed capability acquisition / advanced adaptation |

AgentX separates operational state instead of treating the prompt as the whole system.

**Hive** stores persistent episodes, provenance, causal relationships, trust, scope, contradiction/supersession and preferences.

**World Model** tracks relevant environment state:

```text
OBSERVE → MODEL → COMPARE → DETECT CHANGE → ADAPT
```

**Procedure Memory** converts verified experience into candidate procedures that must be validated before becoming ACTIVE.

---

## 🔐 Security Model

> **Untrusted content is data, never authority.**

```text
┌────────────────────── UNTRUSTED ──────────────────────┐
│ model output • web pages • voice • Android UI         │
│ research • memory • world state • generated source    │
└─────────────────────────┬──────────────────────────────┘
                          │ DATA ONLY
                          ▼
                 ┌─────────────────────┐
                 │   TRUSTED KERNEL    │
                 │ ActionGate          │
                 │ permissions         │
                 │ risk + budgets      │
                 │ cancellation        │
                 │ verification        │
                 │ promotion           │
                 └──────────┬──────────┘
                            ▼
                    AUTHORIZED ACTION
```

Untrusted content cannot legitimately grant permission, lower risk, increase budgets, bypass ActionGate, disable emergency stop, forge verification, self-promote procedures/extensions, mutate the Trusted Kernel, or obtain generic arbitrary shell/Python execution.

---

## 🧩 Safe Self-Extension

```text
CAPABILITY GAP
      ↓
TYPED EXTENSION REQUEST
      ↓
UNTRUSTED CANDIDATE
      ↓
PROVENANCE + IMMUTABLE HASH
      ↓
STATIC / STRUCTURAL INSPECTION
      ↓
ISOLATED VALIDATION
      ↓
BEHAVIORAL + VARIED TESTING
      ↓
INDEPENDENT VERIFICATION
      ↓
AUTHENTICATED TRUSTED PROMOTION
      ↓
CANONICAL CAPABILITY REGISTRATION
      ↓
ACTIVE
```

Generated source remains candidate data until it passes the governed lifecycle.

---

## 🔁 Failure-Driven Repair

```text
FAILURE
  ├─▶ CLASSIFY
  ├─▶ LOCALIZE
  ├─▶ DIAGNOSE
  ├─▶ BUILD BOUNDED REPAIR CANDIDATE
  ├─▶ SHADOW / VARIED VALIDATION
  │       ├─ fail ─▶ REJECT
  │       └─ pass ─▶ PROMOTE REPLACEMENT
  ├─▶ VERIFY
  └─▶ PERSIST / RESTART / REUSE
```

A repair candidate does not silently mutate the ACTIVE procedure.

---

## 🌐 Multi-Surface Runtime

```text
                    ┌────────────┐
                    │   AGENTX   │
                    └─────┬──────┘
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          WINDOWS      BROWSER      ANDROID
             └────────────┼────────────┘
                          ▼
                  SHARED TASK STATE
                          ▼
                     VERIFICATION
```

Cross-device routing is designed to preserve task identity, context, permissions, budgets, deadlines, cancellation, provenance and verification boundaries.

---

## ⏱ Proactive Runtime + Voice/HUD

```text
EVENT → WATCHER → POLICY → SCHEDULE → AUTHORIZE
      → EXECUTE → VERIFY → PERSIST
```

The Voice/HUD layer is designed to expose runtime state such as:

```text
GOAL • STRATEGY • STEP • CAPABILITY • RISK • PERMISSION
COST • MEMORY • PROCEDURE • VERIFICATION • REPAIR • STATUS
```

Proactive execution never implies proactive authority escalation.

---

## 📊 Project Status

AgentX uses strict acceptance semantics. **Implemented is not automatically VERIFIED.**

| Status | Tasks | Share |
|---|---:|---:|
| 🟢 **VERIFIED** | **586** | **97.67%** |
| 🟠 **BLOCKED** | **14** | **2.33%** |
| 🔵 **IN_PROGRESS** | **0** | **0.00%** |
| ⚪ **NOT_AUDITED** | **0** | **0.00%** |
| 🔴 **NOT_IMPLEMENTED** | **0** | **0.00%** |
| **TOTAL** | **600** | **100%** |

**Zero tasks remain classified NOT_IMPLEMENTED.** The 14 blockers require specific external/physical evidence rather than being silently promoted from simulations.

### Milestones

| Milestone | Scope | Verified | Total | Acceptance |
|:---:|---|---:|---:|---:|
| M0 | Trusted foundation | 40 | 40 | **100%** |
| M1 | Governed single-task runtime | 43 | 45 | **95.56%** |
| M2 | Persistent memory / Hive | 40 | 40 | **100%** |
| M3 | Procedure compilation | 50 | 50 | **100%** |
| M4 | Learning-efficiency proof | 27 | 30 | **90.00%** |
| M5 | Failure-driven repair | 40 | 40 | **100%** |
| M6 | Windows capability fabric | 55 | 55 | **100%** |
| M7 | Browser capability layer | 35 | 35 | **100%** |
| M8 | Model / research runtime | 33 | 35 | **94.29%** |
| M9 | Untrusted-content defense | 35 | 35 | **100%** |
| M10 | World Model / adaptation | 30 | 30 | **100%** |
| M11 | Voice / HUD | 25 | 25 | **100%** |
| M12 | Scheduling / proactivity | 25 | 25 | **100%** |
| M13 | Multi-device / Android | 28 | 30 | **93.33%** |
| M14 | Adaptive optimization | 25 | 25 | **100%** |
| M15 | Safe self-extension | 30 | 30 | **100%** |
| M16 | Production / release | 25 | 30 | **83.33%** |
| | **TOTAL** | **586** | **600** | **97.67%** |

### Why not 600/600?

```text
SIMULATION ≠ CONTROLLED INTEGRATION ≠ HOSTED WINDOWS CI
           ≠ PHYSICAL WORKSTATION ACCEPTANCE
```

Remaining evidence classes include genuine Windows 10/11 workstation matrices, physical multi-DPI/multi-monitor acceptance, authorized real-Android E2E evidence, required real-provider L4/L5 evidence and final physical whole-system release acceptance.

---

## 🚀 Release State

```text
╔══════════════════════════════════════════════════════════════╗
║                    AGENTX 1.0.0rc1                         ║
╠══════════════════════════════════════════════════════════════╣
║ Canonical tasks .......................... 600              ║
║ Strictly VERIFIED ......................... 586              ║
║ External / real-env BLOCKED ............... 14               ║
║ NOT_IMPLEMENTED ........................... 0                ║
║ Strict acceptance ......................... 97.67%           ║
║ Release candidate ......................... BUILT            ║
║ Canonical main CI ......................... GREEN            ║
║ Final physical acceptance ................. PENDING          ║
╚══════════════════════════════════════════════════════════════╝
```

---

## ⚙️ Install / Develop

Requirements: **Python 3.12+**, Git, with Windows 10/11 as the primary target. Runtime code has no third-party dependencies; development tooling is installed through the `dev` extra.

```powershell
git clone https://github.com/harsh2025-sketch/AgentX.git
cd AgentX

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python -m agentx --version
agentx init --data-dir .\agentx-data
agentx doctor --data-dir .\agentx-data
```

Quality gates:

```powershell
python -m pytest
ruff check .
ruff format --check .
mypy src/agentx
.\scripts\check.ps1
```

---

## 📁 Repository Map

```text
AgentX/
├── src/agentx/
│   ├── core/             shared contracts and task state
│   ├── kernel/           permission, risk, budgets, ActionGate
│   ├── capabilities/     governed host/browser capabilities
│   ├── hive/             memory, knowledge and provenance
│   ├── procedures/       procedure lifecycle and runtime
│   ├── cognition/        reasoning, decomposition and routing
│   ├── learning/         causal extraction and compilation
│   └── infrastructure/   configuration, persistence and stores
├── tests/
├── docs/
├── scripts/
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## 📈 Live Repository Analytics

<div align="center">

<img width="49%" src="https://github-readme-stats.vercel.app/api?username=harsh2025-sketch&show_icons=true&hide_border=true&bg_color=0D1117&title_color=BD93F9&icon_color=FF79C6&text_color=F8F8F2&ring_color=8B5CF6" alt="GitHub analytics"/>
<img width="49%" src="https://github-readme-streak-stats.herokuapp.com/?user=harsh2025-sketch&hide_border=true&background=0D1117&stroke=8B5CF6&ring=FF79C6&fire=FFB86C&currStreakLabel=BD93F9&sideLabels=F8F8F2&currStreakNum=F8F8F2&sideNums=F8F8F2&dates=6272A4" alt="Developer streak"/>

<img width="46%" src="https://github-readme-stats.vercel.app/api/top-langs/?username=harsh2025-sketch&layout=compact&hide_border=true&bg_color=0D1117&title_color=BD93F9&text_color=F8F8F2" alt="Top languages"/>
<img width="50%" src="https://github-readme-stats.vercel.app/api/pin/?username=harsh2025-sketch&repo=AgentX&hide_border=true&bg_color=0D1117&title_color=BD93F9&icon_color=FF79C6&text_color=F8F8F2" alt="AgentX repository card"/>

</div>

---

## 🌀 How the Moving-Word Effect Works

GitHub README Markdown cannot run arbitrary JavaScript, so the animated hero uses a remotely rendered **SVG**. The typing endpoint constructs timed text states that create the visual illusion of a live terminal.

```text
FRAME 001   A|
FRAME 002   AG|
FRAME 003   AGE|
FRAME 004   AGEN|
FRAME 005   AGENT|
FRAME 006   AGENTX|
                │
             PAUSE
                │
FRAME 007   AGENT|
FRAME 008   AGEN|
FRAME 009   AGE|
FRAME 010   AG|
FRAME 011   A|
FRAME 012   |
                │
          NEXT HEADLINE
```

The animation is essentially:

```text
TEXT SEQUENCE
   + GLYPH-BY-GLYPH REVEAL
   + MICRO-DELAYS
   + HOLD / PAUSE WINDOW
   + FRAME-BY-FRAME CLEARING
   + SVG TIMELINE / KEYFRAME INTERPOLATION
   + REPEAT
   = APPARENT TERMINAL TYPING + BACKSPACING
```

Parameters such as `duration`, `pause`, `repeat`, font weight and alignment shape the timeline. The service returns an SVG containing timed animation states; GitHub embeds it as an image, and the browser continuously interpolates those SVG states. That produces motion without executing JavaScript inside the README.

---

## 📐 Evaluation Metrics

| Metric | Meaning |
|---|---|
| **ATCR** | Autonomous Task Completion Rate |
| **VSR** | Verification Success / Accuracy |
| **HIR** | Human Intervention Rate |
| **PRR** | Procedure Reuse Rate |
| **RSR** | Repair Success Rate |
| **MCT** | Model Calls per Task |
| **TCT** | Token / Cost per Task |
| **LAT** | End-to-End Task Latency |
| **SEC** | Security Boundary Violations |

Target direction with accumulated verified experience:

```text
ATCR ↑   VSR ↑   PRR ↑   RSR ↑
HIR  ↓   MCT ↓   TCT ↓   LAT ↓   SEC → 0
```

These are evaluation goals, not fabricated production-performance claims.

---

## ⚠️ Real-Environment Acceptance Still Required

```text
[ ] Genuine Windows 10 workstation matrix
[ ] Genuine Windows 11 workstation matrix
[ ] Physical multi-DPI matrix
[ ] Physical multi-monitor matrix
[ ] Real authorized Android E2E acceptance
[ ] Required real-provider L4/L5 evidence
[ ] Final physical whole-system release acceptance
```

Controlled fixtures and hosted CI are useful evidence, but AgentX does not claim they prove physical environments they did not exercise.

---

## 📚 Canonical Evidence

- [Project status](docs/STATUS.md)
- [600-task ledger](docs/TASKS.md)
- [Machine-readable task ledger](docs/TASKS.json)
- [Independent AX-001–AX-600 audit](docs/AUDIT_600.md)
- [Roadmap](docs/ROADMAP.md)
- [M16 production/release acceptance](docs/M16_PRODUCTION_RELEASE_ACCEPTANCE.md)
- [Architecture](docs/architecture/README.md)

---

## 🔒 License

AgentX is **proprietary source-visible software**. Public visibility does not itself grant permission to use, run, copy, modify, redistribute, deploy, sublicense, commercially exploit, host, incorporate, train on, or build derivative/competing systems from AgentX.

**All rights reserved by `harsh2025-sketch`.**

See [LICENSE](LICENSE) for the complete terms.

---

## 🌌 Vision

```text
CHATBOT
   ↓
ASSISTANT
   ↓
AGENT
   ↓
PERSONAL AUTOMATION
   ↓
ADAPTIVE AGENT
   ↓
╔════════════════════════════════════════╗
║   PERSONAL OPERATING INTELLIGENCE      ║
║                                        ║
║               A G E N T X              ║
╚════════════════════════════════════════╝
```

AgentX is built around the possibility that the **thousandth hour of interaction should be more valuable than the first** — because the system remembered what happened, verified what worked, learned reusable procedures, detected what stopped working, repaired failures and safely reused operational knowledge.

<div align="center">

## `> BUILD. EXECUTE. VERIFY. LEARN. ADAPT.`

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=18&duration=2200&pause=700&color=50FA7B&center=true&vCenter=true&repeat=true&width=850&lines=Experience+should+compound.;Successful+work+should+become+reusable.;Failure+should+produce+learning.;Learning+should+remain+governed.;The+agent+should+get+better+with+time." alt="AgentX philosophy animation"/>

**AgentX — Adaptive Personal Operating Intelligence**

`Windows-first` • `Local-first` • `Persistent` • `Governed` • `Adaptive`

[![Explore AgentX](https://img.shields.io/badge/EXPLORE-AGENTX-8B5CF6?style=for-the-badge&logo=github)](https://github.com/harsh2025-sketch/AgentX)
[![Issues](https://img.shields.io/badge/ISSUES-TRACKER-FF79C6?style=for-the-badge&logo=github)](https://github.com/harsh2025-sketch/AgentX/issues)

</div>
