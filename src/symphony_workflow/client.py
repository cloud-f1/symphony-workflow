"""HTTP client wrapper for the Jira Cloud REST API.

Uses API v2 for issue CRUD (plain-text `description`/comment bodies, not the
v3 Atlassian Document Format) — matches the pattern already proven against
`cloud-f1.atlassian.net` in this org's other tooling. `search_issues` is the
one exception: it uses v3's `/rest/api/3/search/jql`, because the classic v2
`/rest/api/2/search` endpoint is now HTTP 410 (retired by Atlassian) —
verified live against cloud-f1.atlassian.net on 2026-09-24. If you point this
at a different Jira Cloud instance, re-verify both endpoints are still
current before relying on them.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from symphony_workflow.config import JiraConfig
from symphony_workflow.models import (
    CurrentUser,
    HierarchyIssue,
    Issue,
    IssueCreateResult,
    Project,
    SearchResult,
    Transition,
)
from symphony_workflow.tracker import TrackerError

# Overridable per adopting repo/project — see docs/PORTABILITY.md. Defaults to
# ADLC (this POC's own board) for backward compatibility. Lives here (not
# poller.py) since ADLC-57: building the "ready ticket" query is this
# backend's own concern now, not the poller's — see search_ready_tickets().
JIRA_PROJECT_ENV = "SYMPHONY_WORKFLOW_JIRA_PROJECT"

# **2026-09-30, external-repo adoption**: the default `issuetype not in
# (Epic, Subtask)` assumes each ticket IS the atomic unit of work — true for
# ADLC's flat Task/Story backlog, false for a repo that mirrors its own
# Phase->Epic->Story->Subtask hierarchy into Jira (Epic->Epic, epic->Story,
# dev_task->Subtask — that repo's own sync script does exactly
# this). There, every real unit of work IS a Subtask, so the default query
# finds zero ready tickets no matter how much backlog exists.
#
# **Opt-in, not a behavior change**: unset (`""`) keeps today's exact JQL
# (`issuetype not in (Epic, Subtask)`) for ADLC and any other existing
# consumer. Set it to a comma-separated allowlist to search by inclusion
# instead of exclusion — the two are different questions ("what's NOT an
# epic" vs "what IS our atomic work item") and a repo whose real work lives
# on Subtasks needs the inclusion form, not a shorter exclusion list.
READY_ISSUETYPES_ENV = "SYMPHONY_WORKFLOW_READY_ISSUETYPES"


class JiraError(TrackerError):
    """Raised when the Jira API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Jira API error {status_code}: {message}")


class ReadBackError(JiraError):
    """A card Jira accepted does not read back as asked (wrong summary, parent or label), or an
    identity label matches more than one card. Status code 0: no HTTP response is at fault."""

    def __init__(self, message: str) -> None:
        self.status_code = 0
        TrackerError.__init__(self, message)


def check_label(label: str) -> str:
    """A Jira label cannot hold whitespace; reject it (and a quote, which would break the JQL
    that looks the label up) before any call is made. Raises `JiraError` 400, like Jira would."""
    if not label or any(ch.isspace() or ch in '"\\' for ch in label):
        raise JiraError(400, f"Invalid label {label!r}: no whitespace, quotes or backslashes")
    return label


class JiraClient:
    """Thin, testable wrapper around the Jira Cloud REST API v2."""

    def __init__(self, config: JiraConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.base_url,
            auth=(config.email, config.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_myself(self, *, timeout: float | None = None) -> CurrentUser:
        """Return the authenticated user. Cheap enough to double as a connectivity check —
        pass a short `timeout` (seconds) to override the client default for that use.
        """
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        response = self._client.get("/rest/api/2/myself", **kwargs)
        self._raise_for_status(response)
        return CurrentUser.from_api(response.json())

    def get_issue(self, key: str) -> Issue:
        response = self._client.get(f"/rest/api/2/issue/{key}")
        self._raise_for_status(response)
        return Issue.from_api(response.json())

    def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
        """Like `get_issue` but with the fields roll-up needs: issue type (+ its
        hierarchy level), status category, and `parent`. Raises `JiraError` (404 for an
        unknown key, 400 for a malformed one).
        """
        response = self._client.get(
            f"/rest/api/2/issue/{key}",
            params={"fields": "summary,status,issuetype,parent,assignee,subtasks,description"},
        )
        self._raise_for_status(response)
        return HierarchyIssue.from_api(response.json())

    def search_hierarchy(self, jql: str, *, page_size: int = 100) -> list[HierarchyIssue]:
        """Every issue matching `jql`, with the fields the hierarchy audit needs (type + level,
        status category, parent, assignee, and the list of Subtasks). Pages through
        `/rest/api/3/search/jql`'s `nextPageToken`, so a big project is fully counted: a partial
        list would make "no story is missing subtasks" a false positive.
        """
        found: list[HierarchyIssue] = []
        token: str | None = None
        for _ in range(100):  # hard stop: 10k issues in one audit query is a bug
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": ["summary", "status", "issuetype", "parent", "assignee", "subtasks"],
            }
            if token:
                body["nextPageToken"] = token
            response = self._client.post("/rest/api/3/search/jql", json=body)
            self._raise_for_status(response)
            payload = response.json()
            found.extend(HierarchyIssue.from_api(item) for item in payload.get("issues", []))
            token = payload.get("nextPageToken")
            if payload.get("isLast", not token) or not token:
                return found
        raise JiraError(500, f"Search did not terminate (pagination): {jql}")

    def list_children(self, parent_key: str, *, page_size: int = 100) -> list[HierarchyIssue]:
        """Every issue whose `parent` is `parent_key` (Subtasks of a Story, Stories of an
        Epic in a team-managed project; also Epic children in company-managed projects on
        current Jira). Pages through `/rest/api/3/search/jql`'s `nextPageToken` so an Epic
        with more than one page of Stories is still fully counted -- a partial list would
        make "all children done" a false positive.
        """
        children: list[HierarchyIssue] = []
        token: str | None = None
        for _ in range(100):  # hard stop: 10k children is a bug, not a real tree
            body: dict[str, Any] = {
                "jql": f"parent = {parent_key} ORDER BY key ASC",
                "maxResults": page_size,
                "fields": ["summary", "status", "issuetype", "parent"],
            }
            if token:
                body["nextPageToken"] = token
            response = self._client.post("/rest/api/3/search/jql", json=body)
            self._raise_for_status(response)
            payload = response.json()
            children.extend(HierarchyIssue.from_api(item) for item in payload.get("issues", []))
            token = payload.get("nextPageToken")
            if payload.get("isLast", not token) or not token:
                return children
        raise JiraError(500, f"Child search for {parent_key} did not terminate (pagination)")

    def issue_url(self, key: str) -> str:
        return f"{self._config.base_url}/browse/{key}"

    def search_ready_tickets(self, *, max_results: int = 50) -> SearchResult:
        """TrackerClient's backend-agnostic "what should the poller auto-Plan
        next" query, built as JQL. Default: Epic/Subtask excluded — you
        can't "implement" an Epic, and a Subtask's parent Story is the
        actual unit of work in this POC's model — moved here from poller.py
        (ADLC-57) so the poller doesn't need to know JQL exists.

        `READY_ISSUETYPES_ENV` switches this to an inclusion list instead
        (see its own comment for why some adopting repos need that) — set,
        it becomes `issuetype in (...)`; unset, behavior is unchanged.
        """
        project = os.environ.get(JIRA_PROJECT_ENV, "ADLC")
        included = os.environ.get(READY_ISSUETYPES_ENV, "").strip()
        if included:
            names = ", ".join(f'"{name.strip()}"' for name in included.split(",") if name.strip())
            type_clause = f"issuetype in ({names})"
        else:
            type_clause = "issuetype not in (Epic, Subtask)"
        jql = f'project = {project} AND status = "To Do" AND {type_clause} ORDER BY created ASC'
        return self.search_issues(jql, max_results=max_results)

    def search_issues(self, jql: str, max_results: int = 50) -> SearchResult:
        # Verified live 2026-09-24: the classic v2 `/rest/api/2/search` endpoint
        # now returns HTTP 410 (Atlassian retired it) — this MUST use the v3
        # enhanced search endpoint even though the rest of this client is v2.
        response = self._client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": jql,
                "maxResults": max_results,
                # issuetype / parent / subtasks feed the board fields (ADLC-388): one search gives a
                # Story's Epic and its subtask count, so a board needs no query per Story.
                "fields": [
                    "summary",
                    "status",
                    "assignee",
                    "issuetype",
                    "parent",
                    "subtasks",
                    "labels",
                ],
            },
        )
        self._raise_for_status(response)
        payload = response.json()
        issues = [Issue.from_api(item) for item in payload.get("issues", [])]
        return SearchResult(issues=issues, total=payload.get("total", len(issues)))

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        *,
        labels: list[str] | None = None,
        epic: str | None = None,
        parent: str | None = None,
        sprint_id: int | None = None,
        story_points: float | None = None,
        assignee_account_id: str | None = None,
        identity_label: str | None = None,
    ) -> IssueCreateResult:
        """`epic` and `parent` are two different linking mechanisms, not
        interchangeable spellings of the same thing — Jira project type
        decides which one applies. `epic` sets the classic/company-managed
        "Epic Link" custom field (confirmed on `CF1`; needs `epic_link_field`
        configured, otherwise silently a no-op). `parent` sets the standard
        `parent` field, which is what team-managed projects use for
        Story->Epic linking (confirmed on `ADLC`, a `simplified: true`
        project — `customfield_10014` errors there with "cannot be set. It
        is not on the appropriate screen") *and* what every project type uses
        for Subtask->Story linking. Callers must pick the one that matches
        the target project/issue type.

        `identity_label` makes the create idempotent (plan 0010): the project is searched for
        that label first. No hit: the card is created with the label added, then read back by
        key (never searched for, a JQL index lags a write by a few seconds) and compared with
        what was asked for. One hit: nothing is created and that card comes back with
        `existing=True`. Two or more: `ReadBackError`, nothing created. Without it, behaviour is
        unchanged.
        """
        if identity_label is not None:
            check_label(identity_label)
            hits = self.search_issues(
                f'project = {project_key} AND labels = "{identity_label}"', max_results=2
            ).issues
            if len(hits) > 1:
                keys = ", ".join(issue.key for issue in hits)
                raise ReadBackError(
                    f"Label {identity_label!r} is on more than one card ({keys}): "
                    "refusing to create another"
                )
            if hits:
                found = hits[0]
                return IssueCreateResult(
                    key=found.key,
                    id=found.id,
                    url=f"{self._config.base_url}/browse/{found.key}",
                    existing=True,
                )
            labels = [*(labels or []), identity_label]
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = description
        if labels:
            fields["labels"] = labels
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if epic and self._config.epic_link_field:
            fields[self._config.epic_link_field] = epic
        if parent:
            fields["parent"] = {"key": parent}
        if sprint_id is not None and self._config.sprint_field:
            fields[self._config.sprint_field] = sprint_id
        if story_points is not None and self._config.story_points_field:
            fields[self._config.story_points_field] = story_points

        response = self._client.post("/rest/api/2/issue", json={"fields": fields})
        self._raise_for_status(response)
        payload = response.json()
        result = IssueCreateResult(
            key=payload["key"],
            id=payload["id"],
            url=f"{self._config.base_url}/browse/{payload['key']}",
        )
        if identity_label is not None:
            self.verify_card(result.key, summary=summary, parent=parent, label=identity_label)
        return result

    def verify_card(
        self,
        key: str,
        *,
        summary: str | None = None,
        parent: str | None = None,
        label: str | None = None,
    ) -> Issue:
        """Read `key` back (a GET by key, not a search) and raise `ReadBackError` listing every
        difference from what was asked for: summary, parent key, label. Returns the issue read.
        """
        issue = self.get_issue(key)
        problems: list[str] = []
        if summary is not None and issue.summary != summary:
            problems.append(f"summary is {issue.summary!r}, expected {summary!r}")
        if parent is not None and issue.parent != parent:
            problems.append(f"parent is {issue.parent!r}, expected {parent!r}")
        if label is not None and label not in issue.labels:
            problems.append(f"label {label!r} is missing (has {issue.labels})")
        if problems:
            raise ReadBackError(f"{key} did not read back as asked: " + "; ".join(problems))
        return issue

    def update_issue(
        self,
        key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        parent: str | None = None,
        labels_add: list[str] | None = None,
    ) -> None:
        """PUT /issue/{key} with only the fields given — Jira's update endpoint
        only touches fields present in the payload, so omitted ones are left
        alone. At least one of summary/description/parent/labels_add must be given.

        `labels_add` adds labels without replacing the existing ones (plan 0010): it goes in
        `update.labels` as `{"add": ...}` operations, because a `fields.labels` write would
        overwrite the whole list. A label with whitespace is rejected before any call.

        `parent` re-parents the issue (e.g. moves a Subtask to another Story after
        splitting an oversized one; ADLC-146). Confirmed to work on team-managed projects
        via `fields.parent`. A JQL child list lags by a few seconds afterwards, so verify
        with `get_hierarchy_issue(key).parent_key`, not `list_children`.
        """
        fields: dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = description
        if parent is not None:
            fields["parent"] = {"key": parent}
        body: dict[str, Any] = {"fields": fields}
        if labels_add:
            body["update"] = {"labels": [{"add": check_label(label)} for label in labels_add]}
        response = self._client.put(f"/rest/api/2/issue/{key}", json=body)
        self._raise_for_status(response)

    def add_comment(self, key: str, body: str) -> None:
        response = self._client.post(f"/rest/api/2/issue/{key}/comment", json={"body": body})
        self._raise_for_status(response)

    def create_remote_link(self, key: str, *, url: str, title: str) -> None:
        """A Jira "web link" (`/remotelink`), distinct from a comment — shows up
        in the issue's Links section. Call this only once the target `url`
        actually resolves (e.g. after a commit pointing at it is merged) —
        there's nothing stopping you from linking a URL that 404s, so don't.
        """
        response = self._client.post(
            f"/rest/api/2/issue/{key}/remotelink",
            json={"object": {"url": url, "title": title}},
        )
        self._raise_for_status(response)

    def get_project(self, key: str) -> Project:
        """`GET /rest/api/3/project/{key}?expand=issueTypes,lead`. Raises JiraError(404) when the
        key is free (or the token cannot see the project)."""
        response = self._client.get(
            f"/rest/api/3/project/{key}", params={"expand": "issueTypes,lead"}
        )
        self._raise_for_status(response)
        return Project.from_api(response.json())

    @staticmethod
    def project_payload(
        key: str,
        name: str,
        *,
        lead_account_id: str,
        project_template_key: str,
        project_type_key: str = "software",
        description: str | None = None,
        assignee_type: str = "UNASSIGNED",
    ) -> dict[str, Any]:
        """The JSON body `create_project` sends; public so a dry run can show it unchanged."""
        payload: dict[str, Any] = {
            "key": key,
            "name": name,
            "projectTypeKey": project_type_key,
            "projectTemplateKey": project_template_key,
            "leadAccountId": lead_account_id,
            "assigneeType": assignee_type,
        }
        if description:
            payload["description"] = description
        return payload

    def create_project(
        self,
        key: str,
        name: str,
        *,
        lead_account_id: str,
        project_template_key: str,
        project_type_key: str = "software",
        description: str | None = None,
        assignee_type: str = "UNASSIGNED",
    ) -> dict[str, str]:
        """`POST /rest/api/3/project`. Needs global ADMINISTER permission. The template decides
        team-managed vs company-managed; read the project back (`get_project`) to confirm, since
        the template key's name is not a reliable signal. Returns `{"id", "key", "url"}`.
        A deleted project keeps its key and name reserved in Jira's trash for about 60 days."""
        body = self.project_payload(
            key,
            name,
            lead_account_id=lead_account_id,
            project_template_key=project_template_key,
            project_type_key=project_type_key,
            description=description,
            assignee_type=assignee_type,
        )
        response = self._client.post("/rest/api/3/project", json=body)
        self._raise_for_status(response)
        payload = response.json()
        return {
            "id": str(payload["id"]),
            "key": payload["key"],
            "url": f"{self._config.base_url}/browse/{payload['key']}",
        }

    def list_transitions(self, key: str) -> list[Transition]:
        response = self._client.get(f"/rest/api/2/issue/{key}/transitions")
        self._raise_for_status(response)
        return [
            Transition(id=t["id"], name=t["name"]) for t in response.json().get("transitions", [])
        ]

    def transition_issue(self, key: str, transition_name: str) -> None:
        transitions = self.list_transitions(key)
        match = next((t for t in transitions if t.name.lower() == transition_name.lower()), None)
        if match is None:
            available = ", ".join(t.name for t in transitions) or "none"
            raise JiraError(
                404,
                f"No transition named {transition_name!r} for {key}. Available: {available}",
            )
        response = self._client.post(
            f"/rest/api/2/issue/{key}/transitions", json={"transition": {"id": match.id}}
        )
        self._raise_for_status(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
            message = "; ".join(payload.get("errorMessages", [])) or str(
                payload.get("errors", payload)
            )
        except ValueError:
            message = response.text
        raise JiraError(response.status_code, message)
