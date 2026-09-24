from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable


_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
_SENSITIVE_QUERY = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|credential)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class EgressRule:
    rule_id: str
    host: str
    path_prefix: str = "/"
    methods: tuple[str, ...] = ("GET", "HEAD")


@dataclass(frozen=True, slots=True)
class EgressDecision:
    allowed: bool
    reason: str
    rule_id: str | None = None


class EgressPolicy:
    """Default-deny URL policy generalized from Vera's agent egress boundary."""

    def __init__(self, rules: Iterable[EgressRule] = ()) -> None:
        self.rules = tuple(rules)
        seen: set[str] = set()
        for rule in self.rules:
            if not rule.rule_id or rule.rule_id in seen:
                raise ValueError("egress_rule_id_invalid")
            seen.add(rule.rule_id)
            if not _HOST.fullmatch(rule.host):
                raise ValueError("egress_host_invalid")
            if not rule.path_prefix.startswith("/"):
                raise ValueError("egress_path_invalid")
            methods = tuple(method.upper() for method in rule.methods)
            if not methods or any(method not in _ALLOWED_METHODS for method in methods):
                raise ValueError("egress_methods_invalid")

    def decide(self, url: str, method: str) -> EgressDecision:
        method = str(method or "").upper()
        if method not in _ALLOWED_METHODS:
            return EgressDecision(False, "egress_method_invalid")

        try:
            parsed = urllib.parse.urlsplit(str(url or ""))
            port = parsed.port
        except ValueError:
            return EgressDecision(False, "egress_url_invalid")

        if parsed.scheme != "https":
            return EgressDecision(False, "egress_https_required")
        if not parsed.hostname or not _HOST.fullmatch(parsed.hostname):
            return EgressDecision(False, "egress_host_invalid")
        if parsed.username or parsed.password or parsed.fragment:
            return EgressDecision(False, "egress_url_credentials_or_fragment_blocked")
        if port not in {None, 443}:
            return EgressDecision(False, "egress_port_blocked")

        decoded_path = urllib.parse.unquote(parsed.path or "/")
        if "\\" in decoded_path or any(
            part in {".", ".."} for part in decoded_path.split("/")
        ):
            return EgressDecision(False, "egress_path_invalid")

        for key, _ in urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=False,
        ):
            if _SENSITIVE_QUERY.search(key):
                return EgressDecision(False, "egress_sensitive_query_blocked")

        host = parsed.hostname.lower()
        path = parsed.path or "/"
        for rule in self.rules:
            prefix = rule.path_prefix
            path_matches = (
                prefix == "/"
                or path == prefix
                or path.startswith(prefix.rstrip("/") + "/")
            )
            if (
                host == rule.host.lower()
                and method in {item.upper() for item in rule.methods}
                and path_matches
            ):
                return EgressDecision(True, "allowed", rule.rule_id)

        return EgressDecision(False, "egress_policy_denied")
