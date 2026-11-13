# SPDX-License-Identifier: Apache-2.0
"""A loopback double of the parts of Elasticsearch the managed writer, reader and dispatcher use. No JVM is started:
it is an in-process HTTP server that keeps documents in a dict and can be told to misbehave (drop the connection
mid-reply, fail one bulk item, report failed shards, resolve an alias elsewhere)."""
from __future__ import annotations

import http.server
import json
import threading

from g6.knowledge.ctx_elastic import elastic


class State:
    def __init__(self) -> None:
        self.indices: dict[str, dict] = {}        # concrete index -> {"mappings": ..., "docs": {id: source}}
        self.aliases: dict[str, list[str]] = {}
        self.seen: list[tuple[str, str]] = []
        self.drop_bulk = 0                        # how many bulk requests to apply and then cut before replying
        self.fail_ids: set[str] = set()           # bulk items answered with status 429 and not applied
        self.shards = {"total": 1, "successful": 1, "skipped": 0, "failed": 0}
        self.down = False

    def docs(self, index: str) -> dict:
        return self.indices[index]["docs"]


def _handler(state: State):
    class Fake(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _concrete(self, name):
            if name in state.indices:
                return name
            resolved = state.aliases.get(name, [])
            return resolved[0] if len(resolved) == 1 else None

        def _match(self, source, query):
            clause = query["bool"]
            for one in clause.get("filter", []):
                (kind, pair), = one.items()
                (field, value), = pair.items()
                if (kind == "term" and source.get(field) != value) or (kind == "terms" and source.get(field) not in value):
                    return False
            must = clause["must"][0]
            if "match_all" in must:
                return True
            words = set(must["multi_match"]["query"].lower().split())
            return bool(words & set(str(source.get("text", "")).lower().split()))

        def _answer(self, method):
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            state.seen.append((method, self.path))
            if state.down:
                self.connection.close()
                return
            parts = self.path.strip("/").split("/")
            if parts == [""]:
                return self._send(200, {"version": {"number": "0.0.0-double"}})
            if parts[0] == "_alias":
                found = {i: {"aliases": {parts[1]: {}}} for i in state.aliases.get(parts[1], [])}
                return self._send(200, found) if found else self._send(404, {"error": "alias missing"})
            if method == "PUT" and len(parts) == 1:
                body = json.loads(raw)
                state.indices[parts[0]] = {"mappings": body["mappings"], "docs": {}}
                for alias in body.get("aliases", {}):
                    state.aliases.setdefault(alias, []).append(parts[0])
                return self._send(200, {"acknowledged": True, "index": parts[0]})
            index = self._concrete(parts[0])
            if index is None:
                return self._send(404, {"error": "no such index"})
            op = parts[1] if len(parts) > 1 else ""
            if op == "_mapping":
                return self._send(200, {index: {"mappings": state.indices[index]["mappings"]}})
            if op == "_bulk":
                lines = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
                items, n = [], 0
                while n < len(lines):
                    (verb, head), = lines[n].items()
                    ident = head["_id"]
                    if verb == "delete":
                        existed = state.docs(index).pop(ident, None) is not None
                        items.append({verb: {"_index": index, "_id": ident, "status": 200 if existed else 404}})
                        n += 1
                        continue
                    if ident in state.fail_ids:
                        items.append({verb: {"_index": index, "_id": ident, "status": 429, "error": {"type": "rejected"}}})
                    elif verb == "create" and ident in state.docs(index):
                        items.append({verb: {"_index": index, "_id": ident, "status": 409}})
                    else:
                        state.docs(index)[ident] = lines[n + 1]
                        items.append({verb: {"_index": index, "_id": ident, "status": 201}})
                    n += 2
                if state.drop_bulk:
                    state.drop_bulk -= 1
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 99999\r\n\r\n{\"it")
                    self.wfile.flush()
                    self.connection.close()
                    return
                return self._send(200, {"errors": any(i[next(iter(i))]["status"] >= 300 for i in items), "items": items})
            if op == "_mget":
                ids = json.loads(raw)["ids"]
                return self._send(200, {"docs": [{"_index": index, "_id": i, "found": i in state.docs(index),
                                                  **({"_source": state.docs(index)[i]} if i in state.docs(index) else {})}
                                                 for i in ids]})
            if op == "_count":
                query = json.loads(raw)["query"]
                return self._send(200, {"count": sum(self._match(d, query) for d in state.docs(index).values()),
                                        "_shards": state.shards})
            if op == "_search":
                body = json.loads(raw)
                found = [{"_index": index, "_id": i, "_score": 1.0, "_source": d}
                         for i, d in sorted(state.docs(index).items()) if self._match(d, body["query"])]
                reply = {"_shards": state.shards, "hits": {"total": {"value": len(found)},
                                                           "hits": found[:body.get("size", 10)]}}
                if "aggs" in body:
                    (kind, spec), = body["aggs"]["a"].items()
                    values = [d["_source"].get(spec["field"]) for d in found]
                    reply["aggregations"] = {"a": {"value": len(values)} if kind == "value_count" else {
                        "buckets": [{"key": k, "doc_count": values.count(k)} for k in sorted(set(values))]}}
                return self._send(200, reply)
            return self._send(404, {"error": "no such route in the double"})

        def do_GET(self):
            self._answer("GET")

        def do_POST(self):
            self._answer("POST")

        def do_PUT(self):
            self._answer("PUT")

    return Fake


def serve(monkeypatch) -> tuple[State, callable]:
    """Start the double on a free loopback port, point G6 at it, and return its state and a stop function."""
    state = State()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv(elastic.ELASTICSEARCH.env, f"http://127.0.0.1:{httpd.server_address[1]}")

    def stop():
        httpd.shutdown()
        httpd.server_close()

    return state, stop
