"""The six provenance shapes, run on the online monitor."""

from __future__ import annotations

import asyncio

from agentrec.graph_monitor import GRAPH_POLICIES, evaluate_monitor_graphs


def _row(rows, template, fault, policy):
    return next(row for row in rows if row.template == template and row.fault == fault and row.policy == policy)


def test_monitor_graphs_keep_steps_that_never_read_the_fault():
    rows = asyncio.run(evaluate_monitor_graphs())
    assert len(rows) == 160
    assert all(row.success for row in rows)

    for template, fault in {(row.template, row.fault) for row in rows}:
        online = _row(rows, template, fault, "online")
        support = _row(rows, template, fault, "support")
        coarse = _row(rows, template, fault, "coarse")
        suffix = _row(rows, template, fault, "suffix")
        restart = _row(rows, template, fault, "restart")
        assert online.preserved == coarse.preserved == support.preserved
        assert set(suffix.preserved) <= set(coarse.preserved)
        assert online.calls <= coarse.calls <= suffix.calls
        assert restart.preserved == ()
        assert restart.calls >= suffix.calls

    # Chain is a line, so the order and the edges keep the same steps.
    for fault in ("a", "b", "c", "d"):
        assert _row(rows, "chain", fault, "suffix").preserved == _row(rows, "chain", fault, "coarse").preserved

    # Diamond: c is later than b and never reads b.
    assert "c" in _row(rows, "diamond", "b", "online").preserved
    assert "c" not in _row(rows, "diamond", "b", "suffix").preserved
    assert _row(rows, "diamond", "b", "suffix").calls > _row(rows, "diamond", "b", "coarse").calls

    # Join: q and r never read p, but both follow p in the plan.
    assert _row(rows, "join", "p", "coarse").preserved == ("q", "r")
    assert _row(rows, "join", "p", "suffix").preserved == ()
    assert "r" in _row(rows, "join", "q", "coarse").preserved
    assert "r" not in _row(rows, "join", "q", "suffix").preserved

    # Back-edge: side is later than the return and reads only the first visit.
    assert "side" in _row(rows, "backedge", "b", "coarse").preserved
    assert "side" not in _row(rows, "backedge", "b", "suffix").preserved
    assert "side" in _row(rows, "backedge", "a:2", "coarse").preserved
    assert "side" not in _row(rows, "backedge", "a:2", "suffix").preserved

    # Collaborative: a:2 reads b and c, so a fault at a misses that turn.
    assert "a:2" in _row(rows, "collaborative", "a", "coarse").preserved
    assert "a:2" not in _row(rows, "collaborative", "a", "suffix").preserved

    # Hierarchical: c and b:2 never read b. The assignment stays upstream.
    assert _row(rows, "hierarchical", "b", "coarse").preserved == ("a", "b:2", "c")
    for peer in ("b:2", "c"):
        assert peer not in _row(rows, "hierarchical", "b", "suffix").preserved
    assert "a" in _row(rows, "hierarchical", "b", "suffix").preserved
    assert set(GRAPH_POLICIES) == {"online", "support", "coarse", "suffix", "restart"}
