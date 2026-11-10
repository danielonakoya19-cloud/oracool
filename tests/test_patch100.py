"""patch100 — connector TOTAL ACCESS: real writes (edit/change/create/send) in linked accounts."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
INDEX_SRC = (ROOT / "index.html").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture()
def http(monkeypatch):
    calls = []

    def fake(url, method="GET", headers=None, json_body=None, timeout=25):
        calls.append({"url": url, "method": method, "json": json_body})
        if "/repos/" in url and url.endswith("/issues"):
            return 201, {"html_url": "https://github.com/o/r/issues/9"}
        if "/issues/" in url and url.endswith("/comments"):
            return 201, {"html_url": "https://github.com/o/r/issues/3#issuecomment-1"}
        if "/contents/" in url:
            return 200 if method == "GET" else 200, {"sha": "abc123", "content": {"html_url": "https://github.com/o/r/blob/main/x.md"}}
        if "/messages/send" in url:
            return 200, {"id": "msg1"}
        if "quickAdd" in url:
            return 200, {"summary": "Standup", "htmlLink": "https://cal"}
        if "values" in url and "append" in url:
            return 200, {"updates": {"updatedRows": 1}}
        if url.endswith(":batchUpdate"):
            return 200, {}
        if url.endswith("/documents/doc123456789"):
            return 200, {"body": {"content": [{"endIndex": 42}]}}
        if "/v1/pages" in url:
            return 200, {"url": "https://notion.so/new"}
        if "/blocks/" in url:
            return 200, {}
        if "linear.app" in url:
            return 200, {"data": {"teams": {"nodes": [{"id": "team1"}]},
                                  "issueCreate": {"success": True, "issue": {"url": "https://linear.app/x/1"}}}}
        if "create_folder" in url:
            return 200, {}
        if "/rest/v1/" in url:
            return 201, {"id": 1}
        return 200, {}

    monkeypatch.setattr(server, "_conn_http", fake)
    return calls


def _w(name, text, tok="tok"):
    return server._connector_write(name, {}, text.lower(), text, tok)


# ---------------------------------------------------------------- github writes

def test_github_close_issue(http):
    r = _w("github", "close issue #5 in danielonakoya19-cloud/oracool")
    assert "closed" in r
    hit = [c for c in http if c["method"] == "PATCH"]
    assert hit and hit[0]["url"].endswith("/issues/5") and hit[0]["json"] == {"state": "closed"}


def test_github_comment(http):
    r = _w("github", "comment on issue #3 in o/r: looks good to me")
    assert "Comment posted" in r
    hit = [c for c in http if c["url"].endswith("/issues/3/comments")]
    assert hit and hit[0]["json"]["body"] == "looks good to me"


def test_github_create_issue_command_form(http):
    r = _w("github", "connector :: github :: create issue :: o/r :: Fix login :: users cannot sign in")
    assert "issue created" in r.lower()
    hit = [c for c in http if c["method"] == "POST" and c["url"].endswith("/issues")]
    assert hit and hit[0]["json"]["title"] == "Fix login" and "cannot sign in" in hit[0]["json"]["body"]


def test_github_edit_file(http):
    r = _w("github", "edit file README.md in o/r: # Hello\nThis is the new readme.")
    assert "README.md" in r and ("Updated" in r or "Created" in r)
    hit = [c for c in http if c["method"] == "PUT"]
    assert hit and hit[0]["json"]["sha"] == "abc123" and hit[0]["json"]["content"]


# ---------------------------------------------------------------- google writes

def test_gmail_send(http):
    r = _w("gmail", "send email to bob@example.com subject: Hello :: just saying hi")
    assert "Email sent to bob@example.com" in r
    hit = [c for c in http if "/messages/send" in c["url"]]
    assert hit and hit[0]["json"].get("raw")


def test_calendar_create_event(http):
    r = _w("calendar", "create calendar event: Standup tomorrow 9am")
    assert "Calendar event created" in r
    hit = [c for c in http if "quickAdd" in c["url"]]
    assert hit and hit[0]["method"] == "POST"


def test_sheets_append_row(http):
    r = _w("sheets", "add row to google sheet https://docs.google.com/spreadsheets/d/doc123456789/edit: Ada, ada@x.y, 2026-01-01")
    assert "Row appended" in r
    hit = [c for c in http if "append" in c["url"]]
    assert hit and hit[0]["json"]["values"] == [["Ada", "ada@x.y", "2026-01-01"]]


def test_docs_append_text(http):
    r = _w("docs", "add to google doc https://docs.google.com/document/d/doc123456789/edit: A fresh paragraph.")
    assert "added" in r.lower()
    hit = [c for c in http if c["url"].endswith(":batchUpdate")]
    assert hit and "insertText" in str(hit[0]["json"])


# ---------------------------------------------------------------- other families

def test_notion_create_page(http):
    r = _w("notion", "create notion page under https://notion.so/parent123: Meeting notes :: discussed the roadmap")
    assert "Notion page created" in r
    hit = [c for c in http if c["url"].endswith("/v1/pages")]
    assert hit and hit[0]["json"]["parent"]["page_id"] == "parent123"


def test_linear_create_issue(http):
    r = _w("linear", "create linear issue: Fix the onboarding bug")
    assert "Linear issue created" in r


def test_dropbox_create_folder(http):
    r = _w("dropbox", "create dropbox folder: Projects/2026")
    assert "folder created" in r.lower()


def test_supabase_insert_row(http):
    c = {"url": "https://xyz.supabase.co", "token": "svc"}
    r = server._connector_write("supabase", c, 'insert into table notes {"title": "hi"}'.lower(),
                                'insert into table notes {"title": "hi"}', "svc")
    assert "inserted" in r.lower()
    hit = [x for x in http if "/rest/v1/notes" in x["url"] and x["method"] == "POST"]
    assert hit and hit[0]["json"] == {"title": "hi"}


# ---------------------------------------------------------------- read stays read

def test_reads_fall_through_untouched(http):
    assert _w("github", "list my github repos") is None
    assert _w("gmail", "check my gmail inbox") is None
    assert _w("github", "what are the recent changes in o/r") is None   # 'changes' must not hijack
    assert http == []


def test_write_missing_target_gives_usage_hint(http):
    r = _w("gmail", "send an email")
    assert "who should i send it to" in r.lower()
    r2 = _w("github", "open a github issue")
    assert "which repository" in r2.lower()


# ---------------------------------------------------------------- prompts + wiring

def test_prompt_total_access():
    assert "TOTAL ACCESS (READ AND WRITE)" in SERVER_SRC
    assert "connector :: <service> :: <action> :: <target> :: <content>" in SERVER_SRC
    assert "Never say you can only read" in SERVER_SRC
    assert "never tell them to go do it manually" in SERVER_SRC.lower()
    assert "TOTAL access" in INDEX_SRC
    i = SERVER_SRC.find("TOTAL ACCESS (READ AND WRITE)")
    assert "patch100" not in SERVER_SRC[i:i + 400]


def test_action_verbs_trigger_connector_branch():
    for v in ("'edit', 'change', 'update'", "'delete', 'remove', 'rename'", "'comment', 'send', 'post', 'upload'"):
        assert v in SERVER_SRC


def test_write_layer_wired_into_dispatch():
    assert "def _connector_write(name, c, low, text, tok):" in SERVER_SRC
    i = SERVER_SRC.find("def _connector_do")
    assert "_connector_write(name, c, low, text, tok)" in SERVER_SRC[i:i + 700]


# ---------------------------------------------------------------- marker

def test_marker():
    assert SERVER_SRC.count("patch100-conn-write") == 2


# ---------------------------------------------------------------------------
# patch100b — plain command interception + verb normalization + clean errors
# ---------------------------------------------------------------------------

def test_plain_command_lines_are_extracted_not_examples():
    import server
    cmds = server._plain_commands("I made the issue.\ncommenting now... connector :: github :: close issue :: me/app/5 :: \n\nDone \u2705")
    assert any("close issue" in x for x in cmds)
    # example-looking lines never fire actions
    assert server._plain_commands('You can say: "certificate :: John :: Course"') == []
    assert server._plain_commands("e.g. connector :: github :: comment :: me/app/1 :: hi") == []


def test_write_action_underscores_normalized():
    import server, json, tests.test_patch100 as _t
    def fake(url, method="GET", headers=None, json_body=None, timeout=25):
        if "/comments" in url and method == "POST":
            return 201, {"id": 9}
        if method == "PATCH" and "/issues/" in url:
            return 200, {"state": "closed"}
        return 404, {}
    server._conn_http, backup = fake, server._conn_http
    try:
        c = {"token": "tk"}
        r = server._connector_do("github", c, "create issue comment danielonakoya19-cloud/oracool/1",
                                 "connector :: github :: create_issue_comment :: danielonakoya19-cloud/oracool/1 :: clean write")
        assert "Comment posted" in r
        r2 = server._connector_do("github", c, "close issue",
                                  "connector :: github :: _close_issue :: danielonakoya19-cloud/oracool/1 :: ")
        assert "closed" in r2
    finally:
        server._conn_http = backup


def test_conn_http_credentials_message_sanitized():
    import server
    def fake_fetch(url, method="GET", headers=None, json_body=None, timeout=25):
        return 401, '{"message": "Bad credentials"}', {}
    backup = server.http_fetch
    server.http_fetch = fake_fetch
    try:
        st, d = server._conn_http("https://api.github.com/user")
    finally:
        server.http_fetch = backup
    assert st == 401
    assert "Bad credentials" not in str(d)
    assert "token rejected" in str(d.get("message", ""))


def test_plain_sweep_helpers_exist_and_strip():
    import server
    assert callable(server._plain_commands)
    assert callable(server._exec_plain_now)
    src = open("index.html").read()
    assert src.count("_stripCmdLines") >= 3


# ---------------------------------------------------------------------------
# patch100c — connector intent without service words + linked-accounts prompt
# ---------------------------------------------------------------------------

def test_connector_intent_without_github_word():
    import server
    # follow-up like "close issue #1 in owner/repo" must reach the connector layer
    calls = []
    def fake_call(email, text):
        calls.append(text)
        return ("done", "GitHub issue #1 closed.")
    backup = server._connector_call
    server._connector_call = fake_call
    try:
        runs = server.auto_tools("close issue #7 in danielonakoya19-cloud/oracool", "free", email="danielonakoya19@gmail.com")
        assert calls and "close issue #7" in calls[0]
        assert any((r or {}).get("tool") == "connector" for r in (runs or []))
        # totally unrelated text must NOT fire
        calls.clear()
        server.auto_tools("tell me a joke about cats", "free", email="danielonakoya19@gmail.com")
        assert not calls
    finally:
        server._connector_call = backup


def test_linked_accounts_block_knows_state():
    import server
    backup = server.connectors_state
    server.connectors_state = lambda e: {"github": {"token": "t", "label": "danielonakoya19-cloud"}}
    try:
        b = server._linked_accounts_block("someone@x.y")
        assert "TOTAL read+write" in b and "GitHub" in b and "never say you can only read" in b
        server.connectors_state = lambda e: {}
        b2 = server._linked_accounts_block("someone@x.y")
        assert "none yet" in b2 and "link it right here in chat" in b2
    finally:
        server.connectors_state = backup


def test_dispatch_falls_back_to_github_on_repo_words():
    import server
    src = open("server.py").read()
    assert "_gh_fallback" in src and "_linked_accounts_block(chat_email)" in src
