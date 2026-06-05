import os
import tempfile
from phases.symbol_locator import parse_diff, DiffFile, DiffHunk, locate_symbols, ChangedSymbol

MODIFY_DIFF = """\
diff --git a/src/stores/user.js b/src/stores/user.js
index abc..def 100644
--- a/src/stores/user.js
+++ b/src/stores/user.js
@@ -10,5 +10,8 @@ export const useUserStore = defineStore('user', () => {
-  const tradeUserId = ref(null)
+  const tradeUserId = ref('')
+  const tradeUserName = ref('')
"""

NEW_FILE_DIFF = """\
diff --git a/src/utils/format.js b/src/utils/format.js
new file mode 100644
--- /dev/null
+++ b/src/utils/format.js
@@ -0,0 +1,5 @@
+export function formatDate(d) {
+  return d.toISOString()
+}
"""

MULTI_HUNK_DIFF = """\
diff --git a/foo.ts b/foo.ts
index 1..2 100644
--- a/foo.ts
+++ b/foo.ts
@@ -1,3 +1,4 @@
+import x from 'y'
 line
@@ -20,2 +21,3 @@
+newline
 end
"""


def test_parse_diff_extracts_file_path():
    files = parse_diff(MODIFY_DIFF)
    assert len(files) == 1
    assert files[0].path == "src/stores/user.js"


def test_parse_diff_extracts_hunk_start_and_count():
    files = parse_diff(MODIFY_DIFF)
    assert files[0].hunks[0].start_line == 10
    assert files[0].hunks[0].line_count == 8


def test_parse_diff_detects_new_file():
    files = parse_diff(NEW_FILE_DIFF)
    assert files[0].is_new_file is True
    assert files[0].path == "src/utils/format.js"


def test_parse_diff_multiple_hunks():
    files = parse_diff(MULTI_HUNK_DIFF)
    assert len(files[0].hunks) == 2
    assert files[0].hunks[0].start_line == 1
    assert files[0].hunks[1].start_line == 21


def test_parse_diff_deleted_file():
    diff = """\
diff --git a/old.js b/old.js
deleted file mode 100644
--- a/old.js
+++ /dev/null
"""
    files = parse_diff(diff)
    assert files[0].is_deleted is True


def test_parse_diff_single_line_hunk():
    diff = "diff --git a/x.js b/x.js\nindex 1..2\n--- a/x.js\n+++ b/x.js\n@@ -1 +1 @@\n changed\n"
    files = parse_diff(diff)
    assert files[0].hunks[0].line_count == 1


JS_SOURCE = """\
import { ref } from 'vue'

export const useUserStore = defineStore('user', () => {
  const tradeUserId = ref(null)

  function setTradeUserId(id) {
    tradeUserId.value = id
    return id
  }

  async function refreshAccount() {
    const res = await fetch('/api/account')
    tradeUserId.value = res.userId
  }

  return { tradeUserId, setTradeUserId, refreshAccount }
})
"""


def test_locate_symbol_for_modified_line():
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(JS_SOURCE)
        path = f.name
    try:
        # Lines 7–8 are inside setTradeUserId
        symbols = locate_symbols(path, changed_lines=[7, 8])
        assert any(s.symbol == "setTradeUserId" for s in symbols)
    finally:
        os.unlink(path)


def test_locate_symbol_type_is_function():
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(JS_SOURCE)
        path = f.name
    try:
        symbols = locate_symbols(path, changed_lines=[7])
        match = next(s for s in symbols if s.symbol == "setTradeUserId")
        assert match.symbol_type == "function"
    finally:
        os.unlink(path)


def test_locate_top_level_export_function():
    source = "export function formatDate(d) {\n  return d.toISOString()\n}\n"
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        symbols = locate_symbols(path, changed_lines=[2])
        assert any(s.symbol == "formatDate" for s in symbols)
    finally:
        os.unlink(path)


def test_locate_symbols_returns_empty_for_missing_file():
    symbols = locate_symbols("/nonexistent/path.js", changed_lines=[1])
    assert symbols == []
