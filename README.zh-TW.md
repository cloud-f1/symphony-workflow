# symphony-workflow

[English](README.md) · [繁體中文](README.zh-TW.md)

把計畫寫成 Markdown 檔，需要看板時再放進 **Linear** 或 **Jira**，並讓兩邊的狀態雙向保持一致。底層的每個 Jira
操作同時也是一個有 schema 描述的工具呼叫，所以 AI agent 可以像工程師一樣操作票。

這裡的任何指令都不會刪除東西，而且每個會寫入的指令都有 `--dry-run`（試跑）。

## 適合誰

- **你把計畫寫成 Markdown**（每個 epic 一個 `docs/epics/e1-...md`，像 Athena 風格的工作流程），想放到 Linear 或 Jira
  看板，又不想重打一遍，也不想兩邊慢慢不一致。
- **你用 AI agent 處理 Jira 票**（Claude Code、Codex、排程工作），希望它透過具名工具來讀取、留言、移動與規劃票，
  而不是直接打 HTTP。
- **你在整理一個 Jira 專案**：Story 底下沒有 Subtask，或父項永遠關不掉。

它不是給無關程式碼引用的 Jira 客戶端函式庫，也不會修改你的程式碼：Implement 階段永遠由人啟動。

## 五分鐘上手

**1. 安裝。** 不需要 clone：

```bash
uv tool install git+https://github.com/cloud-f1/symphony-workflow   # 留下一個 `symphony-workflow` 指令
uvx --from git+https://github.com/cloud-f1/symphony-workflow symphony-workflow --help   # 或只試用一次
```

在網址後加 `@vX.Y.Z` 可以釘住某個版本（看 tags）。若要改程式碼：`git clone`，再 `uv sync --extra dev`，指令前面加 `uv run`。

**2. 看一個專案，什麼都不改。** 在有 `docs/epics/*.md` 的專案裡：

```bash
symphony-workflow profile --path .
```

**3. 憑證，只有需要追蹤系統時才要。** 只用 Markdown 不需要任何憑證。要用 Jira 或 Linear，請放在專案自己的、
被 gitignore 的 `.env`，不要貼在聊天裡：

```bash
symphony-workflow init-env          # 建立只有空欄位的 .env，並加進 .gitignore
symphony-workflow check-env --verify # 列出缺少的欄位，只做一次唯讀呼叫，永遠不印出值
```

## 我該選哪種設定？

| 你有 | 執行 | 需要 |
|---|---|---|
| 計畫檔，沒有追蹤系統 | `profile`，然後 `sync-plan --to markdown` | 不需要任何東西 |
| 計畫檔，任務沒有待辦 | `split-todos`，審閱，`split-todos --approve` | Claude Agent SDK 能使用的 Claude 憑證 |
| 計畫檔，想要 Linear 看板 | `sync-plan --to linear --project "名稱"` | `LINEAR_API_KEY`、`SYMPHONY_WORKFLOW_LINEAR_TEAM` |
| 計畫檔，想要 Jira 看板 | `sync-plan --to jira --project KEY` | `.env` 裡的三個 Jira 欄位 |
| 想讓看板保持同步 | `sync-status --to linear` 或 `--to jira` | 與目的地相同 |
| Jira 票，加上一個處理它們的 agent | Plan/Implement 流程，見 `docs/QUICKSTART.md` | Jira 欄位與 Claude 憑證 |
| 一個已經亂掉的 Jira 階層 | `audit`、`add-subtasks`、`rollup`，見 Reference | 三個 Jira 欄位 |

不確定時先跑 `profile`：它會印出兩個建議，第一個來自專案的使用方式，第二個依計畫內容修正，除非你加 `--write`，
否則什麼都不會存。

## 使用情境

下面每個快照都是真實輸出，來自一個拋棄式專案（Linear 和 Jira 則是結束後就刪除的拋棄式看板）。完整文字在
`docs/snapshots/`。快照維持英文，因為那是指令實際印出的內容。

### 1. 設定憑證，而不是把 token 貼進聊天

你要在新專案裡用 Jira 或 Linear。`init-env` 寫出檔案，`check-env` 告訴你缺什麼，而且永遠不印出值。

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

下一步：用編輯器填入空欄位，再執行 `symphony-workflow check-env --verify`。

### 2. 看一個專案目前是怎麼運作的

在選擇任何設定之前，先讓工具讀專案：它的計畫檔、有哪些憑證的*名稱*、git 歷史裡的票號，以及計畫的形狀。

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

接著 `import-plan` 把計畫顯示成三層（類別、任務、待辦），形狀不對時會警告：

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

只有一個任務的類別其實就是一個任務；沒有待辦的任務，看板還沒有東西可以追蹤。情境 3 和 4 處理第二個問題。

### 3. 把計畫留成 Markdown，不用追蹤系統

沒有看板，但你想要可以 commit 的任務清單和索引。`sync-plan --to markdown` 會寫出 Athena 相容的檔案，而且只動它自己
管理的區塊，你手寫的文字會保留。

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

試跑告訴你會改什麼，真正執行就改了，第三次執行則發現沒有事情要做。

### 4. 讓 agent 提議待辦，由你核准

沒有自己拆分的任務，預設會得到五個固定步驟（Spec、Implement、Tests and QA、Commit、Merge）。想要更具體的內容，就讓
agent 跑一個唯讀回合，寫出一份你可以編輯的提案。核准之前，什麼都不會送到追蹤系統。

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

提案就是一個檔案：改措辭、刪掉一項，然後核准。在你核准之前，`sync-plan` 會等待，而不是先把五個步驟寫進去。

### 5. 把計畫推到 Linear

類別變成里程碑，任務變成 issue，待辦變成 sub-issue，全都在同一個專案裡。試跑會讀取 Linear，但不寫入。

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

第二次、第三次執行，會靠它放在每個描述裡的 `sd-id:` 那一行找到每個物件：

<!-- snapshot: docs/snapshots/06-linear.txt -->
```text
# run it again: nothing to create or update
$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan"
  To-Dos for E4: from steps
Linear project: Demo plan
  Unchanged    milestone Phase 2
  Unchanged    issue     Login page
  Unchanged    sub-issue Build the form
  Unchanged    sub-issue Wire the API
  Unchanged    sub-issue Tests
  Unchanged    issue     Reports
  Unchanged    sub-issue Phase 1 — Data layer
  Unchanged    sub-issue Phase 2 — UI
  Unchanged    milestone Unphased
  Unchanged    issue     Not in any phase
  Unchanged    sub-issue Spec
  Unchanged    sub-issue Implement
  Unchanged    sub-issue Tests and QA
  Unchanged    sub-issue Commit
  Unchanged    sub-issue Merge
  WARNING unphased one_task: a Category with one Task is really a Task
```

第一次請用全新的 `--project` 名稱：既有專案裡手動建立的 issue 沒有 `sd-id:` 那一行，會被重複建立，而不是被認領。

### 6. 讓狀態雙向保持一致

一邊的變更會帶到另一邊。兩邊都改了，就是衝突：會被回報，而且原地不動。

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

拉取會勾選你 Markdown 檔裡的 checkbox；推送會移動卡片。Athena 風格專案的進度矩陣不會被寫回：它有五個步驟欄，
看板只有一個狀態。

### 7. 把計畫推到 Jira

類別變成 Epic，任務變成 Story，待辦變成 Subtask。它建立的票號會存進 `docs/context/sync-state.json`（請 commit 這個檔案），
因為 Jira 的子項列表在建立之後會延遲幾秒才更新。

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

再執行一次，會靠標記和存下的票號找到卡片，所以不會重複建立。

## 它絕對不會做的事

- **刪除任何東西**，任何方向都不會。只存在於一邊的卡片或檔案，只會被回報。
- **覆蓋你的文字。** Markdown 檔只有 `<!-- sd:begin ... -->` 區塊內會被改動。
- **印出憑證。** 欄位放在專案的 `.env`；檢查只會說缺什麼，不會印出值。
- **信任不是它寫的檔案。** `docs/context/sync-state.json` 若指向別人的票，會被忽略；git 執行時會關掉由儲存庫提供的
  命令；計畫檔如果是 symlink，會被拒絕。
- **自己啟動 Implement。** 背景 poller 只做規劃；Implement 由人啟動。

兩件要知道的事：id 在一份計畫內唯一，所以兩份計畫不能同步進同一個 Jira 專案；而且 Linear 和 Jira 的目的地只對它們自己
建立的物件保證冪等。

## Reference

參考資料（工具呼叫層、追蹤系統後端、Jira 階層指令、專案結構、如何新增工具或後端）維持英文，見
[README.md 的 Reference](README.md#reference)。Plan/Implement 流程的逐步說明在 `docs/QUICKSTART.md`，專案目前狀態在
`docs/HANDOVER.md`。
