# symphony-workflow

[English](README.md) · [繁體中文](README.zh-TW.md)

[![CI](https://github.com/cloud-f1/symphony-workflow/actions/workflows/ci.yml/badge.svg)](https://github.com/cloud-f1/symphony-workflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/cloud-f1/symphony-workflow)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Version](https://img.shields.io/github/v/tag/cloud-f1/symphony-workflow?sort=semver&label=version)](https://github.com/cloud-f1/symphony-workflow/tags)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](#和你的-agent-一起使用)
[![Plan sync](https://img.shields.io/badge/plan%20sync-Markdown%20%7C%20Linear%20%7C%20Jira-informational)](#agent-實際執行了什麼)

**給你的 AI agent 一份計畫和一個看板。** symphony-workflow 是一個 Claude Code plugin（兩個 skill）加上一個讓 agent 操作的 CLI：
它讀你用 Markdown 寫的計畫、把它顯示成看板、推到 **Linear** 或 **Jira**，並讓兩邊的狀態雙向保持一致。每個 Jira 操作同時也是一個
有 schema 描述的工具呼叫，所以任何 agent 都能使用。

它不會刪除任何東西，在你同意之前不會寫入任何追蹤系統，而且每個寫入都有 `--dry-run`（試跑）。

## 適合誰

- **你把計畫寫成 Markdown**（每個 epic 一個 `docs/epics/e1-...md`，像 Athena 風格的工作流程），想放到看板上，又不想重打一遍，
  也不想兩邊慢慢不一致。
- **你用 agent 處理 Jira 或 Linear 的票**（Claude Code、Codex、排程工作），希望它透過具名工具來讀取、規劃與移動票，而不是直接打 HTTP。
- **你在整理一個 Jira 專案**：Story 底下沒有 Subtask，或父項永遠關不掉。

## 和你的 agent 一起使用

**1. 在 Claude Code 安裝 plugin**（兩個 skill 加一個 SessionStart hook）：

```text
/plugin marketplace add cloud-f1/symphony-workflow
/plugin install symphony-workflow-ops --marketplace cloud-f1/symphony-workflow
```

**2. 安裝 skill 會呼叫的 CLI。** plugin 本身不含它：

```bash
uv tool install git+https://github.com/cloud-f1/symphony-workflow
```

**3. 說出你要什麼。** skill 會對計畫和看板的請求觸發；你不需要說出指令名稱。

### agent 得到什麼

| | 是什麼 | 涵蓋 |
|---|---|---|
| Skill `symphony-workflow-ops` | 如何操作這個工具 | plan sync（Markdown、Linear、Jira）、Plan/Implement 流程、發版、git 與 PR 的坑 |
| Skill `jira-ticket-ops` | 如何安全地動 Jira | 每個專案的憑證、階層稽核、Sub-task、rollup、拋棄式測試樹 |
| SessionStart hook | 健康檢查 | 開著 symphony-workflow checkout 時檢查本機 API server，其他地方保持安靜 |
| 工具呼叫 | `list-tools` 與 `call` | 每個 Jira 操作都是一個 JSON schema 工具，給不是 Claude Code 的 agent 用 |

### 可以這樣說

| 你說 | agent 做 | 會寫入東西嗎？ |
|---|---|---|
| 「把我 `docs/epics` 的計畫顯示成看板，什麼都不要改」 | 讀專案、預覽 | 不會 |
| 「哪些任務沒有待辦？幫我提議」 | 一個唯讀的 agent 回合，寫出你可以編輯與核准的提案檔 | 只有那個檔案 |
| 「把這份計畫放到 Linear，專案叫 Q4，先試跑」 | 先試跑，你同意後才真正執行 | 你同意後才寫 Linear |
| 「Jira 專案 KEY 也一樣」 | 先試跑，再真正執行 | 你同意後才寫 Jira |
| 「讓看板跟我的清單保持同步」 | 從看板拉取變更，推送勾選的待辦 | checkbox、卡片；衝突只回報、不動 |
| 「稽核我的 Jira 專案的 Story 和 Sub-task」 | 稽核、提議 Sub-task、往上收尾 | 你同意後才改票 |
| 「處理票 KEY-12：先規劃，我來審」 | 對那張票跑 Plan 階段，然後等待 | 你核准前不會有程式碼 |

### 一次真實的對話

這是真實執行，不是示意圖：一個載入了這個 plugin 的 Claude agent，在拋棄式的計畫專案裡工作。它載入 skill、讀專案、執行唯讀指令，什麼都沒寫
（`git status` 前後相同）。我第一次執行時 skill 完全沒有被觸發，因為它的描述沒有提到 Markdown 計畫；後來把描述加寬，再重跑一次。

![A real agent session: the agent loads the skill, reads the plan and previews it as a board](docs/snapshots/img/09-agent.svg)

<details>
<summary>這次對話的文字版</summary>

<!-- snapshot: docs/snapshots/09-agent.txt -->
```text
# a real agent session: Claude with this repo's plugin loaded, in a throwaway plan project.
# file reads are left out, and the agent's chained commands are shown one by one (same output).
you> I keep my plan as Markdown under docs/epics. Show me how it would look as a tidy task list for a board, but
     do not write or change anything yet.

agent> [loads the symphony-workflow-ops skill]
agent> Now the read-only CLI preview (profile without `--write`, then import-plan).

$ symphony-workflow profile
Project: .
Plan files: 4 epic file(s); index=False progress=True strategy-log=False; plan folders=0; athena commands=False
Tracker: jira credentials=False linear credentials=False SYMPHONY_WORKFLOW_TRACKER=unset; issue keys in history: none
Content: 2 phase(s) (1 not complete), 4 epic(s), tasks per phase median=1.5, acceptance items median=1.0, epics with a ready-made To-Do breakdown=75%
Recommendation 1 (from usage): tracker=markdown plan_source=epics-md todo_source=hybrid push=manual record=markdown include_done=false
  - tracker: no tracker evidence in this project -> use Markdown files
  - tracker: LINEAR_API_KEY is set in the shell, shared by every project, but not in this project's .env -> not enough to pick Linear; pass --to linear if you want it
  - plan source: 4 epic file(s) under docs/epics -> read them with `epics-md`
Recommendation 2 (refined from content): tracker=markdown plan_source=epics-md todo_source=breakdown push=manual record=markdown include_done=false
  - To-Do source: 75% of epics have a ready-made To-Do breakdown -> use each epic's own breakdown (to-do list, phases or story headings) as To-Dos

$ symphony-workflow import-plan
plan: 2 categor(ies), 3 task(s), 5 to-do(s)
  phase-2  Phase 2  [in_progress]
    E1  Login page  [in_progress]  (3 to-dos)
    E2  Reports  [todo]  (2 to-dos)
  unphased  Unphased  [todo]
    E4  Not in any phase  [todo]  (0 to-dos)
  WARNING unphased one_task: a Category with one Task is really a Task
  WARNING E4 no_todos: no To-Dos yet (layer-3 generation fills them in)

$ symphony-workflow import-plan --include-done
plan: 3 categor(ies), 4 task(s), 6 to-do(s)
  phase-1  Phase 1  [done]
    E3  Old work  [done]  (1 to-dos)
  phase-2  Phase 2  [in_progress]
    E1  Login page  [in_progress]  (3 to-dos)
    E2  Reports  [todo]  (2 to-dos)
  unphased  Unphased  [todo]
    E4  Not in any phase  [todo]  (0 to-dos)
  WARNING phase-1 one_task: a Category with one Task is really a Task
  WARNING E3 one_todo: one To-Do means the Task is really a To-Do
  WARNING unphased one_task: a Category with one Task is really a Task
  WARNING E4 no_todos: no To-Dos yet (layer-3 generation fills them in)

agent> Here's how your plan would look as a board. I only read files to get this. Nothing was
       written or changed: I used `symphony-workflow profile` without `--write` and `import-
       plan`, and `git status` is the same as before.

       ## Board preview (`docs/epics`)

       **Phase 2: In progress**
       - **E1: Login page** (in progress)
         - [ ] Build the form
         - [ ] Wire the API
         - [ ] Tests
         - *Acceptance:* form validates the email · wrong password shows an error
       - **E2: Reports** (to do)
         - [ ] Phase 1: Data layer
         - [ ] Phase 2: UI
         - *Acceptance:* export works

       **Unphased: To do**
       - **E4: Not in any phase** (to do)
         - *(no to-dos yet)*
         - *Acceptance:* something

       **Hidden by default (Phase 1, complete):** E3: Old work, with one finished to-do, "Ship
       it". It only appears if you add `--include-done`.

       That makes 2 columns, 3 cards and 5 to-dos. `notes.md` has no epic in it, so it was
       skipped.

       ## Things to tidy before pushing it anywhere
       1. **E4 has no to-dos.** You can write some yourself, or run `split-todos` to get
          suggested ones that you then approve.
       2. **"Unphased" holds only one card.** The tool warns that a column with one card is
          really just a card. Moving E4 into Phase 2 or a new phase would fix it.
       3. **E3 would cause the same kind of warning** if you include done work: one column with
          one card that has one to-do. It's probably best left hidden.
       4. **E2's to-dos are its "Phase 1 / Phase 2" headings,** so the board would have a "Phase
          2" column and also an E2 to-do called "Phase 2: UI". You may want to rename those to
          "Data layer" and "UI".
       5. **Board status can disagree with `epic-progress.md`.** That file marks E1's Spec step
          as done, and one of E1's acceptance items is ticked. All three E1 to-dos would still
          show as not started.

       ## Where it would go
       The tool found no Jira or Linear setup in this project, so it suggests keeping the board
       as Markdown files, using each epic's own lists as its to-dos. When you're ready, it could
       go to Markdown or to Linear or Jira. I'd do a `--dry-run` first and only write once you
       say so.

       [last paragraph omitted: it was about claude.ai connectors in the machine that ran this]
```

</details>

## agent 實際執行了什麼

下面每張圖，都是 agent 為上面某個請求實際執行的內容，擷取自拋棄式專案的真實執行（Linear 和 Jira 則是結束後就刪除的拋棄式看板）。圖的下面有文字版，
也放在 `docs/snapshots/`。快照維持英文，因為那是指令實際印出的內容。

### 1. 檢查憑證，而 token 永遠不會進到對話裡

`init-env` 寫出被 gitignore 的 `.env`，欄位是空的；`check-env` 說出缺什麼，而且永遠不印出值。

![init-env creates .env, check-env reports missing keys](docs/snapshots/img/01-setup.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/01-setup.txt -->
```text
$ symphony-workflow init-env
Created .env (owner-only permissions).
Added .env to .gitignore.
Next: fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN, then run `symphony-workflow check-env --verify`.

$ symphony-workflow check-env
.env: found
  MISSING  JIRA_BASE_URL
  MISSING  JIRA_EMAIL
  MISSING  JIRA_API_TOKEN
(exit 1)

# you paste the base URL and your e-mail, forget the token:
$ symphony-workflow check-env
.env: found
  ok       JIRA_BASE_URL
  ok       JIRA_EMAIL
  MISSING  JIRA_API_TOKEN
(exit 1)
```

</details>

### 2. 看專案目前是怎麼運作的

agent 讀計畫檔、有哪些憑證的*名稱*、git 歷史裡的票號、計畫的形狀，然後建議一種設定。它什麼都不改。

![profile reads the project and prints two recommendations](docs/snapshots/img/02-profile.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/02-profile.txt -->
```text
$ symphony-workflow profile --path .
Project: .
Plan files: 4 epic file(s); index=False progress=True strategy-log=False; plan folders=0; athena commands=False
Tracker: jira credentials=False linear credentials=False SYMPHONY_WORKFLOW_TRACKER=unset; issue keys in history: none
Content: 2 phase(s) (1 not complete), 4 epic(s), tasks per phase median=1.5, acceptance items median=1.0, epics with a ready-made To-Do breakdown=75%
Recommendation 1 (from usage): tracker=markdown plan_source=epics-md todo_source=hybrid push=manual record=markdown include_done=false
  - tracker: no tracker evidence in this project -> use Markdown files
  - tracker: LINEAR_API_KEY is set in the shell, shared by every project, but not in this project's .env -> not enough to pick Linear; pass --to linear if you want it
  - plan source: 4 epic file(s) under docs/epics -> read them with `epics-md`
Recommendation 2 (refined from content): tracker=markdown plan_source=epics-md todo_source=breakdown push=manual record=markdown include_done=false
  - To-Do source: 75% of epics have a ready-made To-Do breakdown -> use each epic's own breakdown (to-do list, phases or story headings) as To-Dos
```

</details>

`import-plan` 把計畫顯示成三層（類別、任務、待辦），形狀不對時會警告：

![import-plan shows the plan and its warnings](docs/snapshots/img/03-import-plan.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/03-import-plan.txt -->
```text
$ symphony-workflow import-plan --path .
plan: 2 categor(ies), 3 task(s), 5 to-do(s)
  phase-2  Phase 2  [in_progress]
    E1  Login page  [in_progress]  (3 to-dos)
    E2  Reports  [todo]  (2 to-dos)
  unphased  Unphased  [todo]
    E4  Not in any phase  [todo]  (0 to-dos)
  WARNING unphased one_task: a Category with one Task is really a Task
  WARNING E4 no_todos: no To-Dos yet (layer-3 generation fills them in)
```

</details>

### 3. 把計畫留成 Markdown，不用看板

`sync-plan --to markdown` 寫出 Athena 相容的檔案，只動它自己管理的區塊，你的文字會保留。

![sync-plan to Markdown: dry run, real run, then nothing to do](docs/snapshots/img/04-markdown.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/04-markdown.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to markdown --dry-run
  To-Dos for E4: from steps
  Would update docs/epics/e1-login.md
  Would update docs/epics/e2-reports.md
  Would update docs/epics/e4-loose.md
  Would create docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task

$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  To-Dos for E4: from steps
  Updated      docs/epics/e1-login.md
  Updated      docs/epics/e2-reports.md
  Updated      docs/epics/e4-loose.md
  Created      docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task

# run it again: nothing to do
$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  Unchanged    docs/epics/e1-login.md
  Unchanged    docs/epics/e2-reports.md
  Unchanged    docs/epics/e4-loose.md
  Unchanged    docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task
```

</details>

### 4. 讓 agent 提議待辦，由你核准

沒有自己拆分的任務，預設會得到五個固定步驟。想要更具體，就讓一個唯讀的 agent 回合寫出你可以編輯的提案。核准之前什麼都不會送到看板，
而且 `sync-plan` 會等待，而不是先寫入那五個步驟。

![split-todos proposes To-Dos, the proposal is approved, sync-plan uses it](docs/snapshots/img/05-todos.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/05-todos.txt -->
```text
$ symphony-workflow split-todos --path .
Proposed To-Dos for 1 Task(s): docs/context/todo-proposal.md
Review docs/context/todo-proposal.json, then run `symphony-workflow split-todos --approve`.

# docs/context/todo-proposal.json is what you review and edit:
{
  "approved": false,
  "tasks": {
    "E4": [
      "Clarify and document the scope/requirements for this task (since it's currently in no phase and criteria is unspecified)",
      "Implement the core change needed to satisfy the requirement",
      "Update related docs/config and wire the change into the existing system",
      "Write and run tests covering the acceptance criteria, confirm they pass"
    ]
  }
}

$ symphony-workflow split-todos --path . --approve
Approved 1 Task(s) in docs/context/todo-proposal.json.

$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  To-Dos for E4: from proposal
  Updated      docs/epics/e1-login.md
  Updated      docs/epics/e2-reports.md
  Updated      docs/epics/e4-loose.md
  Created      docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task
```

</details>

### 5. 把計畫推到 Linear

類別變成里程碑，任務變成 issue，待辦變成 sub-issue。試跑會讀取 Linear 但不寫入；再次執行會靠每個描述裡的 `sd-id:` 找到所有物件。第一次請用全新的專案名稱。

![sync-plan to Linear: dry run, create, re-run](docs/snapshots/img/06-linear.svg)

<details>
<summary>文字版（執行的開頭）</summary>

<!-- snapshot: docs/snapshots/06-linear.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan" --dry-run
  To-Dos for E4: from steps
Linear project: Demo plan (dry run, nothing written)
  Would create milestone Phase 2
  Would create issue     Login page
  Would create sub-issue Build the form
  Would create sub-issue Wire the API
  Would create sub-issue Tests
  Would create issue     Reports
  Would create sub-issue Phase 1 — Data layer
  Would create sub-issue Phase 2 — UI
  Would create milestone Unphased
  Would create issue     Not in any phase
  Would create sub-issue Spec
  Would create sub-issue Implement
  Would create sub-issue Tests and QA
  Would create sub-issue Commit
  Would create sub-issue Merge
  WARNING unphased one_task: a Category with one Task is really a Task

$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan"
  To-Dos for E4: from steps
...
```

</details>

### 6. 讓狀態雙向保持一致

一邊的變更會帶到另一邊。兩邊都改了，就是衝突：只回報，原地不動。

![sync-status pulls, pushes, and reports a conflict](docs/snapshots/img/07-status.svg)

<details>
<summary>文字版</summary>

<!-- snapshot: docs/snapshots/07-status.txt -->
```text
# in Linear someone moves 'Build the form' to Done; you pull it:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  pull      to-do  E1.d1  plan=todo tracker=done (last sync: todo)
  1 pull

# you tick 'Wire the API' in docs/epics/e1-login.md; push it:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  push      to-do  E1.d2  plan=done tracker=todo (last sync: todo)
  1 push

# nothing left to do:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  nothing to do

# both sides change 'Tests' differently: a conflict, left alone
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  conflict  to-do  E1.d3  plan=done tracker=in_progress (last sync: todo)
  1 conflict
Conflicts were left as they are: fix one side, then run this again.
(exit 1)
```

</details>

### 7. 把計畫推到 Jira

類別變成 Epic，任務變成 Story，待辦變成 Subtask。它建立的票號會存進 `docs/context/sync-state.json`（請 commit），因為 Jira 的子項列表在建立之後會延遲幾秒才更新。

![sync-plan to Jira: dry run, create, re-run](docs/snapshots/img/08-jira.svg)

<details>
<summary>文字版（執行的開頭）</summary>

<!-- snapshot: docs/snapshots/08-jira.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to jira --project ADLC --dry-run
  To-Dos for E4: from steps
Jira project: ADLC (dry run, nothing written)
  Would create epic     Phase 2
  Would create story    Login page
  Would create subtask  Build the form
  Would create subtask  Wire the API
  Would create subtask  Tests
  Would create story    Reports
  Would create subtask  Phase 1 — Data layer
  Would create subtask  Phase 2 — UI
  Would create epic     Unphased
  Would create story    Not in any phase
  Would create subtask  Spec
  Would create subtask  Implement
  Would create subtask  Tests and QA
  Would create subtask  Commit
  Would create subtask  Merge
  WARNING unphased one_task: a Category with one Task is really a Task
...
```

</details>

## 出貨：分支、PR、合併、發版

Jira 會把分支、commit 和 pull request 掛到卡片上，是因為分支名稱裡有票號。agent 用五個指令，讓票號不會在途中掉掉：

| 步驟 | 指令 | 會發生什麼 |
|---|---|---|
| 開始 | `start ADLC-12` | 從最新的 `origin/main` 建立分支 `ADLC-12-<slug>` 和 worktree；卡片移到 In Progress |
| Commit | `check-commit` | 標題長得像 `feat(scope): 改了什麼 (ADLC-12)`；plugin 的 hook 會擋下忘了票號的 commit |
| 開 PR | `pr ADLC-12` | 推送並開 PR，標題和內文都帶票號；卡片移到 In Review |
| 收尾 | `land ADLC-12` | 到 GitHub 確認 PR 已合併，更新 `main`，刪除遠端分支、worktree 和本機分支；卡片變成 Done 並往上收 |
| 發版 | `release 0.4.0` | 跑發版檢查、打 tag、推 tag，並用 CHANGELOG 建立 GitHub Release；`changelog-draft` 依 commit 產生該版的段落 |

不會強制任何事：有未提交變更的 worktree 不會被動，還沒合併的 PR 什麼都不會改。可選的 Jira 設定（smart commits、Create branch 按鈕、PR 合併時移動卡片）寫在 [docs/JIRA_GIT_SETUP.md](docs/JIRA_GIT_SETUP.md)。

## agent 遵守的規則

這些寫在 skill 裡，所以 agent 不需要被告知就會遵守：

- **先讀、後寫。** 在任何東西碰到看板之前，先 `profile` 和 `--dry-run`。
- **沒有你的同意，絕不寫入追蹤系統**，也絕不刪除：只存在於一邊的卡片或檔案，只會被回報。
- **絕不印出憑證。** 欄位放在專案的 `.env`；檢查只會說缺什麼，不會印出值。
- **絕不碰不是它建立的東西。** 指向別人的票的 `sync-state.json` 會被忽略；Linear 專案裡手動建立的 issue 不會被認領；git 執行時會關掉由儲存庫提供的命令。
- **改動要真實驗證。** 拋棄式看板、只刪該次執行建立的東西的清理，然後回報。
- **只有你啟動時才開始 Implement。** 背景 poller 只做規劃，由人核准。

## 給其他 agent：工具呼叫

任何能執行指令的 agent，都能把同樣的操作當成有 JSON schema 的具名工具使用：

```bash
symphony-workflow list-tools                                  # 每個工具的 JSON schema
symphony-workflow call get_issue '{"key": "PROJ-1"}'          # 碰到 Jira 之前先用 schema 驗證
```

## 我該選哪種設定？

| 你有 | 說 / 執行 | 需要 |
|---|---|---|
| 計畫檔，沒有追蹤系統 | 「把我的計畫顯示成看板」（`profile`、`sync-plan --to markdown`） | 不需要任何東西 |
| 計畫檔，任務沒有待辦 | 「幫我提議待辦」（`split-todos`） | Claude Agent SDK 能使用的 Claude 憑證 |
| 計畫檔，想要 Linear 看板 | 「放到 Linear」（`sync-plan --to linear --project "名稱"`） | `LINEAR_API_KEY`、`SYMPHONY_WORKFLOW_LINEAR_TEAM` |
| 計畫檔，想要 Jira 看板 | 「放到 Jira」（`sync-plan --to jira --project KEY`） | `.env` 裡的三個 Jira 欄位 |
| 想讓看板保持同步 | 「讓它保持同步」（`sync-status`） | 與目的地相同 |
| Jira 票，加上一個處理它們的 agent | Plan/Implement 流程，見 `docs/QUICKSTART.md` | Jira 欄位與 Claude 憑證 |
| 一個已經亂掉的 Jira 階層 | 「稽核我的專案」（`audit`、`add-subtasks`、`rollup`） | 三個 Jira 欄位 |

憑證放在專案自己的、被 gitignore 的 `.env`（`symphony-workflow init-env`），不要貼在對話裡。只用 Markdown 不需要任何憑證。

## Reference

參考資料（工具呼叫層、追蹤系統後端、Jira 階層指令、專案結構、如何新增工具或後端）維持英文，見
[README.md 的 Reference](README.md#reference)。Plan/Implement 流程的逐步說明在 `docs/QUICKSTART.md`，專案目前狀態在
`docs/HANDOVER.md`。
