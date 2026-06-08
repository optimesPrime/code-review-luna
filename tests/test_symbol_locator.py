import os
import tempfile
from phases.symbol_locator import parse_diff, DiffFile, DiffHunk, locate_symbols, ChangedSymbol, extract_changed_symbols_from_diff

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


def test_extract_finds_modified_function_body():
    """
    Key regression test: a diff that only MODIFIES lines *inside* an existing
    function must still surface that function — the current grep-only approach
    would miss this entirely.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "stores", "user.js")
        os.makedirs(os.path.dirname(src_path), exist_ok=True)
        with open(src_path, "w") as f:
            f.write(JS_SOURCE)

        rel = "stores/user.js"
        diff = f"""\
diff --git a/{rel} b/{rel}
index 1..2 100644
--- a/{rel}
+++ b/{rel}
@@ -7,2 +7,3 @@ export const useUserStore = defineStore('user', () => {{
-  tradeUserId.value = id
+  tradeUserId.value = String(id)
+  sessionStorage.setItem('tradeId', String(id))
"""
        symbols = extract_changed_symbols_from_diff(diff, project_root=tmp)
        names = [s.symbol for s in symbols]
        assert "setTradeUserId" in names, f"Expected setTradeUserId, got: {names}"


def test_extract_marks_new_file_symbols_as_added():
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "utils", "format.js")
        os.makedirs(os.path.dirname(src_path), exist_ok=True)
        with open(src_path, "w") as f:
            f.write("export function formatDate(d) {\n  return d.toISOString()\n}\n")

        diff = """\
diff --git a/utils/format.js b/utils/format.js
new file mode 100644
--- /dev/null
+++ b/utils/format.js
@@ -0,0 +1,3 @@
+export function formatDate(d) {
+  return d.toISOString()
+}
"""
        symbols = extract_changed_symbols_from_diff(diff, project_root=tmp)
        match = next((s for s in symbols if s.symbol == "formatDate"), None)
        assert match is not None
        assert match.change_type == "added"


def test_extract_returns_empty_when_file_not_on_disk():
    diff = """\
diff --git a/ghost.js b/ghost.js
index 1..2 100644
--- a/ghost.js
+++ b/ghost.js
@@ -1,1 +1,2 @@
+const x = 1
"""
    symbols = extract_changed_symbols_from_diff(diff, project_root="/nonexistent")
    assert symbols == []


# ── tree-sitter AST tests ─────────────────────────────────────────────────────
from pathlib import Path as _Path


def _write_file(path: _Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


REACT_SRC = """\
import React, { useState, useEffect } from 'react';
import axios from 'axios';

export function OrderList({ userId }) {
  const [orders, setOrders] = useState([]);
  useEffect(() => {
    axios.get('/api/orders/' + userId).then(r => setOrders(r.data));
  }, [userId]);
  return orders;
}

export const OrderCard = ({ order }) => (
  <div>{order.name}</div>
);

export function useOrderData(id) {
  const [data, setData] = useState(null);
  return data;
}

const useAuth = () => {
  return { user: null };
};

export const useOrderStore = defineStore('orders', () => {
  const items = [];
  return { items };
});
"""

VUE_SFC = """\
<template>
  <div>{{ count }}</div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
const count = ref(0);

function increment() {
  count.value++;
}

const useCounter = () => {
  return count;
};
</script>
"""


def test_locate_symbols_react_function_component(tmp_path):
    f = tmp_path / "orders.tsx"
    _write_file(f, REACT_SRC)
    from phases.symbol_locator import locate_symbols
    # Line 5 is inside OrderList body
    symbols = locate_symbols(str(f), [5])
    assert len(symbols) == 1
    assert symbols[0].symbol == "OrderList"
    assert symbols[0].symbol_type == "component"


def test_locate_symbols_react_hook(tmp_path):
    f = tmp_path / "orders.tsx"
    _write_file(f, REACT_SRC)
    from phases.symbol_locator import locate_symbols
    # Line 17 is inside useOrderData body
    symbols = locate_symbols(str(f), [17])
    assert len(symbols) == 1
    assert symbols[0].symbol == "useOrderData"
    assert symbols[0].symbol_type == "hook"


def test_locate_symbols_store(tmp_path):
    f = tmp_path / "orders.tsx"
    _write_file(f, REACT_SRC)
    from phases.symbol_locator import locate_symbols
    # Line 26 is inside useOrderStore body
    symbols = locate_symbols(str(f), [26])
    assert len(symbols) == 1
    assert symbols[0].symbol == "useOrderStore"
    assert symbols[0].symbol_type == "store"


def test_locate_symbols_vue_sfc_function(tmp_path):
    f = tmp_path / "Counter.vue"
    _write_file(f, VUE_SFC)
    from phases.symbol_locator import locate_symbols
    # Line 10 is inside increment() in <script setup>
    symbols = locate_symbols(str(f), [10])
    assert len(symbols) == 1
    assert symbols[0].symbol == "increment"
    assert symbols[0].symbol_type == "function"


def test_locate_symbols_vue_sfc_composable(tmp_path):
    f = tmp_path / "Counter.vue"
    _write_file(f, VUE_SFC)
    from phases.symbol_locator import locate_symbols
    # Line 14 is inside useCounter arrow function
    symbols = locate_symbols(str(f), [14])
    assert len(symbols) == 1
    assert symbols[0].symbol == "useCounter"
    assert symbols[0].symbol_type == "hook"
