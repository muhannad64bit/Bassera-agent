"""Tests for the invocation-aware branding helpers in bassera_constants.

The same installed codebase serves both the ``bassera`` and ``bassera`` entry
points; identity strings (CLI prog, version output, doctor banner) follow the
invocation name.
"""

import sys
from unittest.mock import patch

from bassera_constants import agent_display_name, is_bassera_invocation


class TestInvocationBranding:
    def test_bassera_entry_point(self):
        with patch.object(sys, "argv", ["/usr/local/bin/bassera", "--help"]):
            assert is_bassera_invocation() is True
            assert agent_display_name() == "Bassera"

    def test_bassera_agent_entry_point(self):
        with patch.object(sys, "argv", ["./bassera-agent"]):
            assert is_bassera_invocation() is True

    def test_legacy_wafi_entry_point_reports_bassera_identity(self):
        """The legacy wafi alias still runs, but reports Bassera."""
        with patch.object(sys, "argv", ["/usr/local/bin/wafi", "--help"]):
            assert is_bassera_invocation() is False
            assert agent_display_name() == "Bassera"

    def test_similar_prefix_other_tool_not_bassera(self):
        """A different tool with a legacy-brand name is not a bassera entry point."""
        with patch.object(sys, "argv", ["/usr/local/bin/waficlaw"]):
            assert is_bassera_invocation() is False

    def test_empty_argv_is_safe(self):
        with patch.object(sys, "argv", []):
            assert is_bassera_invocation() is False
            assert agent_display_name() == "Bassera"
